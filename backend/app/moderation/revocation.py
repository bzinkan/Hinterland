"""Durable, fail-closed photo revocation for review rejection.

The privacy boundary is intentionally split into two commits:

1. persist a ``photo_revocations`` row so every new signed-URL request is
   denied;
2. move the canonical bytes to a restricted held prefix, verify them, delete
   the readable source, then commit the authoritative rejection and rebuild.

If the process dies between those commits, the active revocation still denies
new URLs and a retry can prove the already-copied destination before finishing
the tombstone.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import SignedUrlGenerator, StorageCopyVerificationError
from app.db import models
from app.moderation.review_service import (
    ReviewResolutionConflict,
    lock_linked_review_subject,
    reject_review_item,
    revoke_approved_review_item,
)

log = structlog.get_logger()

MAX_REVOCATION_ATTEMPTS = 5
_CANONICAL_PARENT_SOURCES = frozenset({"adult_review", "adult_revocation", "parent_delete"})


class PhotoRevocationError(RuntimeError):
    """Base class for fail-closed revocation failures."""


class PhotoRevocationPending(PhotoRevocationError):
    """Storage or database work failed but the durable deny record remains."""

    def __init__(self, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


@dataclass(frozen=True)
class ClaimedRevocation:
    photo_id: str
    review_id: str
    bucket: str
    source_object_name: str
    held_object_name: str
    expected_byte_count: int
    expected_sha256: str
    attempt_count: int
    claim_review_status: str = "pending"
    requesting_actor_user_id: str | None = None
    canonical_parent_user_id: str | None = None


async def _claim_revocation(
    session: AsyncSession,
    *,
    review: models.ReviewQueueItem,
    source: str,
    claim_review_status: str,
    requesting_actor_user_id: str | None,
    nonblocking: bool,
) -> ClaimedRevocation:
    """Persist the signed-URL deny gate before any storage mutation."""
    if claim_review_status not in {"pending", "approved"}:
        raise ValueError("claim_review_status must be pending or approved")
    canonical_parent_user_id = (
        requesting_actor_user_id if source in _CANONICAL_PARENT_SOURCES else None
    )
    subject = await lock_linked_review_subject(
        session,
        review=review,
        expected_status=claim_review_status,
        nonblocking=nonblocking,
        canonical_parent_user_id=canonical_parent_user_id,
    )
    locked_review = subject.review
    photo = subject.photo
    if claim_review_status == "approved":
        if photo.status != "clean" or photo.attachment_status != "attached":
            raise ReviewResolutionConflict("Approved review no longer has an attached clean photo")
        if (
            subject.observation.moderation_status != "clean"
            or subject.observation.rejected_at is not None
        ):
            raise ReviewResolutionConflict("Approved review no longer has a clean observation")

    held_object_name = f"rejected/held/{photo.id}.jpg"
    revocation = (
        await session.execute(
            select(models.PhotoRevocation)
            .where(models.PhotoRevocation.photo_id == photo.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revocation is None:
        revocation = models.PhotoRevocation(
            photo_id=photo.id,
            review_id=locked_review.id,
            claim_review_status=claim_review_status,
            requesting_actor_user_id=requesting_actor_user_id,
            source=source,
            bucket=photo.bucket,
            source_object_name=photo.object_name,
            held_object_name=held_object_name,
            expected_byte_count=photo.byte_count,
            expected_sha256=photo.sha256,
            state="pending",
            attempt_count=0,
        )
        session.add(revocation)
    else:
        if revocation.review_id != locked_review.id:
            raise ReviewResolutionConflict("Photo is already held by another review")
        if revocation.claim_review_status != claim_review_status:
            raise ReviewResolutionConflict("Photo revocation claim type changed")
        if (
            revocation.requesting_actor_user_id is not None
            and requesting_actor_user_id is not None
            and revocation.requesting_actor_user_id != requesting_actor_user_id
        ):
            raise ReviewResolutionConflict("Photo revocation belongs to another actor")
        if revocation.state == "succeeded":
            raise ReviewResolutionConflict("Photo revocation already succeeded")
        if revocation.attempt_count >= MAX_REVOCATION_ATTEMPTS:
            raise PhotoRevocationPending(
                "Photo revocation exhausted its retry budget",
                terminal=True,
            )

    # New canonical photos always carry both values. A malformed legacy row
    # still gets the durable deny record before this terminal failure is
    # surfaced; guessing bytes would weaken the relocation guarantee.
    if revocation.expected_byte_count is None or revocation.expected_sha256 is None:
        revocation.state = "failed"
        revocation.attempt_count = MAX_REVOCATION_ATTEMPTS
        revocation.last_attempt_at = datetime.now(UTC)
        revocation.last_error = "Photo is missing verified length or SHA-256 metadata"
        await session.flush()
        await session.commit()
        raise PhotoRevocationPending(
            "Photo is missing verified length or SHA-256 metadata",
            terminal=True,
        )

    revocation.state = "copying"
    revocation.attempt_count += 1
    revocation.last_attempt_at = datetime.now(UTC)
    revocation.last_error = None
    await session.flush()
    claimed = ClaimedRevocation(
        photo_id=photo.id,
        review_id=locked_review.id,
        bucket=revocation.bucket,
        source_object_name=revocation.source_object_name,
        held_object_name=revocation.held_object_name,
        expected_byte_count=revocation.expected_byte_count,
        expected_sha256=revocation.expected_sha256,
        attempt_count=revocation.attempt_count,
        claim_review_status=revocation.claim_review_status,
        requesting_actor_user_id=revocation.requesting_actor_user_id,
        canonical_parent_user_id=canonical_parent_user_id,
    )
    # This commit makes URL denial visible before a possibly slow Blob copy.
    await session.commit()
    return claimed


def _verify_held_destination(
    storage: SignedUrlGenerator,
    claim: ClaimedRevocation,
) -> None:
    properties = storage.get_object_properties(
        bucket=claim.bucket,
        object_name=claim.held_object_name,
    )
    if properties.byte_count != claim.expected_byte_count:
        raise StorageCopyVerificationError("held destination length verification failed")
    held_bytes = storage.fetch_object_bytes(
        bucket=claim.bucket,
        object_name=claim.held_object_name,
    )
    if hashlib.sha256(held_bytes).hexdigest() != claim.expected_sha256:
        raise StorageCopyVerificationError("held destination SHA-256 verification failed")


def relocate_photo_to_held(
    storage: SignedUrlGenerator,
    claim: ClaimedRevocation,
) -> None:
    """Idempotently relocate verified bytes and prove the source is absent."""
    try:
        source_properties = storage.get_object_properties(
            bucket=claim.bucket,
            object_name=claim.source_object_name,
        )
    except FileNotFoundError:
        # A prior attempt may have copied and deleted successfully before its
        # final database commit. The held bytes are the recovery authority.
        _verify_held_destination(storage, claim)
        return

    if source_properties.byte_count != claim.expected_byte_count:
        raise StorageCopyVerificationError("revocation source length verification failed")

    storage.copy_object(
        src_bucket=claim.bucket,
        src_object=claim.source_object_name,
        dst_bucket=claim.bucket,
        dst_object=claim.held_object_name,
        expected_size=claim.expected_byte_count,
        expected_sha256=claim.expected_sha256,
    )
    _verify_held_destination(storage, claim)
    storage.delete_object(bucket=claim.bucket, object_name=claim.source_object_name)
    try:
        storage.get_object_properties(
            bucket=claim.bucket,
            object_name=claim.source_object_name,
        )
    except FileNotFoundError:
        return
    raise StorageCopyVerificationError("revocation source still exists after delete")


async def _record_failure(
    session: AsyncSession,
    *,
    claim: ClaimedRevocation,
    exc: Exception,
) -> bool:
    """Record a retryable/terminal failure; return whether it is terminal."""
    await session.rollback()
    revocation = (
        await session.execute(
            select(models.PhotoRevocation)
            .where(models.PhotoRevocation.photo_id == claim.photo_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if revocation is None:
        # The claim commit succeeded before storage work, so disappearance is
        # a state mismatch that must never be treated as safe completion.
        raise PhotoRevocationPending("Durable photo revocation record disappeared") from exc
    terminal = revocation.attempt_count >= MAX_REVOCATION_ATTEMPTS
    revocation.state = "failed" if terminal else "pending"
    revocation.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
    await session.commit()
    log.error(
        "photo_revocation.terminal_failure" if terminal else "photo_revocation.failed",
        photo_id=claim.photo_id,
        review_id=claim.review_id,
        attempt_count=revocation.attempt_count,
        terminal=terminal,
        error_type=type(exc).__name__,
    )
    return terminal


async def revoke_and_reject_review_item(
    session: AsyncSession,
    *,
    storage: SignedUrlGenerator,
    review: models.ReviewQueueItem,
    reviewer_user_id: str | None,
    source: str,
    claim_review_status: str = "pending",
    nonblocking: bool = False,
) -> models.DerivedStateRebuild | None:
    """Deny, relocate, tombstone, and queue compensation as one service."""
    claim = await _claim_revocation(
        session,
        review=review,
        source=source,
        claim_review_status=claim_review_status,
        requesting_actor_user_id=reviewer_user_id,
        nonblocking=nonblocking,
    )
    try:
        await asyncio.to_thread(relocate_photo_to_held, storage, claim)
    except Exception as exc:
        terminal = await _record_failure(session, claim=claim, exc=exc)
        raise PhotoRevocationPending(
            "Photo remains private while revocation is retried",
            terminal=terminal,
        ) from exc

    try:
        # Re-load after the claim commit so the finalizer observes any race.
        reloaded_review = (
            await session.execute(
                select(models.ReviewQueueItem).where(models.ReviewQueueItem.id == claim.review_id)
            )
        ).scalar_one_or_none()
        if reloaded_review is None:
            raise ReviewResolutionConflict("Review item no longer exists")
        if claim.claim_review_status == "approved":
            rebuild = await revoke_approved_review_item(
                session,
                review=reloaded_review,
                nonblocking=nonblocking,
                canonical_parent_user_id=claim.canonical_parent_user_id,
            )
        else:
            rebuild = await reject_review_item(
                session,
                review=reloaded_review,
                reviewer_user_id=reviewer_user_id,
                nonblocking=nonblocking,
                canonical_parent_user_id=claim.canonical_parent_user_id,
            )
        photo = (
            await session.execute(
                select(models.Photo).where(models.Photo.id == claim.photo_id).with_for_update()
            )
        ).scalar_one_or_none()
        revocation = (
            await session.execute(
                select(models.PhotoRevocation)
                .where(models.PhotoRevocation.photo_id == claim.photo_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if photo is None or revocation is None:
            raise PhotoRevocationPending("Revocation finalization state is incomplete")
        photo.object_name = claim.held_object_name
        photo.canonical_object_name = claim.held_object_name
        revocation.state = "succeeded"
        revocation.completed_at = datetime.now(UTC)
        revocation.last_error = None
        await session.commit()
    except Exception as exc:
        # The clean source is already absent. Roll back partial DB state and
        # leave `copying` durable so a retry verifies the held bytes and safely
        # repeats finalization.
        await session.rollback()
        log.exception(
            "photo_revocation.finalize_failed",
            photo_id=claim.photo_id,
            review_id=claim.review_id,
        )
        raise PhotoRevocationPending(
            "Photo is private and rejection finalization will retry"
        ) from exc

    log.info(
        "photo_revocation.succeeded",
        photo_id=claim.photo_id,
        review_id=claim.review_id,
        attempt_count=claim.attempt_count,
    )
    return rebuild
