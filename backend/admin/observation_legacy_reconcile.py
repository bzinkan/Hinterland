"""Safely adopt observations written by the pre-W1 API during cutover.

The previous API stores attached bytes as ``pending/<photo_id>.jpg`` and does
not know the W1 submission, attachment, verification, or moderation-outbox
columns. Alembic keeps the submission keys nullable for that short
migration-first compatibility window. This scheduled job closes the gap:

* acquire the same per-user advisory lock as submission/rebuild;
* verify and re-encode the legacy JPEG into ``pending/finalized/``;
* fill compatibility keys, verified metadata, and coarse-only location;
* create/reset the committed moderation-outbox row; and
* delete the raw legacy object best-effort after commit.

Permanently invalid or missing legacy bytes fail closed to ``rejected`` and
queue the normal deterministic rebuild. Transient infrastructure failures are
raised so Container Apps retries the job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.geospatial import encode_geohash
from app.core.storage import BlobSignedUrlGenerator, SignedUrlGenerator
from app.db import models
from app.derived_state import enqueue_rebuild
from app.derived_state.rebuild import acquire_user_lock
from app.observation.photo_finalize import PhotoValidationError, finalize_uploaded_photo

log = structlog.get_logger()

_LEGACY_PREFIX = "pending/"
_W1_PREFIXES = ("pending/uploads/", "pending/finalized/")
_MAX_PER_RUN = 200
_POLICY_VERSION = "legacy-canonicalization-v1"


@dataclass(frozen=True)
class LegacyReconcileStats:
    candidates: int = 0
    canonicalized: int = 0
    rejected: int = 0
    raw_delete_failures: int = 0


def _is_legacy_pending(object_name: str) -> bool:
    return object_name.startswith(_LEGACY_PREFIX) and not object_name.startswith(_W1_PREFIXES)


def _coarsen_compat_location(observation: models.Observation) -> None:
    """Remove coordinates that an old revision may write after migration."""
    if (
        observation.geohash4 is None
        and observation.latitude is not None
        and observation.longitude is not None
    ):
        try:
            observation.geohash4 = encode_geohash(
                observation.latitude,
                observation.longitude,
                precision=4,
            )
        except ValueError:
            # Invalid legacy coordinates are discarded, never logged.
            observation.geohash4 = None
    observation.latitude = None
    observation.longitude = None
    observation.location_source = "legacy_coarsened" if observation.geohash4 is not None else "none"


async def _delete_raw_best_effort(
    storage: SignedUrlGenerator,
    *,
    bucket: str,
    object_name: str,
) -> bool:
    try:
        await asyncio.to_thread(
            storage.delete_object,
            bucket=bucket,
            object_name=object_name,
        )
        return True
    except Exception as exc:
        log.warning(
            "observation.legacy_reconcile.raw_delete_failed",
            bucket=bucket,
            object_name=object_name,
            error=type(exc).__name__,
        )
        return False


async def reconcile_legacy_pending(
    session: AsyncSession,
    storage: SignedUrlGenerator,
    *,
    limit: int = _MAX_PER_RUN,
) -> LegacyReconcileStats:
    """Canonicalize a bounded batch of legacy attached pending photos."""
    candidate_rows = (
        await session.execute(
            select(models.Observation.id, models.Observation.user_id)
            .join(models.Photo, models.Photo.id == models.Observation.photo_id)
            .where(
                models.Observation.moderation_status == "pending",
                models.Observation.rejected_at.is_(None),
                models.Photo.status == "pending",
                models.Photo.canonical_object_name.is_(None),
                models.Photo.object_name.like("pending/%"),
                models.Photo.object_name.not_like("pending/uploads/%"),
                models.Photo.object_name.not_like("pending/finalized/%"),
            )
            .order_by(models.Observation.created_at, models.Observation.id)
            .limit(limit)
        )
    ).all()

    canonicalized = 0
    rejected = 0
    raw_delete_failures = 0

    for observation_id, user_id in candidate_rows:
        # Submission, correction, rebuild, and compatibility adoption all use
        # the same lock order: advisory user lock before row locks.
        await acquire_user_lock(session, str(user_id))
        locked = (
            await session.execute(
                select(models.Observation, models.Photo)
                .join(models.Photo, models.Photo.id == models.Observation.photo_id)
                .where(
                    models.Observation.id == str(observation_id),
                    models.Observation.moderation_status == "pending",
                    models.Photo.status == "pending",
                    models.Photo.canonical_object_name.is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()
        if locked is None:
            await session.commit()
            continue

        observation, photo = locked
        raw_object_name = photo.object_name
        if not _is_legacy_pending(raw_object_name):
            await session.commit()
            continue

        try:
            canonical = await finalize_uploaded_photo(
                storage,
                bucket=photo.bucket,
                raw_object_name=raw_object_name,
                photo_id=photo.id,
            )
        except PhotoValidationError as exc:
            now = datetime.now(UTC)
            photo.status = "deleted"
            photo.attachment_status = "deleted"
            photo.submission_key = photo.submission_key or photo.id
            photo.moderated_at = now
            observation.submission_key = observation.submission_key or photo.id
            observation.moderation_status = "rejected"
            observation.moderation_source = "none"
            observation.moderation_policy_version = _POLICY_VERSION
            observation.rejected_at = now
            _coarsen_compat_location(observation)

            outbox = await session.get(models.ModerationOutbox, observation.id)
            if outbox is not None:
                # The migration registered this work. Rejection is a terminal,
                # fail-closed resolution rather than a stuck/DLQ item.
                outbox.status = "succeeded"
                outbox.last_attempt_at = now
                outbox.last_error = "legacy_photo_rejected_during_canonicalization"
            await enqueue_rebuild(
                session,
                user_id=observation.user_id,
                trigger_observation_id=observation.id,
            )
            await session.commit()
            if not await _delete_raw_best_effort(
                storage,
                bucket=photo.bucket,
                object_name=raw_object_name,
            ):
                raw_delete_failures += 1
            rejected += 1
            log.info(
                "observation.legacy_reconcile.rejected",
                observation_id=observation.id,
                photo_id=photo.id,
                reason=type(exc).__name__,
            )
            continue
        except Exception:
            await session.rollback()
            raise

        photo.object_name = canonical.object_name
        photo.canonical_object_name = canonical.object_name
        photo.attachment_status = "attached"
        photo.submission_key = photo.submission_key or photo.id
        photo.content_type = "image/jpeg"
        photo.byte_count = canonical.byte_count
        photo.width_px = canonical.width_px
        photo.height_px = canonical.height_px
        photo.sha256 = canonical.sha256
        photo.verified_at = canonical.verified_at
        observation.submission_key = observation.submission_key or photo.id
        # The pre-W1 dispatcher had no durable handler ledger and may have
        # applied only a prefix of derived side effects before this adoption.
        # Replacement-first rebuild is the sole safe reconciliation path.
        observation.dispatch_status = "unverified"
        observation.dispatched_at = None
        observation.rewards = []
        _coarsen_compat_location(observation)

        outbox = await session.get(models.ModerationOutbox, observation.id)
        if outbox is None:
            outbox = models.ModerationOutbox(
                observation_id=observation.id,
                photo_id=photo.id,
                status="pending",
                retry_count=0,
            )
            session.add(outbox)
        else:
            outbox.photo_id = photo.id
            outbox.status = "pending"
            outbox.retry_count = 0
            outbox.lease_until = None
            outbox.last_attempt_at = None
            outbox.last_error = None

        await enqueue_rebuild(
            session,
            user_id=observation.user_id,
            trigger_observation_id=observation.id,
        )

        await session.commit()
        if raw_object_name != canonical.object_name and not await _delete_raw_best_effort(
            storage,
            bucket=photo.bucket,
            object_name=raw_object_name,
        ):
            raw_delete_failures += 1
        canonicalized += 1

    stats = LegacyReconcileStats(
        candidates=len(candidate_rows),
        canonicalized=canonicalized,
        rejected=rejected,
        raw_delete_failures=raw_delete_failures,
    )
    log.info("observation.legacy_reconcile.complete", **stats.__dict__)
    return stats


async def main() -> None:
    settings = get_settings()
    if settings.storage_provider != "blob" or not settings.blob_account_endpoint:
        raise RuntimeError("legacy reconciliation requires configured Azure Blob storage")

    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    storage = BlobSignedUrlGenerator(settings.blob_account_endpoint)
    try:
        async with sessions() as session:
            total = LegacyReconcileStats()
            while True:
                batch = await reconcile_legacy_pending(session, storage)
                total = LegacyReconcileStats(
                    candidates=total.candidates + batch.candidates,
                    canonicalized=total.canonicalized + batch.canonicalized,
                    rejected=total.rejected + batch.rejected,
                    raw_delete_failures=(total.raw_delete_failures + batch.raw_delete_failures),
                )
                if batch.candidates < _MAX_PER_RUN:
                    break
        print(
            "observation_legacy_reconcile: "
            f"{total.canonicalized} canonicalized, {total.rejected} rejected, "
            f"{total.raw_delete_failures} raw-delete retry item(s)"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
