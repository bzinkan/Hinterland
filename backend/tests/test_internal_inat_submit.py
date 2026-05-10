"""Tests for POST /internal/inat/submit (Phase 8 slice 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_OBS_ID = "01J0OBSID0000000000000ULID"  # 26 chars (ULID size)
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


class _StubStorage:
    def __init__(self, image_bytes: bytes = b"jpeg") -> None:
        self._bytes = image_bytes

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return self._bytes

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        raise NotImplementedError

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        raise NotImplementedError


def _build_client(
    fake_session: AsyncMock,
    *,
    storage: _StubStorage | None = None,
) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test", inat_oauth_token="test-token"))
    app.state.signed_url_generator = storage if storage is not None else _StubStorage()

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _photo_row(status: str = "clean") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id="user-id",
        bucket="dragonfly-photos-test",
        object_name=f"observations/{_PHOTO_ID}.jpg",
        status=status,
        content_type="image/jpeg",
    )


def _obs_row(*, inat_id: int | None = None) -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id="user-id",
        group_id="group-id",
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=12345,
        species_name="Northern Cardinal",
        inat_observation_id=inat_id,
    )
    obs.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return obs


def _wire_session(
    fake_session: AsyncMock,
    *,
    observation: models.Observation | None,
    photo: models.Photo | None = None,
) -> None:
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=observation)
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    side_effects: list[Any] = [obs_result]
    if observation is not None and observation.inat_observation_id is None:
        # The endpoint only loads the photo if it's about to submit.
        side_effects.append(photo_result)
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------


def test_submit_404_when_observation_missing(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, observation=None)
    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 404


def test_submit_short_circuits_when_already_submitted(fake_session: AsyncMock) -> None:
    obs = _obs_row(inat_id=9876543)
    _wire_session(fake_session, observation=obs)
    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] is True
        assert body["inat_observation_id"] == 9876543


def test_submit_skips_when_photo_not_clean(fake_session: AsyncMock) -> None:
    obs = _obs_row()
    photo = _photo_row(status="quarantine")
    _wire_session(fake_session, observation=obs, photo=photo)
    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] is True
        assert body["inat_observation_id"] is None


def test_submit_404_when_photo_missing(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, observation=_obs_row(), photo=None)
    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 404


@respx.mock
def test_submit_happy_path_writes_inat_id(fake_session: AsyncMock) -> None:
    obs = _obs_row()
    _wire_session(fake_session, observation=obs, photo=_photo_row())

    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 9876543, "uuid": _OBS_ID})
    )
    respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] is False
        assert body["inat_observation_id"] == 9876543

    assert obs.inat_observation_id == 9876543
    assert obs.submitted_to_inat_at is not None
    fake_session.commit.assert_awaited_once()


@respx.mock
def test_submit_503_when_inat_unavailable(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, observation=_obs_row(), photo=_photo_row())
    respx.post("https://api.inaturalist.org/v1/observations").mock(return_value=httpx.Response(503))
    for client in _build_client(fake_session):
        response = client.post("/internal/inat/submit", json={"observation_id": _OBS_ID})
        assert response.status_code == 503
