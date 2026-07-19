from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.storage import SignedUrlGenerator
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_PARENT_A_ID = "01J0PARENTA00000000000ULID"
_PARENT_B_ID = "01J0PARENTB00000000000ULID"
_TEACHER_ID = "01J0TEACHER00000000000ULID"
_PEER_KID_ID = "01J0PEERKID00000000000ULID"
_SHARED_GROUP_ID = "01J0SHAREDGROUP0000000ULID"


class _StubSignedUrlGenerator:
    """Records the args it was called with and returns a deterministic URL."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.fetch_calls: list[tuple[str, str]] = []
        self.bytes_to_return = b"fake-jpeg-bytes"

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

    def put_required_headers(self, *, content_type: str) -> dict[str, str]:
        return {"Content-Type": content_type, "x-ms-blob-type": "BlockBlob"}

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        self.fetch_calls.append((bucket, object_name))
        return self.bytes_to_return

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

    def generate_get_url(
        self,
        *,
        bucket: str,
        object_name: str,
        expires_in: timedelta,
    ) -> tuple[str, datetime]:
        self.get_calls.append(
            {
                "bucket": bucket,
                "object_name": object_name,
                "expires_in": expires_in,
            }
        )
        return (
            f"https://storage.googleapis.com/{bucket}/{object_name}?signed=stub-get",
            datetime(2026, 5, 10, 23, 30, 0, tzinfo=UTC),
        )


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch, uid: str = _FIREBASE_UID) -> None:
    """Back-compat shim that delegates to the shared helper.

    Intentionally omits role/group_id -- the photos route's 403 path is
    exercised by the absent role claim.
    """
    stub_token_verifier(monkeypatch, uid=uid, email="kid@example.com", role=None, group_id=None)


def _build_client(
    fake_session: AsyncMock,
    *,
    signer: SignedUrlGenerator | None = None,
    inat_token: str = "test-token",
) -> Iterator[TestClient]:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            photos_bucket="hinterland-photos-test",
            inat_oauth_token=inat_token,
        )
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
    replay_result = MagicMock()
    replay_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(
        side_effect=[user_result, replay_result] if user is not None else [user_result]
    )


def _adult_row(*, user_id: str = _PARENT_A_ID, role: str = "parent") -> models.User:
    return models.User(
        id=user_id,
        firebase_uid=_FIREBASE_UID,
        role=role,
        display_name="Parent Name",
    )


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
    assert body["bucket"] == "hinterland-photos-test"
    assert body["object_name"].startswith("pending/uploads/")
    assert body["object_name"].endswith(".jpg")
    assert body["content_type"] == "image/jpeg"
    assert body["upload_headers"] == {
        "Content-Type": "image/jpeg",
        "x-ms-blob-type": "BlockBlob",
    }
    assert body["required_headers"] == body["upload_headers"]
    assert body["attachment_status"] == "reserved"
    assert body["upload_url"].startswith("https://storage.googleapis.com/")
    assert body["expires_at"]  # ISO timestamp present

    # Signer was called with a 15-minute TTL and matching object_name.
    assert len(stub_signer.calls) == 1
    call = stub_signer.calls[0]
    assert call["bucket"] == "hinterland-photos-test"
    assert call["object_name"] == body["object_name"]
    assert call["content_type"] == "image/jpeg"
    assert cast(timedelta, call["expires_in"]) == timedelta(minutes=15)

    # A Photo row was added with status=pending and matching keys.
    assert fake_session.add.call_count == 2
    photo: models.Photo = fake_session.add.call_args_list[0].args[0]
    assert isinstance(photo, models.Photo)
    assert photo.status == "pending"
    assert photo.attachment_status == "reserved"
    assert photo.submission_key is not None
    assert photo.bucket == "hinterland-photos-test"
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


def test_presign_replay_returns_same_reservation_and_fresh_sas(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    stub_signer: _StubSignedUrlGenerator,
) -> None:
    _stub_token_verifier(monkeypatch)
    key = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    photo = _photo_row(owner=_USER_ID, status="pending")
    photo.attachment_status = "reserved"
    record = models.ObservationIdempotency(
        user_id=_USER_ID,
        idempotency_key=key,
        operation="photo_presign",
        request_hash="fd824fcedd245e55871c74cb48ebbad02dab9bc4b9370433b609d6022ece7a73",
        resource_id=photo.id,
    )
    results = []
    for value in (_user_row(), record, photo):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=value)
        results.append(result)
    fake_session.execute = AsyncMock(side_effect=results)
    fake_session.add = MagicMock()

    response = photos_client.post(
        "/v1/photos/presign",
        json={"content_type": "image/jpeg"},
        headers={"Authorization": "Bearer fake", "Idempotency-Key": key},
    )

    assert response.status_code == 201
    assert response.json()["photo_id"] == photo.id
    assert response.json()["upload_headers"]["x-ms-blob-type"] == "BlockBlob"
    fake_session.add.assert_not_called()
    assert len(stub_signer.calls) == 1


def test_presign_rejects_idempotency_key_reuse_with_different_request(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    key = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    record = models.ObservationIdempotency(
        user_id=_USER_ID,
        idempotency_key=key,
        operation="photo_presign",
        request_hash="different",
        resource_id="01J0PHOTOID00000000000ULID",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    record_result = MagicMock()
    record_result.scalar_one_or_none = MagicMock(return_value=record)
    fake_session.execute = AsyncMock(side_effect=[user_result, record_result])

    response = photos_client.post(
        "/v1/photos/presign",
        json={"content_type": "image/jpeg"},
        headers={"Authorization": "Bearer fake", "Idempotency-Key": key},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# GET /v1/photos/{id}/url
# ---------------------------------------------------------------------------


def _photo_row(*, owner: str = _USER_ID, status: str = "clean") -> models.Photo:
    return models.Photo(
        id="01J0PHOTOID00000000000ULID",
        user_id=owner,
        bucket="hinterland-photos-test",
        object_name="observations/01J0PHOTOID00000000000ULID.jpg",
        status=status,
        attachment_status="attached",
        content_type="image/jpeg",
    )


def _wire_photo_url(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    photo: models.Photo | None,
    parent_manages_child: bool = False,
    moderation_status: str | None = None,
    observation_group_id: str = _SHARED_GROUP_ID,
    observation_owner_id: str | None = None,
    revocation_active: bool = False,
) -> None:
    """Wire user, photo, observation state, then canonical-parent authority."""
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    owner_id = observation_owner_id or (photo.user_id if photo is not None else "owner")
    observation_result = MagicMock()
    observation_result.one_or_none = MagicMock(
        return_value=(
            moderation_status or photo.status,
            observation_group_id,
            owner_id,
            revocation_active,
        )
        if photo is not None
        else None
    )
    managed_child_result = MagicMock()
    managed_child_result.scalar_one_or_none = MagicMock(
        return_value=owner_id if parent_manages_child else None
    )

    side_effects: list[object] = [user_result]
    if user is not None:
        side_effects.append(photo_result)
        if photo is not None:
            side_effects.append(observation_result)
        if photo is not None and photo.user_id != user.id and user.role == "parent":
            side_effects.append(managed_child_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


def test_photo_url_requires_bearer(photos_client: TestClient) -> None:
    response = photos_client.get("/v1/photos/x/url")
    assert response.status_code == 401


def test_photo_url_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(fake_session, user=None, photo=None)
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403


def test_photo_url_404_when_photo_missing(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(fake_session, user=_user_row(), photo=None)
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404


def test_photo_url_owner_caller_returns_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    stub_signer: _StubSignedUrlGenerator,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(fake_session, user=_user_row(), photo=_photo_row(owner=_USER_ID))
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://storage.googleapis.com/")
    assert body["expires_at"]

    # The server contract is deliberately one minute.
    assert stub_signer.get_calls[-1]["expires_in"] == timedelta(seconds=60)


def test_photo_url_active_revocation_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_user_row(),
        photo=_photo_row(owner=_USER_ID),
        revocation_active=True,
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404


@pytest.mark.parametrize("photo_status", ["pending", "quarantine", "deleted"])
def test_photo_url_kid_owner_cannot_read_non_clean_photo(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    photo_status: str,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_user_row(),
        photo=_photo_row(owner=_USER_ID, status=photo_status),
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404


def test_photo_url_pilot_private_observation_is_denied_even_if_photo_says_clean(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_user_row(),
        photo=_photo_row(owner=_USER_ID, status="clean"),
        moderation_status="pilot_private",
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404


def test_photo_url_unrelated_parent_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """An unrelated parent gets the same 404 as a missing photo."""
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_adult_row(user_id=_PARENT_B_ID),
        photo=_photo_row(owner="someone-else"),
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("photo_status", "moderation_status"),
    [("clean", "clean"), ("quarantine", "quarantine")],
)
def test_photo_url_canonical_parent_can_read_own_child_photo(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    photo_status: str,
    moderation_status: str,
) -> None:
    """Parent A can read both clean and review-held photos for their child."""
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_adult_row(user_id=_PARENT_A_ID),
        photo=_photo_row(owner=_USER_ID, status=photo_status),
        moderation_status=moderation_status,
        parent_manages_child=True,
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200

    authority_statement = fake_session.execute.await_args_list[-1].args[0]
    authority_sql = str(
        authority_statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "users.parent_user_id" in authority_sql
    assert f"users.parent_user_id = '{_PARENT_A_ID}'" in authority_sql
    assert f"users.id = '{_USER_ID}'" in authority_sql
    assert "memberships" not in authority_sql


@pytest.mark.parametrize(
    ("photo_status", "moderation_status"),
    [("clean", "clean"), ("quarantine", "quarantine")],
)
def test_photo_url_group_owner_parent_b_cannot_read_parent_a_child_photo(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
    photo_status: str,
    moderation_status: str,
) -> None:
    """Sharing the observation group never substitutes for parent ownership."""
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_adult_row(user_id=_PARENT_B_ID),
        photo=_photo_row(owner=_USER_ID, status=photo_status),
        moderation_status=moderation_status,
        observation_group_id=_SHARED_GROUP_ID,
        parent_manages_child=False,
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404

    authority_statement = fake_session.execute.await_args_list[-1].args[0]
    authority_sql = str(authority_statement.compile(dialect=postgresql.dialect()))
    assert "users.parent_user_id" in authority_sql
    assert "memberships" not in authority_sql
    observation_statement = fake_session.execute.await_args_list[2].args[0]
    observation_sql = str(observation_statement.compile(dialect=postgresql.dialect()))
    assert "observations.group_id" in observation_sql
    assert "memberships" not in observation_sql


def test_photo_url_teacher_in_same_group_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=_adult_row(user_id=_TEACHER_ID, role="teacher"),
        photo=_photo_row(owner=_USER_ID, status="clean"),
        observation_group_id=_SHARED_GROUP_ID,
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404
    assert fake_session.execute.await_count == 3


def test_photo_url_peer_kid_in_same_group_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_photo_url(
        fake_session,
        user=models.User(
            id=_PEER_KID_ID,
            firebase_uid=_FIREBASE_UID,
            role="kid",
            display_name="Peer Kid",
        ),
        photo=_photo_row(owner=_USER_ID),
        observation_group_id=_SHARED_GROUP_ID,
    )
    response = photos_client.get("/v1/photos/x/url", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 404
    assert fake_session.execute.await_count == 3


def test_delete_reserved_photo_tombstones_and_returns_204(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row(owner=_USER_ID, status="pending")
    photo.attachment_status = "reserved"
    _wire_photo_url(fake_session, user=_user_row(), photo=photo)
    fake_session.commit = AsyncMock()

    response = photos_client.delete(
        f"/v1/photos/{photo.id}", headers={"Authorization": "Bearer fake"}
    )

    assert response.status_code == 204
    assert photo.attachment_status == "deleted"
    assert photo.status == "deleted"
    fake_session.commit.assert_awaited_once()


def test_delete_attached_photo_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
    photos_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row(owner=_USER_ID, status="pending")
    photo.attachment_status = "attached"
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    fake_session.execute = AsyncMock(side_effect=[user_result, photo_result])

    response = photos_client.delete(
        f"/v1/photos/{photo.id}", headers={"Authorization": "Bearer fake"}
    )
    assert response.status_code == 409
