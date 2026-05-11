"""Tests for POST /internal/moderation/process."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from app.moderation.provider import ModerationResult, ModerationUnavailable

_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_BUCKET = "dragonfly-photos-test"
_OBJECT_NAME = f"pending/{_PHOTO_ID}.jpg"


class _StubStorage:
    def __init__(self) -> None:
        self.copy_calls: list[tuple[str, str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return b"jpeg-bytes"

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        self.copy_calls.append((src_bucket, src_object, dst_bucket, dst_object))

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        self.delete_calls.append((bucket, object_name))

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


class _StubModerator:
    def __init__(self, result: ModerationResult | Exception) -> None:
        self._result = result

    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _build_client(
    fake_session: AsyncMock,
    *,
    storage: _StubStorage,
    moderator: _StubModerator,
) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.signed_url_generator = storage
    app.state.moderator = moderator

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _wire_session(
    fake_session: AsyncMock,
    *,
    photo: models.Photo | None,
) -> None:
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(side_effect=[photo_result, obs_result])
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()


def _photo_row() -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id="user-id",
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        status="pending",
        content_type="image/jpeg",
    )


# ---------------------------------------------------------------------------


def test_process_clean_path_returns_200(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, photo=_photo_row())
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    for client in _build_client(fake_session, storage=storage, moderator=moderator):
        response = client.post(
            "/internal/moderation/process",
            json={"bucket": _BUCKET, "object_name": _OBJECT_NAME},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "clean"
        assert body["new_object_name"] == f"observations/{_PHOTO_ID}.jpg"


def test_process_returns_404_when_photo_missing(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, photo=None)
    for client in _build_client(
        fake_session,
        storage=_StubStorage(),
        moderator=_StubModerator(ModerationResult(decision="clean")),
    ):
        response = client.post(
            "/internal/moderation/process",
            json={"bucket": _BUCKET, "object_name": _OBJECT_NAME},
        )
        assert response.status_code == 404


def test_process_returns_503_when_moderation_unavailable(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, photo=_photo_row())
    moderator = _StubModerator(ModerationUnavailable("vision down"))
    for client in _build_client(fake_session, storage=_StubStorage(), moderator=moderator):
        response = client.post(
            "/internal/moderation/process",
            json={"bucket": _BUCKET, "object_name": _OBJECT_NAME},
        )
        # 503 lets Eventarc retry per docs/moderation.md outage contract.
        assert response.status_code == 503


def test_process_skipped_for_non_pending_object(fake_session: AsyncMock) -> None:
    # No DB lookup happens -- the object name short-circuits.
    fake_session.execute = AsyncMock(side_effect=[])
    for client in _build_client(
        fake_session,
        storage=_StubStorage(),
        moderator=_StubModerator(ModerationResult(decision="clean")),
    ):
        response = client.post(
            "/internal/moderation/process",
            json={"bucket": _BUCKET, "object_name": "observations/whatever.jpg"},
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "skipped"
