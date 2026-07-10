"""Shared, transaction-scoped review resolution behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.derived_state import enqueue_rebuild
from app.derived_state.rebuild import acquire_user_lock, try_acquire_user_lock


class ReviewResolutionConflict(Exception):
    """The review disappeared or another resolver won the race."""


async def _subject_user_id(
    session: AsyncSession,
    review: models.ReviewQueueItem,
) -> str:
    """Resolve the affected user without taking any row lock."""
    if review.observation_id is not None:
        user_id = (
            await session.execute(
                select(models.Observation.user_id).where(
                    models.Observation.id == review.observation_id
                )
            )
        ).scalar_one_or_none()
        if user_id is not None:
            return user_id
    user_id = (
        await session.execute(
            select(models.Photo.user_id).where(models.Photo.id == review.photo_id)
        )
    ).scalar_one_or_none()
    if user_id is None:
        raise ReviewResolutionConflict("Review subject no longer exists")
    return user_id


async def reject_review_item(
    session: AsyncSession,
    *,
    review: models.ReviewQueueItem,
    reviewer_user_id: str | None,
    nonblocking: bool = False,
) -> models.DerivedStateRebuild | None:
    """Tombstone one pending review and queue deterministic compensation.

    The caller passes an authorized but unlocked review and commits. This
    service acquires the per-user advisory lock before every row lock, matching
    rebuild ordering. No counters are adjusted piecemeal.
    """
    subject_user_id = await _subject_user_id(session, review)
    if nonblocking:
        if not await try_acquire_user_lock(session, subject_user_id):
            raise ReviewResolutionConflict("Review subject is already being resolved")
    else:
        await acquire_user_lock(session, subject_user_id)
    # The authorization/candidate read placed this identity in the session.
    # Force the locked SELECT to overwrite that cached snapshot so a resolver
    # that waited behind the winner observes its committed terminal status.
    review_statement = (
        select(models.ReviewQueueItem)
        .where(models.ReviewQueueItem.id == review.id)
        .execution_options(populate_existing=True)
    )
    if nonblocking:
        review_statement = review_statement.with_for_update(skip_locked=True)
    else:
        review_statement = review_statement.with_for_update()
    locked_review = (await session.execute(review_statement)).scalar_one_or_none()
    if locked_review is None:
        raise ReviewResolutionConflict("Review item no longer exists")
    if locked_review.status != "pending":
        raise ReviewResolutionConflict(f"Review item is already {locked_review.status}")
    review = locked_review

    now = datetime.now(UTC)
    photo = (
        await session.execute(
            select(models.Photo).where(models.Photo.id == review.photo_id).with_for_update()
        )
    ).scalar_one_or_none()
    if photo is not None:
        photo.status = "deleted"
        photo.attachment_status = "deleted"
        photo.moderated_at = now

    rebuild: models.DerivedStateRebuild | None = None
    if review.observation_id is not None:
        observation = (
            await session.execute(
                select(models.Observation)
                .where(models.Observation.id == review.observation_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if observation is not None:
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
