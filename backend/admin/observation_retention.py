"""Purge expired private observation bytes without exposing child photos.

This job is the database-aware half of the Observation retention policy:

* unattached reservations and their raw uploads expire after 24 hours;
* unreferenced legacy/raw/finalized pending blobs expire after 24 hours;
* W1 ``pilot_private`` photos expire after seven days.

Azure lifecycle management separately removes old ``pending/uploads/`` and
``quarantine/`` bytes as a last-resort guard.  Canonical finalized blobs are
handled here because Azure lifecycle rules cannot see attachment or moderation
state and must not delete a legitimate pending observation.

Run as an Azure Container Apps scheduled job::

    python -m admin.observation_retention
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models

log = structlog.get_logger()

_UNATTACHED_RETENTION = timedelta(hours=24)
_ORPHAN_RETENTION = timedelta(hours=24)
_PILOT_PRIVATE_RETENTION = timedelta(days=7)
# Scan the common parent once so pre-W1 `pending/<photo_id>.jpg` objects are
# included without double-listing the newer uploads/finalized subtrees.
_SWEEP_PREFIXES = ("pending/",)


@dataclass(frozen=True)
class BlobItem:
    object_name: str
    last_modified: datetime


class RetentionStorage(Protocol):
    """Small storage surface used by the retention job and its fakes."""

    def list_objects(self, *, bucket: str, prefix: str) -> list[BlobItem]: ...

    def delete_object(self, *, bucket: str, object_name: str) -> None: ...


class AzureBlobRetentionStorage:
    """Managed-identity Blob implementation kept out of the request facade."""

    def __init__(self, account_endpoint: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self._service = BlobServiceClient(
            account_url=account_endpoint,
            credential=DefaultAzureCredential(),
        )

    def list_objects(self, *, bucket: str, prefix: str) -> list[BlobItem]:
        container = self._service.get_container_client(bucket)
        return [
            BlobItem(object_name=blob.name, last_modified=blob.last_modified)
            for blob in container.list_blobs(name_starts_with=prefix)
        ]

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._service.get_blob_client(bucket, object_name).delete_blob()
        except ResourceNotFoundError:
            # A prior retry or the lifecycle policy may already have removed it.
            return


@dataclass(frozen=True)
class RetentionStats:
    reservations_expired: int = 0
    pilot_photos_purged: int = 0
    orphan_objects_deleted: int = 0
    objects_deleted: int = 0
    delete_failures: int = 0


async def _delete(
    storage: RetentionStorage,
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
            "observation.retention.delete_failed",
            bucket=bucket,
            object_name=object_name,
            error=str(exc),
        )
        return False


async def sweep(
    session: AsyncSession,
    storage: RetentionStorage,
    *,
    bucket: str,
    now: datetime | None = None,
) -> RetentionStats:
    """Apply retention policy and return auditable counters.

    Byte deletion happens before a row is marked deleted.  A transient Blob
    failure therefore leaves the row eligible for the next run instead of
    recording a purge that did not happen.
    """
    current = now or datetime.now(UTC)
    unattached_cutoff = current - _UNATTACHED_RETENTION
    pilot_cutoff = current - _PILOT_PRIVATE_RETENTION
    orphan_cutoff = current - _ORPHAN_RETENTION

    rows = (
        await session.execute(
            select(models.Photo, models.Observation).outerjoin(
                models.Observation,
                models.Observation.photo_id == models.Photo.id,
            )
        )
    ).all()

    reservations_expired = 0
    pilot_photos_purged = 0
    orphan_objects_deleted = 0
    objects_deleted = 0
    delete_failures = 0
    protected: set[str] = set()

    for photo, observation in rows:
        names = {photo.object_name}
        if photo.canonical_object_name:
            names.add(photo.canonical_object_name)

        is_expired_reservation = (
            photo.attachment_status == "reserved"
            # A previous API revision can insert an observation after the
            # additive migration without knowing `attachment_status`. The
            # legacy reconciler repairs it; retention must not delete the
            # attached bytes during that compatibility window.
            and observation is None
            and photo.created_at is not None
            and photo.created_at < unattached_cutoff
        )
        pilot_timestamp = photo.moderated_at or (
            observation.updated_at if observation is not None else None
        )
        is_expired_pilot = (
            observation is not None
            and observation.moderation_status == "pilot_private"
            and photo.attachment_status != "deleted"
            and pilot_timestamp is not None
            and pilot_timestamp < pilot_cutoff
        )

        if not is_expired_reservation and not is_expired_pilot:
            if photo.attachment_status != "deleted":
                protected.update(names)
            continue

        row_ok = True
        for object_name in names:
            deleted = await _delete(
                storage,
                bucket=photo.bucket,
                object_name=object_name,
            )
            objects_deleted += int(deleted)
            delete_failures += int(not deleted)
            row_ok = row_ok and deleted

        if not row_ok:
            protected.update(names)
            continue

        photo.status = "deleted"
        photo.attachment_status = "deleted"
        if is_expired_reservation:
            reservations_expired += 1
        else:
            pilot_photos_purged += 1

    # Flush state changes before scanning.  A crash after the Blob delete but
    # before commit is harmless: the next run treats missing deletes as success.
    await session.commit()

    for prefix in _SWEEP_PREFIXES:
        items = await asyncio.to_thread(
            storage.list_objects,
            bucket=bucket,
            prefix=prefix,
        )
        for item in items:
            if item.object_name in protected or item.last_modified >= orphan_cutoff:
                continue
            deleted = await _delete(
                storage,
                bucket=bucket,
                object_name=item.object_name,
            )
            if deleted:
                objects_deleted += 1
                orphan_objects_deleted += 1
            else:
                delete_failures += 1

    stats = RetentionStats(
        reservations_expired=reservations_expired,
        pilot_photos_purged=pilot_photos_purged,
        orphan_objects_deleted=orphan_objects_deleted,
        objects_deleted=objects_deleted,
        delete_failures=delete_failures,
    )
    log.info("observation.retention.complete", **stats.__dict__)
    return stats


async def main() -> None:
    settings = get_settings()
    if settings.storage_provider != "blob" or not settings.blob_account_endpoint:
        raise RuntimeError("observation retention requires configured Azure Blob storage")

    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    storage = AzureBlobRetentionStorage(settings.blob_account_endpoint)
    try:
        async with sessions() as session:
            stats = await sweep(
                session,
                storage,
                bucket=settings.photos_bucket,
            )
        print(
            "observation_retention: "
            f"{stats.reservations_expired} reservation(s), "
            f"{stats.pilot_photos_purged} pilot photo(s), "
            f"{stats.orphan_objects_deleted} orphan(s) purged"
        )
        if stats.delete_failures:
            raise RuntimeError(f"{stats.delete_failures} Blob deletion(s) failed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
