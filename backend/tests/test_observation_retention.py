from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from admin.observation_retention import BlobItem, sweep
from app.db import models


class _Result:
    def __init__(self, rows: list[tuple[models.Photo, models.Observation | None]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[models.Photo, models.Observation | None]]:
        return self._rows


class _Storage:
    def __init__(self, items: list[BlobItem], *, fail: set[str] | None = None) -> None:
        self.items = {item.object_name: item for item in items}
        self.fail = fail or set()
        self.deleted: list[str] = []

    def list_objects(self, *, bucket: str, prefix: str) -> list[BlobItem]:
        assert bucket == "photos"
        return [item for name, item in self.items.items() if name.startswith(prefix)]

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        assert bucket == "photos"
        if object_name in self.fail:
            raise RuntimeError("storage unavailable")
        self.deleted.append(object_name)
        self.items.pop(object_name, None)


def _photo(
    photo_id: str,
    object_name: str,
    *,
    created_at: datetime,
    attachment_status: str,
    canonical_object_name: str | None = None,
    moderated_at: datetime | None = None,
) -> models.Photo:
    row = models.Photo(
        id=photo_id,
        user_id="01USER00000000000000000000",
        bucket="photos",
        object_name=object_name,
        canonical_object_name=canonical_object_name,
        status="pending",
        attachment_status=attachment_status,
        content_type="image/jpeg",
    )
    row.created_at = created_at
    row.updated_at = created_at
    row.moderated_at = moderated_at
    return row


def _observation(photo_id: str, *, status: str, updated_at: datetime) -> models.Observation:
    row = models.Observation(
        id="01OBS0000000000000000000000",
        user_id="01USER00000000000000000000",
        group_id="01GROUP0000000000000000000",
        photo_id=photo_id,
        observed_at=updated_at,
        location_source="none",
        identification_source="unknown",
        moderation_status=status,
    )
    row.created_at = updated_at
    row.updated_at = updated_at
    return row


def _session(
    rows: list[tuple[models.Photo, models.Observation | None]],
) -> SimpleNamespace:
    return SimpleNamespace(
        execute=AsyncMock(return_value=_Result(rows)),
        commit=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_expires_unattached_reservation_after_24_hours() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    object_name = "pending/uploads/01PHOTO0000000000000000000.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        object_name,
        created_at=now - timedelta(hours=25),
        attachment_status="reserved",
    )
    storage = _Storage([BlobItem(object_name, now - timedelta(hours=25))])
    session = _session([(photo, None)])

    stats = await sweep(session, storage, bucket="photos", now=now)  # type: ignore[arg-type]

    assert stats.reservations_expired == 1
    assert stats.objects_deleted == 1
    assert photo.status == "deleted"
    assert photo.attachment_status == "deleted"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purges_pilot_private_photo_after_seven_days() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    canonical = "pilot-private/01PHOTO0000000000000000000.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        canonical,
        canonical_object_name=canonical,
        created_at=now - timedelta(days=8),
        moderated_at=now - timedelta(days=8),
        attachment_status="attached",
    )
    observation = _observation(
        photo.id,
        status="pilot_private",
        updated_at=now - timedelta(days=8),
    )
    storage = _Storage([BlobItem(canonical, now - timedelta(days=8))])

    stats = await sweep(  # type: ignore[arg-type]
        _session([(photo, observation)]), storage, bucket="photos", now=now
    )

    assert stats.pilot_photos_purged == 1
    assert storage.deleted == [canonical]
    assert photo.status == "deleted"
    assert photo.attachment_status == "deleted"

    repeated = await sweep(  # type: ignore[arg-type]
        _session([(photo, observation)]), storage, bucket="photos", now=now
    )
    assert repeated.pilot_photos_purged == 0
    assert repeated.objects_deleted == 0


@pytest.mark.asyncio
async def test_keeps_pilot_private_photo_until_full_seven_days() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    canonical = "pilot-private/01PHOTO0000000000000000000.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        canonical,
        canonical_object_name=canonical,
        created_at=now - timedelta(days=6),
        moderated_at=now - timedelta(days=6),
        attachment_status="attached",
    )
    observation = _observation(
        photo.id,
        status="pilot_private",
        updated_at=now - timedelta(days=6),
    )
    storage = _Storage([BlobItem(canonical, now - timedelta(days=6))])

    stats = await sweep(  # type: ignore[arg-type]
        _session([(photo, observation)]), storage, bucket="photos", now=now
    )

    assert stats.pilot_photos_purged == 0
    assert storage.deleted == []
    assert photo.attachment_status == "attached"


@pytest.mark.asyncio
async def test_keeps_attached_pending_photo_and_deletes_only_old_orphan() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    protected = "pending/finalized/01PHOTO0000000000000000000.jpg"
    old_orphan = "pending/finalized/orphan-old.jpg"
    young_orphan = "pending/uploads/orphan-young.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        protected,
        canonical_object_name=protected,
        created_at=now - timedelta(days=3),
        attachment_status="attached",
    )
    observation = _observation(
        photo.id,
        status="pending",
        updated_at=now - timedelta(days=3),
    )
    storage = _Storage(
        [
            BlobItem(protected, now - timedelta(days=3)),
            BlobItem(old_orphan, now - timedelta(hours=25)),
            BlobItem(young_orphan, now - timedelta(hours=23)),
        ]
    )

    stats = await sweep(  # type: ignore[arg-type]
        _session([(photo, observation)]), storage, bucket="photos", now=now
    )

    assert stats.orphan_objects_deleted == 1
    assert storage.deleted == [old_orphan]
    assert protected in storage.items
    assert young_orphan in storage.items
    assert photo.attachment_status == "attached"


@pytest.mark.asyncio
async def test_legacy_attached_reservation_is_protected_while_old_orphan_is_removed() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    protected = "pending/01PHOTO0000000000000000000.jpg"
    old_orphan = "pending/legacy-orphan-old.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        protected,
        created_at=now - timedelta(days=3),
        # The pre-W1 API does not know the additive attachment column and can
        # leave this default in place during the migration-first cutover.
        attachment_status="reserved",
    )
    observation = _observation(
        photo.id,
        status="pending",
        updated_at=now - timedelta(days=3),
    )
    storage = _Storage(
        [
            BlobItem(protected, now - timedelta(days=3)),
            BlobItem(old_orphan, now - timedelta(days=3)),
        ]
    )

    stats = await sweep(  # type: ignore[arg-type]
        _session([(photo, observation)]), storage, bucket="photos", now=now
    )

    assert stats.reservations_expired == 0
    assert stats.orphan_objects_deleted == 1
    assert storage.deleted == [old_orphan]
    assert protected in storage.items
    assert photo.status == "pending"


@pytest.mark.asyncio
async def test_failed_delete_leaves_row_eligible_for_retry() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    object_name = "pending/uploads/01PHOTO0000000000000000000.jpg"
    photo = _photo(
        "01PHOTO0000000000000000000",
        object_name,
        created_at=now - timedelta(hours=25),
        attachment_status="reserved",
    )
    storage = _Storage(
        [BlobItem(object_name, now - timedelta(hours=25))],
        fail={object_name},
    )

    stats = await sweep(  # type: ignore[arg-type]
        _session([(photo, None)]), storage, bucket="photos", now=now
    )

    assert stats.delete_failures == 1
    assert stats.reservations_expired == 0
    assert photo.status == "pending"
    assert photo.attachment_status == "reserved"
