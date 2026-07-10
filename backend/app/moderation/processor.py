"""Moderate an attached private photo and update its durable lifecycle.

Only a committed observation/photo pair is eligible. A raw BlobCreated event
is insufficient authority to call a provider. Completed work is idempotent.

Clean-path side-effects (Risk 0002 transactional outbox):

- On a clean decision, the matching `observations` row (when one
  exists) is updated with `moderation_status='clean'` and
  `moderation_labels` in the SAME SQLAlchemy transaction that moves
  the photo. A `pending` `inat_submit_outbox` row is inserted in the
  same transaction, so an iNat submission is guaranteed to be tried
  at-least-once via the Service Bus enqueue below + the 15-min replay
  job.
- After commit, the processor attempts to enqueue the observation
  to Service Bus. On success the outbox row is flipped to `enqueued`
  in a second short transaction. On failure -- including the
  expected "Service Bus not provisioned yet" case during the
  rollout window -- the row stays `pending` and the replay job
  picks it up later.
- Flagged decisions set `moderation_status='quarantine'` and skip
  the outbox write entirely; the row is only re-eligible after a
  manual review approval (see `app/api/routes/review_queue.py`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.config import Settings
from app.core.storage import SignedUrlGenerator
from app.db import models
from app.inat.enqueue import enqueue_inat_submit
from app.moderation.provider import Moderator
from app.observation.photo_finalize import PhotoValidationError, validate_canonical_jpeg

log = structlog.get_logger()

_PENDING_PREFIX = "pending/"
_PILOT_PRIVATE_PREFIX = "pilot-private/"
_OBSERVATIONS_PREFIX = "observations/"
_QUARANTINE_PREFIX = "quarantine/"


class PhotoNotFound(Exception):
    """The committed work references a photo row that is not readable yet."""


class ModerationWorkInvalid(Exception):
    """The work item is not backed by the committed observation/photo pair."""


@dataclass(frozen=True)
class ProcessResult:
    photo_id: str
    decision: Literal["clean", "flagged", "pilot_private", "skipped"]
    new_object_name: str | None
    review_queue_id: str | None
    # The observation that was flipped to `clean` or `quarantine` (if a
    # matching row was found). None when no observation references the
    # photo yet, or when the call was a no-op. Callers can use this to
    # log or to drive follow-up logic (today: the processor already
    # enqueues the iNat submit internally).
    observation_id: str | None = None
    # `enqueued` -> the iNat submit was sent to Service Bus successfully
    # and the outbox row is now `enqueued`. `pending` -> the outbox row
    # exists but the enqueue attempt failed (or was skipped because
    # Service Bus isn't provisioned yet); the 15-min replay job will
    # retry. None when no outbox row was written (flagged decision,
    # observation missing, etc.).
    outbox_status: Literal["enqueued", "pending"] | None = None


def _photo_id_from_object_name(object_name: str) -> str | None:
    """Extract `<photo_id>` from `pending/<photo_id>.jpg`. Tolerant of
    other suffixes; returns None if the shape doesn't match."""
    if not object_name.startswith(_PENDING_PREFIX):
        return None
    rest = object_name[len(_PENDING_PREFIX) :].rsplit("/", 1)[-1]
    if "." in rest:
        rest = rest.rsplit(".", 1)[0]
    if not rest:
        return None
    return rest


def _is_terminal_moderation_state(
    photo: models.Photo,
    observation: models.Observation,
) -> bool:
    """Return whether photo/observation state proves moderation completed."""
    return (
        (photo.status == "clean" and observation.moderation_status == "clean")
        or (photo.status == "quarantine" and observation.moderation_status == "quarantine")
        or (photo.status == "pending" and observation.moderation_status == "pilot_private")
        or (photo.status == "deleted" and observation.moderation_status == "rejected")
    )


def _complete_moderation_outbox(outbox: models.ModerationOutbox) -> None:
    outbox.status = "succeeded"
    outbox.lease_until = None
    outbox.last_error = None


