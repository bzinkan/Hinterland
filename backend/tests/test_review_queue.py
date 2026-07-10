"""Tests for /v1/review-queue list / approve / reject endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

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
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        del expected_size, expected_sha256
        self.copy_calls.append((src_bucket, src_object, dst_bucket, dst_object))

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        self.delete_calls.append((bucket, object_name))

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat shim that delegates to the shared helper."""
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID, role="parent", group_id=_GROUP_ID)


def _build_client(
    fake_session: AsyncMock,
    *,
    storage: _StubStorage | None = None,
    inat_submit_enabled: bool = False,
) -> Iterator[TestClient]:
    app = create_app(
        Settings(env="local", app_version="test", inat_submit_enabled=inat_submit_enabled)
    )
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
        bucket="hinterland-photos-test",
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
    approve_outbox: bool = False,
    approve_obs_only: bool = False,
) -> None:
    """Sequence the session.execute side_effects for the approve / reject flows.

    Both flows: user lookup -> review lookup -> membership check. Approval then
    reads the photo. Rejection resolves the subject without a row lock, takes
    the user advisory lock, and only then locks review/photo/observation rows.

    Approve flow with outbox writes (Risk 0002 transactional outbox): when
    ``review.observation_id`` is set, the handler also runs
    ``select(Observation)`` and (after the post-commit enqueue) an
    ``update(InatSubmitOutbox)``. Pass ``approve_outbox=True`` to wire
    those two extra side_effects.

    Reject flow: when ``observation`` is passed, the handler tombstones it,
    acquires the per-user rebuild lock, and coalesces/creates a rebuild job.
    """
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

    subject_result = MagicMock()
    subject_result.scalar_one_or_none = MagicMock(
        return_value=observation.user_id if observation is not None else None
    )

    lock_result = MagicMock()
    rebuild_result = MagicMock()
    rebuild_result.scalar_one_or_none = MagicMock(return_value=None)
    outbox_update_result = MagicMock()

    side_effects: list[Any] = [user_result, review_result, membership_result]
    if approve_outbox:
        # Approve flow with inat_submit_enabled=True: select(Observation)
        # + update(InatSubmitOutbox).
        side_effects.extend([photo_result, obs_result, outbox_update_result])
    elif approve_obs_only:
        # Approve flow with Option B inat_submit_enabled=False: only
        # the select(Observation) lookup; no outbox write.
        side_effects.extend([photo_result, obs_result])
    elif observation is not None:
        # Reject flow: subject read, outer advisory lock, locked review/photo/
        # observation, then enqueue_rebuild's re-entrant lock and active lookup.
        side_effects.extend(
            [
                subject_result,
                lock_result,
                review_result,
                photo_result,
                obs_result,
                MagicMock(),
                rebuild_result,
            ]
        )
    else:
        # Approval/error paths still read the photo after authorization.
        side_effects.append(photo_result)
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()
    fake_session.flush = AsyncMock()
    fake_session.add = MagicMock()


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
    """Happy path (with `inat_submit_enabled=True`): flips
    observation.moderation_status='clean', inserts an InatSubmitOutbox
    row, and attempts a Service Bus enqueue. Mock the enqueue helper
    so the test does not touch Azure.

    Option B default (`inat_submit_enabled=False`) is covered by the
    separate ``test_approve_happy_path_option_b_skips_outbox`` test
    below."""
    from app.inat.enqueue import InatEnqueueResult

    _stub_token_verifier(monkeypatch)

    enqueue_calls: list[str] = []

    async def fake_enqueue(observation_id: str, *, settings: object) -> InatEnqueueResult:
        enqueue_calls.append(observation_id)
        return InatEnqueueResult(success=True)

    monkeypatch.setattr("app.api.routes.review_queue.enqueue_inat_submit", fake_enqueue)

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
        approve_outbox=True,
    )
    fake_session.add = MagicMock()
    storage = _StubStorage()

    for client in _build_client(fake_session, storage=storage, inat_submit_enabled=True):
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
            "hinterland-photos-test",
            f"quarantine/{_PHOTO_ID}.jpg",
            "hinterland-photos-test",
            f"observations/{_PHOTO_ID}.jpg",
        )
    ]
    assert storage.delete_calls == [("hinterland-photos-test", f"quarantine/{_PHOTO_ID}.jpg")]
    assert photo.status == "clean"
    assert photo.object_name == f"observations/{_PHOTO_ID}.jpg"
    assert photo.canonical_object_name == photo.object_name
    assert review.status == "approved"
    assert review.reviewer_user_id == _USER_ID

    # Observation flipped + outbox row inserted in the same transaction.
    assert obs.moderation_status == "clean"
    assert obs.moderation_source == "adult"
    assert obs.moderation_policy_version == "adult-review-v1"
    added = fake_session.add.call_args.args[0]
    assert isinstance(added, models.InatSubmitOutbox)
    assert added.observation_id == _OBS_ID
    assert added.status == "pending"

    # Enqueue was attempted with the right observation id.
    assert enqueue_calls == [_OBS_ID]

    # Two commits: the in-transaction outbox commit + the post-enqueue
    # status update commit.
    assert fake_session.commit.await_count == 2


