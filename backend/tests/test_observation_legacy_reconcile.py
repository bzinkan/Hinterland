from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from admin import observation_legacy_reconcile as legacy
from app.core.storage import StorageObjectProperties
from app.db import models


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def all(self) -> object:
        return self.value

    def one_or_none(self) -> object:
        return self.value


class _Storage:
    def __init__(self, raw_name: str, raw: bytes) -> None:
        self.objects = {raw_name: raw}
        self.deleted: list[str] = []

    def get_object_properties(self, *, bucket: str, object_name: str) -> StorageObjectProperties:
        del bucket
        if object_name not in self.objects:
            raise FileNotFoundError(object_name)
        return StorageObjectProperties(
            byte_count=len(self.objects[object_name]),
            content_type="image/jpeg",
            etag="stable",
        )

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        del bucket
        return self.objects[object_name]

    def put_object_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> None:
        del bucket, content_type, metadata, expected_sha256
        assert overwrite is False
        if object_name in self.objects and self.objects[object_name] != data:
            raise RuntimeError("immutable destination mismatch")
        self.objects[object_name] = data

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        del bucket
        self.deleted.append(object_name)
        self.objects.pop(object_name, None)


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (12, 80, 30)).save(output, format="JPEG")
    return output.getvalue()


def _rows() -> tuple[models.Observation, models.Photo, models.ModerationOutbox]:
    now = datetime(2026, 7, 9, tzinfo=UTC)
    photo_id = "01PHOTO0000000000000000000"
    observation = models.Observation(
        id="01OBS0000000000000000000000",
        user_id="01USER00000000000000000000",
        group_id="01GROUP0000000000000000000",
        photo_id=photo_id,
        submission_key=None,
        latitude=40.75,
        longitude=-73.99,
        geohash4=None,
        observed_at=now,
        location_source="legacy_coarsened",
        identification_source="legacy",
        moderation_status="pending",
    )
    observation.created_at = now
    photo = models.Photo(
        id=photo_id,
        user_id=observation.user_id,
        bucket="photos",
        object_name=f"pending/{photo_id}.jpg",
        status="pending",
        attachment_status="reserved",
        submission_key=None,
        content_type="image/jpeg",
    )
    outbox = models.ModerationOutbox(
        observation_id=observation.id,
        photo_id=photo.id,
        status="pending",
        retry_count=0,
    )
    return observation, photo, outbox


def _session(
    observation: models.Observation,
    photo: models.Photo,
    outbox: models.ModerationOutbox,
) -> SimpleNamespace:
    return SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result([(observation.id, observation.user_id)]),
                _Result((observation, photo)),
            ]
        ),
        get=AsyncMock(return_value=outbox),
        add=MagicMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_canonicalizes_legacy_pending_and_requeues_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, photo, outbox = _rows()
    storage = _Storage(photo.object_name, _jpeg())
    session = _session(observation, photo, outbox)
    lock = AsyncMock()
    rebuild = AsyncMock()
    monkeypatch.setattr(legacy, "acquire_user_lock", lock)
    monkeypatch.setattr(legacy, "enqueue_rebuild", rebuild)
    # Simulate the old dispatcher having stamped success despite having no W1
    # handler ledger. Adoption must not trust or expose that partial state.
    observation.dispatch_status = "complete"
    observation.dispatched_at = observation.created_at
    observation.rewards = [
        {
            "type": "first_find",
            "title": "legacy",
            "detail": "unverified",
            "icon": "legacy",
            "weight": 80,
            "payload": {},
        }
    ]

    stats = await legacy.reconcile_legacy_pending(session, storage)  # type: ignore[arg-type]

    assert stats.canonicalized == 1
    assert stats.rejected == 0
    assert photo.object_name == f"pending/finalized/{photo.id}.jpg"
    assert photo.canonical_object_name == photo.object_name
    assert photo.attachment_status == "attached"
    assert photo.submission_key == photo.id
    assert photo.verified_at is not None
    assert photo.sha256 is not None
    assert observation.submission_key == photo.id
    assert observation.latitude is None and observation.longitude is None
    assert observation.geohash4 is not None and len(observation.geohash4) == 4
    assert observation.dispatch_status == "unverified"
    assert observation.dispatched_at is None
    assert observation.rewards == []
    assert outbox.status == "pending"
    assert storage.deleted == [f"pending/{photo.id}.jpg"]
    lock.assert_awaited_once_with(session, observation.user_id)
    rebuild.assert_awaited_once_with(
        session,
        user_id=observation.user_id,
        trigger_observation_id=observation.id,
    )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_legacy_photo_fails_closed_and_queues_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, photo, outbox = _rows()
    storage = _Storage(photo.object_name, b"not-a-jpeg")
    session = _session(observation, photo, outbox)
    monkeypatch.setattr(legacy, "acquire_user_lock", AsyncMock())
    rebuild = AsyncMock()
    monkeypatch.setattr(legacy, "enqueue_rebuild", rebuild)

    stats = await legacy.reconcile_legacy_pending(session, storage)  # type: ignore[arg-type]

    assert stats.rejected == 1
    assert stats.canonicalized == 0
    assert photo.status == "deleted"
    assert photo.attachment_status == "deleted"
    assert observation.moderation_status == "rejected"
    assert observation.rejected_at is not None
    assert observation.latitude is None and observation.longitude is None
    assert outbox.status == "succeeded"
    assert outbox.last_error == "legacy_photo_rejected_during_canonicalization"
    rebuild.assert_awaited_once_with(
        session,
        user_id=observation.user_id,
        trigger_observation_id=observation.id,
    )
    assert storage.deleted == [f"pending/{photo.id}.jpg"]