async def process_pending_photo(
    session: AsyncSession,
    storage: SignedUrlGenerator,
    moderator: Moderator,
    *,
    bucket: str,
    object_name: str,
    settings: Settings | None = None,
    expected_photo_id: str | None = None,
    expected_observation_id: str | None = None,
) -> ProcessResult:
    """Run one moderation cycle on a committed Blob object.

    When `settings` is provided AND the moderation decision is `clean`,
    the processor also writes an `inat_submit_outbox` row in the same
    transaction and attempts to enqueue the observation to Service
    Bus after commit. When `settings` is None, the outbox row is still
    written but no enqueue is attempted -- the replay job will pick it
    up. Tests that don't care about the outbox path can pass
    `settings=None`.
    """
    photo_id = expected_photo_id or _photo_id_from_object_name(object_name)
    if photo_id is None:
        log.info("moderation.processor.skipped_non_pending", object_name=object_name)
        return ProcessResult(
            photo_id="",
            decision="skipped",
            new_object_name=None,
            review_queue_id=None,
        )

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == photo_id))
    ).scalar_one_or_none()
    if photo is None:
        # Eventarc fired before the presign transaction committed. Raise
        # so the trigger retries with backoff.
        log.warning("moderation.processor.photo_row_missing", photo_id=photo_id)
        raise PhotoNotFound(photo_id)

    if expected_photo_id is not None and photo.id != expected_photo_id:
        raise ModerationWorkInvalid("photo id did not match committed work")

    observation_query = select(models.Observation).where(models.Observation.photo_id == photo_id)
    if expected_observation_id is not None:
        observation_query = observation_query.where(
            models.Observation.id == expected_observation_id
        )
    observation = (await session.execute(observation_query)).scalar_one_or_none()
    if observation is None:
        raise ModerationWorkInvalid("photo is not attached to the committed observation")

    outbox = (
        await session.execute(
            select(models.ModerationOutbox).where(
                models.ModerationOutbox.observation_id == observation.id,
                models.ModerationOutbox.photo_id == photo.id,
            )
        )
    ).scalar_one_or_none()
    if outbox is None:
        raise ModerationWorkInvalid("moderation requires committed outbox authority")

    # A prior attempt may have committed the verified destination and terminal
    # observation state just before its process crashed. Complete the matching
    # outbox idempotently *before* validating the message's old source path.
    # This also repairs rows created by the pre-atomic consumer implementation.
    if _is_terminal_moderation_state(photo, observation):
        _complete_moderation_outbox(outbox)
        await session.commit()
        log.info(
            "moderation.processor.already_moderated",
            photo_id=photo_id,
            status=photo.status,
            moderation_status=observation.moderation_status,
        )
        return ProcessResult(
            photo_id=photo_id,
            decision="skipped",
            new_object_name=photo.object_name,
            review_queue_id=None,
            observation_id=observation.id,
            outbox_status=None,
        )
    if outbox.status == "succeeded":
        raise ModerationWorkInvalid("succeeded moderation outbox has nonterminal state")

    attachment_status = getattr(photo, "attachment_status", None)
    if attachment_status not in (None, "attached"):
        raise ModerationWorkInvalid(f"photo attachment status is {attachment_status}")

    if photo.status != "pending" or observation.moderation_status in {
        "clean",
        "quarantine",
        "pilot_private",
        "rejected",
    }:
        raise ModerationWorkInvalid("photo and observation moderation state is inconsistent")

    canonical_object_name = photo.canonical_object_name or photo.object_name
    if canonical_object_name != object_name or photo.bucket != bucket:
        raise ModerationWorkInvalid("blob location did not match the committed photo")
    if photo.canonical_object_name is None or not object_name.startswith("pending/finalized/"):
        raise ModerationWorkInvalid("moderation requires a finalized canonical photo")

    image_bytes = await asyncio.to_thread(
        storage.fetch_object_bytes,
        bucket=bucket,
        object_name=object_name,
    )

    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    try:
        width_px, height_px = await asyncio.to_thread(validate_canonical_jpeg, image_bytes)
    except PhotoValidationError as exc:
        raise ModerationWorkInvalid(f"canonical JPEG validation failed: {exc}") from exc
    if (
        photo.verified_at is None
        or photo.byte_count != len(image_bytes)
        or photo.width_px != width_px
        or photo.height_px != height_px
        or photo.sha256 != image_sha256
    ):
        raise ModerationWorkInvalid("canonical photo metadata verification failed")

    # ModerationUnavailable bubbles up so the Service Bus consumer abandons
    # the message for retry; it never defaults the photo to clean.
    result = await moderator.moderate(image_bytes)

    if result.decision == "pilot_private":
        # Use a dedicated private prefix so Azure lifecycle can enforce the
        # seven-day W1 ceiling without applying an unsafe blanket rule to all
        # attached `pending/finalized/` observations.
        new_object = f"{_PILOT_PRIVATE_PREFIX}{photo_id}.jpg"
        await asyncio.to_thread(
            storage.copy_object,
            src_bucket=bucket,
            src_object=object_name,
            dst_bucket=bucket,
            dst_object=new_object,
            expected_size=len(image_bytes),
            expected_sha256=image_sha256,
        )
        photo.object_name = new_object
        photo.canonical_object_name = new_object
        photo.moderated_at = datetime.now(UTC)
        observation.moderation_status = "pilot_private"
        observation.moderation_labels = dict(result.labels)
        observation.moderation_source = "noop"
        observation.moderation_policy_version = "noop-w1-v1"
        _complete_moderation_outbox(outbox)
        await session.commit()
        try:
            await asyncio.to_thread(
                storage.delete_object,
                bucket=bucket,
                object_name=object_name,
            )
        except Exception as exc:
            log.warning(
                "moderation.processor.source_delete_failed",
                photo_id=photo_id,
                object_name=object_name,
                error=str(exc),
            )
        return ProcessResult(
            photo_id=photo_id,
            decision="pilot_private",
            new_object_name=new_object,
            review_queue_id=None,
            observation_id=observation.id,
            outbox_status=None,
        )

    if result.decision == "clean":
        new_object = f"{_OBSERVATIONS_PREFIX}{photo_id}.jpg"
        new_status = "clean"
    else:
        new_object = f"{_QUARANTINE_PREFIX}{photo_id}.jpg"
        new_status = "quarantine"

    await asyncio.to_thread(
        storage.copy_object,
        src_bucket=bucket,
        src_object=object_name,
        dst_bucket=bucket,
        dst_object=new_object,
        expected_size=len(image_bytes),
        expected_sha256=image_sha256,
    )

    photo.object_name = new_object
    photo.canonical_object_name = new_object
    photo.status = new_status
    photo.moderated_at = datetime.now(UTC)

    review_queue_id: str | None = None
    wrote_outbox = False

    if result.decision == "clean":
        if observation is not None:
            observation.moderation_status = "clean"
            observation.moderation_labels = dict(result.labels)
            observation.moderation_source = "azure"
            observation.moderation_policy_version = "azure-content-safety-2023-10-01"
            # Outbox row guarantees at-least-once iNat submit even if the
            # Service Bus send below fails. Replay job picks up rows that
            # stay in `pending` past the 5-min grace window.
            #
            # Gated on the Option B `inat_submit_enabled` flag (default
            # False). When False, the iNat-submit pipeline is dormant
            # and no outbox row is written -- the observation stays
            # entirely inside Hinterland until the kid claims it via
            # the Phase 3 age-13 iNat-claim flow.
            if settings is not None and settings.inat_submit_enabled:
                session.add(
                    models.InatSubmitOutbox(
                        observation_id=observation.id,
                        status="pending",
                    )
                )
                wrote_outbox = True
    else:
        # Flagged path: mark the observation quarantined so it cannot
        # be picked up by the iNat submit consumer until an adult
        # reviewer approves it.
        if observation is not None:
            observation.moderation_status = "quarantine"
            observation.moderation_labels = dict(result.labels)
            observation.moderation_source = "azure"
            observation.moderation_policy_version = "azure-content-safety-2023-10-01"

            # The review queue row needs a group_id. If the observation
            # doesn't exist yet (presign-then-no-create), we can't route
            # the review -- leave the photo quarantined and skip the
            # review row. The lifecycle rule cleans the orphan after 90d.
            review_queue_id = str(ULID())
            session.add(
                models.ReviewQueueItem(
                    id=review_queue_id,
                    group_id=observation.group_id,
                    photo_id=photo_id,
                    observation_id=observation.id,
                    status="pending",
                    reason=json.dumps(result.labels),
                )
            )

    # The physical move, observation state, review state, and moderation
    # outbox completion are one transaction. If the consumer crashes after
    # this commit, duplicate delivery sees `succeeded` and completes without
    # trying to process the old source path again.
    _complete_moderation_outbox(outbox)
    await session.commit()

    # DB now points at a verified destination. Source deletion is best-effort;
    # a lifecycle sweep can remove a duplicate, while deleting before commit
    # could strand the database on a missing source after a transaction fault.
    try:
        await asyncio.to_thread(
            storage.delete_object,
            bucket=bucket,
            object_name=object_name,
        )
    except Exception as exc:
        log.warning(
            "moderation.processor.source_delete_failed",
            photo_id=photo_id,
            object_name=object_name,
            error=str(exc),
        )

    log.info(
        "moderation.processor.processed",
        photo_id=photo_id,
        decision=result.decision,
        new_object=new_object,
        review_queue_id=review_queue_id,
    )

    outbox_status: Literal["enqueued", "pending"] | None = None
    if wrote_outbox and observation is not None:
        outbox_status = await _attempt_outbox_enqueue(
            session,
            observation_id=observation.id,
            settings=settings,
        )

    return ProcessResult(
        photo_id=photo_id,
        decision=result.decision,
        new_object_name=new_object,
        review_queue_id=review_queue_id,
        observation_id=observation.id if observation is not None else None,
        outbox_status=outbox_status,
    )


