"""Move a `pending/` photo to its post-moderation home and update DB.

Idempotent within reason -- if the Photo row's status has already
moved past `pending`, we treat the call as a no-op and return early.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.storage import SignedUrlGenerator
from app.db import models
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
) -> ProcessResult:
    """Run one moderation cycle on the GCS object at `bucket/object_name`."""
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

    review_queue_id: str | None = None
    if result.decision == "flagged":
        observation = (
            await session.execute(
                select(models.Observation).where(models.Observation.photo_id == photo_id)
            )
        ).scalar_one_or_none()

        # The review queue row needs a group_id. If the observation
        # doesn't exist yet (presign-then-no-create), we can't route
        # the review -- leave the photo quarantined and skip the
        # review row. The lifecycle rule cleans the orphan after 90d.
        if observation is not None:
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

    return ProcessResult(
        photo_id=photo_id,
        decision=result.decision,
        new_object_name=new_object,
        review_queue_id=review_queue_id,
    )
