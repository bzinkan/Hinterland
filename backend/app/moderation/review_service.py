"""Shared, transaction-scoped review resolution behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.derived_state import enqueue_rebuild
from app.derived_state.rebuild import acquire_user_lock, try_acquire_user_lock


class ReviewResolutionConflict(Exception):
    """The review disappeared or another resolver won the race."""


@dataclass(frozen=True)
class LinkedReviewSubject:
    """The one child/photo/observation tuple authorized by a review row."""

    review: models.ReviewQueueItem
    photo: models.Photo
    observation: models.Observation

    @property
    def user_id(self) -> str:
        return self.photo.user_id


def _validate_linked_subject(
    review: models.ReviewQueueItem,
    photo: models.Photo | None,
    observation: models.Observation | None,
) -> None:
    """Reject every partial or cross-resource review linkage."""
    if photo is None or observation is None:
        raise ReviewResolutionConflict("Review subject no longer exists")
    if review.photo_id != photo.id:
        raise ReviewResolutionConflict("Review subject linkage is inconsistent")
    if review.observation_id is not None and review.observation_id != observation.id:
        raise ReviewResolutionConflict("Review subject linkage is inconsistent")
    if observation.photo_id != photo.id:
        raise ReviewResolutionConflict("Review subject linkage is inconsistent")
    if observation.user_id != photo.user_id:
        raise ReviewResolutionConflict("Review subject linkage is inconsistent")
    if observation.group_id != review.group_id:
        raise ReviewResolutionConflict("Review subject linkage is inconsistent")


async def _read_linked_subject(
    session: AsyncSession,
    review: models.ReviewQueueItem,
    *,
    lock: bool,
    nonblocking: bool = False,
) -> LinkedReviewSubject:
    """Load and validate the subject, safely repairing a legacy null link."""
    photo_statement = select(models.Photo).where(models.Photo.id == review.photo_id)
    if lock:
        photo_statement = photo_statement.with_for_update(skip_locked=nonblocking)
    photo = (await session.execute(photo_statement)).scalar_one_or_none()

    observation_statement = select(models.Observation)
    if review.observation_id is None:
        observation_statement = observation_statement.where(
            models.Observation.photo_id == review.photo_id
        )
    else:
        observation_statement = observation_statement.where(
            models.Observation.id == review.observation_id
        )
    if lock:
        observation_statement = observation_statement.with_for_update(skip_locked=nonblocking)
    observation = (await session.execute(observation_statement)).scalar_one_or_none()

    _validate_linked_subject(review, photo, observation)
    assert photo is not None
    assert observation is not None
    if review.observation_id is None and lock:
        # The caller holds the review lock, and the unique photo relation
        # proves this is the only legacy observation the review can name.
        review.observation_id = observation.id
    return LinkedReviewSubject(review=review, photo=photo, observation=observation)


async def _subject_user_id(
    session: AsyncSession,
    review: models.ReviewQueueItem,
) -> str:
    """Resolve the affected user only from a coherent unlocked tuple."""
    return (await _read_linked_subject(session, review, lock=False)).user_id


async def lock_linked_review_subject(
    session: AsyncSession,
    *,
    review: models.ReviewQueueItem,
    expected_status: str,
    nonblocking: bool = False,
    canonical_parent_user_id: str | None = None,
) -> LinkedReviewSubject:
    """Take advisory and row locks, then recheck every subject relationship."""
    subject_user_id = await _subject_user_id(session, review)
    if nonblocking:
        if not await try_acquire_user_lock(session, subject_user_id):
            raise ReviewResolutionConflict("Review subject is already being resolved")
    else:
        await acquire_user_lock(session, subject_user_id)

    statement = (
        select(models.ReviewQueueItem)
        .where(models.ReviewQueueItem.id == review.id)
        .execution_options(populate_existing=True)
        .with_for_update(skip_locked=nonblocking)
    )
    locked_review = (await session.execute(statement)).scalar_one_or_none()
    if locked_review is None:
        raise ReviewResolutionConflict("Review item no longer exists")
    if locked_review.status != expected_status:
        raise ReviewResolutionConflict(f"Review item is already {locked_review.status}")
    subject = await _read_linked_subject(
        session,
        locked_review,
        lock=True,
        nonblocking=nonblocking,
    )
    if subject.user_id != subject_user_id:
        # The unlocked candidate selected the advisory-lock key. If the
        # review tuple changed to another child before its rows were locked,
        # we do not hold that other child's lock and must not mutate it.
        raise ReviewResolutionConflict("Review subject changed while being resolved")
    if canonical_parent_user_id is not None:
        managed_child_id = (
            await session.execute(
                select(models.User.id).where(
                    models.User.id == subject.user_id,
                    models.User.role == "kid",
                    models.User.parent_user_id == canonical_parent_user_id,
                )
            )
        ).scalar_one_or_none()
        if managed_child_id is None:
            raise ReviewResolutionConflict("Review subject is not managed by this parent")
    return subject


async def reject_review_item(
    session: AsyncSession,
    *,
    review: models.ReviewQueueItem,
    reviewer_user_id: str | None,
    nonblocking: bool = False,
    canonical_parent_user_id: str | None = None,
) -> models.DerivedStateRebuild | None:
    """Tombstone one pending review and queue deterministic compensation.

    The caller passes an authorized but unlocked review and commits. This
    service acquires the per-user advisory lock before every row lock, matching
    rebuild ordering. No counters are adjusted piecemeal.
    """
    subject = await lock_linked_review_subject(
        session,
        review=review,
        expected_status="pending",
        nonblocking=nonblocking,
        canonical_parent_user_id=canonical_parent_user_id,
    )
    review = subject.review

    now = datetime.now(UTC)
    photo = subject.photo
    photo.status = "deleted"
    photo.attachment_status = "deleted"
    photo.moderated_at = now

    observation = subject.observation
    observation.moderation_status = "rejected"
    observation.moderation_source = "adult"
    observation.moderation_policy_version = "adult-review-v1"
    observation.rejected_at = now
    rebuild = await enqueue_rebuild(
        session,
        user_id=observation.user_id,
        trigger_observation_id=observation.id,
    )

    review.status = "rejected"
    review.reviewer_user_id = reviewer_user_id
    review.resolved_at = now
    return rebuild


async def revoke_approved_review_item(
    session: AsyncSession,
    *,
    review: models.ReviewQueueItem,
    nonblocking: bool = False,
    canonical_parent_user_id: str | None = None,
) -> models.DerivedStateRebuild | None:
    """Tombstone an approved clean item without rewriting its approval audit.

    The durable ``PhotoRevocation`` owns the revoking actor.  The review keeps
    its original ``reviewer_user_id`` and ``resolved_at`` and moves only from
    ``approved`` to the distinct terminal ``revoked`` state.
    """
    subject = await lock_linked_review_subject(
        session,
        review=review,
        expected_status="approved",
        nonblocking=nonblocking,
        canonical_parent_user_id=canonical_parent_user_id,
    )
    locked_review = subject.review

    now = datetime.now(UTC)
    photo = subject.photo
    if photo.status != "clean" or photo.attachment_status != "attached":
        raise ReviewResolutionConflict("Approved review no longer has an attached clean photo")
    photo.status = "deleted"
    photo.attachment_status = "deleted"
    photo.moderated_at = now

    observation = subject.observation
    if observation.moderation_status != "clean" or observation.rejected_at is not None:
        raise ReviewResolutionConflict("Approved review no longer has a clean observation")
    observation.moderation_status = "rejected"
    observation.moderation_source = "adult"
    observation.moderation_policy_version = "adult-revocation-v1"
    observation.rejected_at = now
    rebuild = await enqueue_rebuild(
        session,
        user_id=observation.user_id,
        trigger_observation_id=observation.id,
    )

    locked_review.status = "revoked"
    return rebuild
