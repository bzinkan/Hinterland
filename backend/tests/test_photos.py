from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.config import Settings
from app.core.storage import SignedUrlGenerator
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"


class _StubSignedUrlGenerator:
    """Records the args it was called with and returns a deterministic URL."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_put_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        self.calls.append(
            {
                "bucket": bucket,
                "object_name": object_name,
                "content_type": content_type,
                "expires_in": expires_in,
            }
        )
        return (
            f"https://storage.googleapis.com/{bucket}/{object_name}?signed=stub",
            datetime(2026, 5, 9, 23, 30, 0, tzinfo=UTC),
        )

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        # Not exercised by the presign tests; the identify tests use their
        # own stub.
        raise NotImplementedError


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch, uid: str = _FIREBASE_UID) -> None:
    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": uid, "email": "kid@example.com"}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client(
    fake_session: AsyncMock,
    *,
    signer: SignedUrlGenerator | None = None,
) -> Iterator[TestClient]:
    app = create_app(
        Settings(env="local", app_version="test", photos_bucket="dragonfly-photos-test")
    )
    if signer is not None:
        app.state.signed_url_generator = signer

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def stub_signer() -> _StubSignedUrlGenerator:
    return _StubSignedUrlGenerator()


@pytest.fixture
def photos_client(
    fake_session: AsyncMock,
    stub_signer: _StubSignedUrlGenerator,
) -> Iterator[TestClient]:
    yield from _build_client(fake_session, signer=stub_signer)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _wire_user_lookup(fake_session: AsyncMock, user: models.User | None) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    fake_session.execute = AsyncMock(return_value=user_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------


def test_presign_requires_bearer_token(photos_client: TestClient) -> None:
    response = photos_client.post("/v1/photos/presign", json={})
    assert response.status_code == 401


def test_presign_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_user_lookup(fake_session, None)

    response = photos_client.post(
        "/v1/photos/presign",
        json={},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_presign_returns_signed_url_and_inserts_photo_row(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    stub_signer: _StubSignedUrlGenerator,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_user_lookup(fake_session, _user_row())

    response = photos_client.post(
        "/v1/photos/presign",
        json={"content_type": "image/jpeg"},
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["bucket"] == "dragonfly-photos-test"
    assert body["object_name"].startswith("pending/")
    assert body["object_name"].endswith(".jpg")
    assert body["content_type"] == "image/jpeg"
    assert body["upload_url"].startswith("https://storage.googleapis.com/")
    assert body["expires_at"]  # ISO timestamp present

    # Signer was called with a 15-minute TTL and matching object_name.
    assert len(stub_signer.calls) == 1
    call = stub_signer.calls[0]
    assert call["bucket"] == "dragonfly-photos-test"
    assert call["object_name"] == body["object_name"]
    assert call["content_type"] == "image/jpeg"
    assert cast(timedelta, call["expires_in"]) == timedelta(minutes=15)

    # A Photo row was added with status=pending and matching keys.
    fake_session.add.assert_called_once()
    photo: models.Photo = fake_session.add.call_args.args[0]
    assert isinstance(photo, models.Photo)
    assert photo.status == "pending"
    assert photo.bucket == "dragonfly-photos-test"
    assert photo.object_name == body["object_name"]
    assert photo.user_id == _USER_ID
    assert photo.content_type == "image/jpeg"

    fake_session.commit.assert_awaited_once()


def test_presign_rejects_unsupported_content_type(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_user_lookup(fake_session, _user_row())

    response = photos_client.post(
        "/v1/photos/presign",
        json={"content_type": "image/png"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422  # pydantic validation
    fake_session.add.assert_not_called()