def test_approve_happy_path_option_b_skips_outbox(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Option B default (`inat_submit_enabled=False`): the approve handler
    still flips observation.moderation_status='clean', but writes NO
    `inat_submit_outbox` row and attempts NO Service Bus enqueue. The
    observation stays inside Hinterland until the kid claims it via the
    Phase 3 age-13 flow."""
    _stub_token_verifier(monkeypatch)

    enqueue_calls: list[str] = []

    async def fake_enqueue(observation_id: str, *, settings: object) -> object:
        enqueue_calls.append(observation_id)
        # Should never be called -- the handler short-circuits before it.
        raise AssertionError("enqueue should not be invoked under Option B default")

    monkeypatch.setattr("app.api.routes.review_queue.enqueue_inat_submit", fake_enqueue)

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
        approve_obs_only=True,
    )
    fake_session.add = MagicMock()
    storage = _StubStorage()

    # `inat_submit_enabled` defaults to False; build_client mirrors that.
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/review-queue/{_REVIEW_ID}/approve",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["photo_status"] == "clean"

    # Photo + review state still flipped.
    assert photo.status == "clean"
    assert review.status == "approved"
    # Observation flipped but NO outbox row added.
    assert obs.moderation_status == "clean"
    assert obs.moderation_source == "adult"
    assert obs.moderation_policy_version == "adult-review-v1"
    fake_session.add.assert_not_called()
    # Enqueue never attempted.
    assert enqueue_calls == []
    # One commit only: the in-transaction approve commit (no outbox-status
    # update commit because no enqueue was attempted).
    assert fake_session.commit.await_count == 1


# ---------------------------------------------------------------------------
# POST /v1/review-queue/{id}/reject
# ---------------------------------------------------------------------------


def test_reject_tombstones_observation_and_queues_rebuild(
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
    assert photo.attachment_status == "deleted"
    assert review.status == "rejected"
    assert review.reviewer_user_id == _USER_ID
    assert obs.moderation_status == "rejected"
    assert obs.moderation_source == "adult"
    assert obs.moderation_policy_version == "adult-review-v1"
    assert obs.rejected_at is not None
    rebuild = fake_session.add.call_args.args[0]
    assert isinstance(rebuild, models.DerivedStateRebuild)
    assert rebuild.user_id == obs.user_id
    assert rebuild.trigger_observation_id == obs.id
    fake_session.commit.assert_awaited_once()
    statements = [str(call.args[0]) for call in fake_session.execute.await_args_list]
    advisory_index = next(
        index for index, statement in enumerate(statements) if "pg_advisory_xact_lock" in statement
    )
    review_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if "review_queue" in statement and "FOR UPDATE" in statement
    )
    assert advisory_index < review_lock_index