async def _attempt_outbox_enqueue(
    session: AsyncSession,
    *,
    observation_id: str,
    settings: Settings | None,
) -> Literal["enqueued", "pending"]:
    """Send the outbox row to Service Bus; update its status on success.

    Always returns -- never raises. Returns `enqueued` if the send
    succeeded and the row was flipped, `pending` otherwise. The
    `pending` case covers both "settings absent / Service Bus not
    provisioned yet" and "send attempted and failed"; both are handled
    identically by the 15-min replay job.
    """
    if settings is None:
        log.info(
            "moderation.processor.outbox_enqueue_skipped",
            observation_id=observation_id,
            reason="no_settings",
        )
        return "pending"

    result = await enqueue_inat_submit(observation_id, settings=settings)
    now = datetime.now(UTC)

    if result.success:
        await session.execute(
            update(models.InatSubmitOutbox)
            .where(models.InatSubmitOutbox.observation_id == observation_id)
            .values(status="enqueued", last_attempt_at=now)
        )
        await session.commit()
        return "enqueued"

    # Failure -- bump retry_count + last_attempt_at + last_error so the
    # replay job's debug + cap logic has the context it needs. Status
    # stays `pending` so the replay query picks the row up.
    await session.execute(
        update(models.InatSubmitOutbox)
        .where(models.InatSubmitOutbox.observation_id == observation_id)
        .values(
            last_attempt_at=now,
            retry_count=models.InatSubmitOutbox.retry_count + 1,
            last_error=result.reason or "unknown",
        )
    )
    await session.commit()
    return "pending"
