"""Move a `pending/` photo to its post-moderation home and update DB.

Idempotent within reason -- if the Photo row's status has already
moved past `pending`, we treat the call as a no-op and return early.

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

log = structlog.get_logger()

_PENDING_PREFIX = "pending/"
_OBSERVATIONS_PREFIX = "observations/"
_QUARANTINE_PREFIX = "quarantine/"


class PhotoNotFound(Exception):
    """The Photo row referenced by the GCS object doesn't exist in DB.

    Likely race: the moderation event fired before the
    `POST /v1/photos/presign` transaction was committed. Caller
    should retry; Eventarc handles that on its own with backoff.
    """


@dataclass(frozen=True)
class ProcessResult:
    photo_id: str
    decision: Literal["clean", "flagged", "skipped"]
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
    rest = object_name[len(_PENDING_PREFIX) :]
    if "." in rest:
        rest = rest.rsplit(".", 1)[0]
    if not rest:
        return None
    return rest


async def process_pending_photo(
    session: AsyncSession,
    storage: SignedUrlGenerator,
    moderator: Moderator,
    *,
    bucket: str,
    object_name: str,
    settings: Settings | None = None,
) -> ProcessResult:
    """Run one moderation cycle on the GCS object at `bucket/object_name`.

    When `settings` is provided AND the moderation decision is `clean`,
    the processor also writes an `inat_submit_outbox` row in the same
    transaction and attempts to enqueue the observation to Service
    Bus after commit. When `settings` is None, the outbox row is still
    written but no enqueue is attempted -- the replay job will pick it
    up. Tests that don't care about the outbox path can pass
    `settings=None`.
    """
    photo_id = _photo_id_from_object_name(object_name)
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

    if photo.status != "pending":
        log.info(
            "moderation.processor.already_moderated",
            photo_id=photo_id,
            status=photo.status,
        )
        return ProcessResult(
            photo_id=photo_id,
            decision="skipped",
            new_object_name=photo.object_name,
            review_queue_id=None,
        )

    image_bytes = storage.fetch_object_bytes(bucket=bucket, object_name=object_name)

    # ModerationUnavailable bubbles up; the route surface returns non-2xx
    # so Eventarc retries the trigger.
    result = await moderator.moderate(image_bytes)

    if result.decision == "clean":
        new_object = f"{_OBSERVATIONS_PREFIX}{photo_id}.jpg"
        new_status = "clean"
    else:
        new_object = f"{_QUARANTINE_PREFIX}{photo_id}.jpg"
        new_status = "quarantine"

    storage.copy_object(
        src_bucket=bucket,
        src_object=object_name,
        dst_bucket=bucket,
        dst_object=new_object,
    )
    storage.delete_object(bucket=bucket, object_name=object_name)

    photo.object_name = new_object
    photo.status = new_status
    photo.moderated_at = datetime.now(UTC)

    # Find the matching observation row for both paths. Clean rows feed
    # the outbox; flagged rows feed the review queue.
    observation = (
        await session.execute(
            select(models.Observation).where(models.Observation.photo_id == photo_id)
        )
    ).scalar_one_or_none()

    review_queue_id: str | None = None
    wrote_outbox = False

    if result.decision == "clean":
        if observation is not None:
            observation.moderation_status = "clean"
            observation.moderation_labels = dict(result.labels)
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

    await session.commit()

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
