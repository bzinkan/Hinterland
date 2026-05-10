from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
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
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


def _stub_token_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uid: str = _FIREBASE_UID,
    group_id: str | None = _GROUP_ID,
) -> None:
    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        claims: dict[str, object] = {"uid": uid, "role": "kid"}
        if group_id is not None:
            claims["group_id"] = group_id
        return claims

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def observations_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _photo_row(status: str = "pending") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id=_USER_ID,
        bucket="dragonfly-photos-test",
        object_name=f"pending/{_PHOTO_ID}.jpg",
        status=status,
        content_type="image/jpeg",
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    photo: models.Photo | None = None,
    membership_id: str | None = None,
) -> None:
    """Wire `session.execute(...)` for the user -> photo -> membership-update sequence.

    Each `.execute()` returns a Result-like with the corresponding shape.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    membership_result = MagicMock()
    membership_result.scalar_one_or_none = MagicMock(return_value=membership_id)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(photo_result)
        if photo is not None and photo.status == "pending":
            side_effects.append(membership_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()


def _valid_payload() -> dict[str, object]:
    return {
        "photo_id": _PHOTO_ID,
        "latitude": 39.1031,
        "longitude": -84.5120,
        "taxon_id": 12345,
        "species_name": "Northern Cardinal",
        "place_name": "Cincinnati, OH",
    }


# ---------------------------------------------------------------------------


def test_create_requires_bearer_token(observations_client: TestClient) -> None:
    response = observations_client.post("/v1/observations", json=_valid_payload())
    assert response.status_code == 401


def test_create_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_create_403_when_token_missing_group_id(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch, group_id=None)
    _wire_session(fake_session, user=_user_row())

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403
    assert "group_id" in response.json()["error"]["message"]


def test_create_404_when_photo_missing_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), photo=None)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 404


def test_create_409_when_photo_not_pending(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), photo=_photo_row(status="clean"))

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 409
    assert "clean" in response.json()["error"]["message"]


def test_create_403_when_membership_missing(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id=None,
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403
    fake_session.add.assert_not_called()


def test_create_422_on_invalid_latitude(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    payload = _valid_payload() | {"latitude": 91.0}

    response = observations_client.post(
        "/v1/observations",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_create_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id="01J0MEMBERID0000000000ULID",
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["user_id"] == _USER_ID
    assert body["group_id"] == _GROUP_ID
    assert body["photo_id"] == _PHOTO_ID
    assert body["latitude"] == 39.1031
    assert body["longitude"] == -84.5120
    assert body["taxon_id"] == 12345
    assert body["species_name"] == "Northern Cardinal"
    # geohash4 length 4, base32-ish
    assert body["geohash4"] is not None
    assert len(body["geohash4"]) == 4

    fake_session.add.assert_called_once()
    obs: models.Observation = fake_session.add.call_args.args[0]
    assert isinstance(obs, models.Observation)
    assert obs.user_id == _USER_ID
    assert obs.group_id == _GROUP_ID
    assert obs.photo_id == _PHOTO_ID
    assert obs.geohash4 == body["geohash4"]
    fake_session.commit.assert_awaited_once()
