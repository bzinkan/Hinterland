"""Tests for /v1/review-queue list / approve / reject endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
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

_FIREBASE_UID = "firebase-parent-001"
_USER_ID = "01J0PARENTID000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_REVIEW_ID = "01J0REVIEWID0000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_OBS_ID = "01J0OBSID00000000000000ULID"
_KID_ID = "01J0KIDID0000000000000ULID"


class _StubStorage:
    def __init__(self) -> None:
        self.copy_calls: list[tuple[str, str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        raise NotImplementedError

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


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": _FIREBASE_UID, "role": "parent", "group_id": _GROUP_ID}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client(
    fake_session: AsyncMock,
    *,
    storage: _StubStorage | None = None,
) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.signed_url_generator = storage if storage is not None else _StubStorage()

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _adult_user(role: str = "parent") -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role=role,
        display_name="Parent",
    )


def _kid_user() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid",
    )


def _review_row(review_status: str = "pending") -> models.ReviewQueueItem:
    return models.ReviewQueueItem(
        id=_REVIEW_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        observation_id=_OBS_ID,
        status=review_status,
        reason='{"adult":"LIKELY"}',
    )


def _photo_row(status: str = "quarantine") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id=_KID_ID,
        bucket="dragonfly-photos-test",
        object_name=f"quarantine/{_PHOTO_ID}.jpg",
        status=status,
        content_type="image/jpeg",
    )


def _observation_row() -> models.Observation:
    return models.Observation(
        id=_OBS_ID,
        user_id=_KID_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
    )


# ---------------------------------------------------------------------------
# GET /v1/review-queue
# ---------------------------------------------------------------------------


def _wire_list(
    fake_session: AsyncMock,
    *,
    user: models.User,
    group_ids: list[str],
    rows: list[models.ReviewQueueItem],
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    groups_result = MagicMock()
    groups_result.all = MagicMock(return_value=[(gid,) for gid in group_ids])

    list_result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=rows)
    list_result.scalars = MagicMock(return_value=scalars_result)

    fake_session.execute = AsyncMock(side_effect=[user_result, groups_result, list_result])


def test_list_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.get("/v1/review-queue")
        assert response.status_code == 401


def test_list_403_when_user_role_is_kid(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_list(fake_session, user=_kid_user(), group_ids=[], rows=[])

    for client in _build_client(fake_session):
        response = client.get(
            "/v1/review-queue",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 403


def test_list_returns_empty_when_no_adult_groups(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_list(fake_session, user=_adult_user(), group_ids=[], rows=[])

    for client in _build_client(fake_session):
        response = client.get(
            "/v1/review-queue",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["next_cursor"] is None


def test_list_returns_pending_items_for_caller_groups(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    review = _review_row()
    review.created_at = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    _wire_list(fake_session, user=_adult_user(), group_ids=[_GROUP_ID], rows=[review])

    for client in _build_client(fake_session):
        response = client.get(
            "/v1/review-queue",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["id"] == _REVIEW_ID
        assert item["status"] == "pending"
        assert item["reason"] == '{"adult":"LIKELY"}'


# ---------------------------------------------------------------------------
# POST /v1/review-queue/{id}/approve
# ---------------------------------------------------------------------------


def _wire_resolve(
    fake_session: AsyncMock,
    *,
    user: models.User,
    review: models.ReviewQueueItem | None,
    membership_present: bool,
    photo: models.Photo | None,
    observation: models.Observation | None = None,
) -> None:
    """Approve flow: user lookup -> review lookup -> membership check -> photo lookup.
    Reject flow adds an observation lookup at the end + an UPDATE result."""
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    review_result = MagicMock()
    review_result.scalar_one_or_none = MagicMock(return_value=review)

    membership_result = MagicMock()
    membership_result.scalar_one_or_none = MagicMock(
        return_value="membership-id" if membership_present else None
    )

    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=observation)

    update_result = MagicMock()

    side_effects: list[Any] = [user_result, review_result, membership_result, photo_result]
    if observation is not None:
        side_effects.extend([obs_result, update_result])
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


def test_approve_404_when_review_missing(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_resolve(
        fake_session,
        user=_adult_user(),
        review=None,
        membership_present=True,
        photo=None,
    )
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/approve",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404


def test_approve_404_when_caller_not_in_group(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_resolve(
        fake_session,
        user=_adult_user(),
        review=_review_row(),
        membership_present=False,
        photo=None,
    )
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/approve",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404


def test_approve_409_when_already_resolved(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_resolve(
        fake_session,
        user=_adult_user(),
        review=_review_row(review_status="approved"),
        membership_present=True,
        photo=None,
    )
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/approve",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 409


def test_approve_happy_path_moves_photo_back_and_marks_review(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    review = _review_row()
    photo = _photo_row()
    _wire_resolve(
        fake_session,
        user=_adult_user(),
        review=review,
        membership_present=True,
        photo=photo,
    )
    storage = _StubStorage()

    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/approve",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["photo_status"] == "clean"

    # Photo was moved quarantine -> observations
    assert storage.copy_calls == [
        (
            "dragonfly-photos-test",
            f"quarantine/{_PHOTO_ID}.jpg",
            "dragonfly-photos-test",
            f"observations/{_PHOTO_ID}.jpg",
        )
    ]
    assert storage.delete_calls == [("dragonfly-photos-test", f"quarantine/{_PHOTO_ID}.jpg")]
    assert photo.status == "clean"
    assert photo.object_name == f"observations/{_PHOTO_ID}.jpg"
    assert review.status == "approved"
    assert review.reviewer_user_id == _USER_ID
    fake_session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /v1/review-queue/{id}/reject
# ---------------------------------------------------------------------------


def test_reject_decrements_counter_and_marks_photo_deleted(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    review = _review_row()
    photo = _photo_row()
    obs = _observation_row()
    _wire_resolve(
        fake_session,
        user=_adult_user(),
        review=review,
        membership_present=True,
        photo=photo,
        observation=obs,
    )

    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/reject",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["photo_status"] == "deleted"

    assert photo.status == "deleted"
    assert review.status == "rejected"
    assert review.reviewer_user_id == _USER_ID
    fake_session.commit.assert_awaited_once()
