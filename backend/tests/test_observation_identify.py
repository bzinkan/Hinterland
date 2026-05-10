"""Tests for POST /v1/observations/{id}/identify (Phase 7 slice 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID00000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


class _StubStorage:
    """Stub PhotoStorage that returns fixed bytes."""

    def __init__(self, bytes_to_return: bytes = b"fake-jpeg-bytes") -> None:
        self.bytes_to_return = bytes_to_return
        self.fetch_calls: list[tuple[str, str]] = []

    def generate_put_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        self.fetch_calls.append((bucket, object_name))
        return self.bytes_to_return


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": _FIREBASE_UID, "role": "kid", "group_id": _GROUP_ID}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client(
    fake_session: AsyncMock,
    *,
    inat_token: str = "test-token",
    storage: _StubStorage | None = None,
    inat_client: httpx.AsyncClient | None = None,
) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test", inat_oauth_token=inat_token))
    # FastAPI resolves all dependencies before the handler runs, so even
    # tests that 401/403/404 before touching storage would otherwise pull
    # the real GcsSignedUrlGenerator (which needs ADC). Always inject a
    # stub.
    app.state.signed_url_generator = storage if storage is not None else _StubStorage()
    if inat_client is not None:
        app.state.inat_client = inat_client

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _obs_with_photo() -> tuple[models.Observation, models.Photo]:
    photo = models.Photo(
        id=_PHOTO_ID,
        user_id=_USER_ID,
        bucket="dragonfly-photos-test",
        object_name=f"pending/{_PHOTO_ID}.jpg",
        status="pending",
        content_type="image/jpeg",
    )
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
    )
    return obs, photo


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    obs_photo: tuple[models.Observation, models.Photo] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    obs_result = MagicMock()
    obs_result.one_or_none = MagicMock(return_value=obs_photo)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(obs_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


# ---------------------------------------------------------------------------


def test_identify_requires_bearer_token(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.post(f"/v1/observations/{_OBS_ID}/identify")
        assert response.status_code == 401


def test_identify_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 403


def test_identify_404_when_observation_missing_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=None)
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404


def test_identify_returns_cv_unavailable_when_no_token(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    storage = _StubStorage()
    for client in _build_client(fake_session, inat_token="", storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is True
        assert body["suggestions"] == []
        # Storage was never read -- short-circuited before any iNat work.
        assert storage.fetch_calls == []


@respx.mock
def test_identify_happy_path_returns_top_3(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "combined_score": 92.5,
                        "taxon": {
                            "id": 12345,
                            "name": "Cardinalis cardinalis",
                            "preferred_common_name": "Northern Cardinal",
                        },
                    },
                    {
                        "combined_score": 51.0,
                        "taxon": {
                            "id": 67890,
                            "name": "Cardinalis sinuatus",
                            "preferred_common_name": "Pyrrhuloxia",
                        },
                    },
                ]
            },
        )
    )
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is False
        assert len(body["suggestions"]) == 2
        assert body["suggestions"][0]["taxon_id"] == 12345
        assert body["suggestions"][0]["common_name"] == "Northern Cardinal"
        assert body["suggestions"][0]["score"] == 92.5
        # Storage WAS read with the observations photo key.
        assert storage.fetch_calls == [("dragonfly-photos-test", f"pending/{_PHOTO_ID}.jpg")]


@respx.mock
def test_identify_returns_cv_unavailable_on_inat_5xx(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(503)
    )
    for client in _build_client(fake_session, storage=_StubStorage()):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is True
        assert body["suggestions"] == []


@respx.mock
def test_identify_returns_empty_on_inat_4xx(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """4xx other than auth -> iNat couldn't classify; not an outage."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(400, json={"error": "bad image"})
    )
    for client in _build_client(fake_session, storage=_StubStorage()):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        # iNat said "no idea" -- the call succeeded, just empty.
        assert body["cv_unavailable"] is False
        assert body["suggestions"] == []
