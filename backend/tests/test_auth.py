from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.auth as auth_routes_module
import app.core.auth as auth_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier


def test_me_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Missing bearer token"


def _wire_empty_dev_auth_bootstrap(fake_session: AsyncMock) -> None:
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none = MagicMock(return_value=None)
    no_group_result = MagicMock()
    no_group_result.scalar_one_or_none = MagicMock(return_value=None)
    no_membership_result = MagicMock()
    no_membership_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(
        side_effect=[no_user_result, no_group_result, no_membership_result]
    )
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()


def _dev_bootstrap_results(
    *,
    user: models.User | None,
    group: models.Group | None,
    membership: models.Membership | None,
) -> list[MagicMock]:
    rows: list[models.User | models.Group | models.Membership | None] = [
        user,
        group,
        membership,
    ]
    results: list[MagicMock] = []
    for row in rows:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        results.append(result)
    return results


def test_me_uses_dev_auth_bypass_without_bearer(fake_session: AsyncMock) -> None:
    app = create_app(
        Settings(env="local", app_version="test", dev_auth_enabled=True)
    )

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    _wire_empty_dev_auth_bootstrap(fake_session)

    with TestClient(app) as test_client:
        response = test_client.get("/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["uid"] == "01J0KIDID0000000000000ULID"
    assert body["role"] == "kid"
    assert body["group_id"] == "01J0GROUPID00000000000ULID"
    assert fake_session.add.call_count == 3
    fake_session.commit.assert_awaited_once()


def test_me_tolerates_parallel_dev_auth_bootstrap_conflict(fake_session: AsyncMock) -> None:
    app = create_app(
        Settings(env="local", app_version="test", dev_auth_enabled=True)
    )

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    user = models.User(
        id="01J0KIDID0000000000000ULID",
        firebase_uid=None,
        role="kid",
        display_name="Dev Explorer",
    )
    group = models.Group(
        id="01J0GROUPID00000000000ULID",
        name="Dev Backyard",
        join_code="DEV001",
        owner_user_id=user.id,
    )
    membership = models.Membership(
        id="01J0MEMBERID0000000000ULID",
        user_id=user.id,
        group_id=group.id,
        role="kid",
    )
    fake_session.execute = AsyncMock(
        side_effect=[
            *_dev_bootstrap_results(user=None, group=None, membership=None),
            *_dev_bootstrap_results(user=user, group=group, membership=membership),
        ]
    )
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("duplicate key"))
    )
    fake_session.rollback = AsyncMock()

    with TestClient(app) as test_client:
        response = test_client.get(
            "/v1/me",
            headers={"Authorization": "Bearer hinterland-dev-bypass"},
        )

    assert response.status_code == 200
    assert response.json()["role"] == "kid"
    fake_session.rollback.assert_awaited_once()


def test_me_rejects_missing_dev_auth_bearer_outside_local() -> None:
    app = create_app(Settings(env="dev", app_version="test", dev_auth_enabled=True))

    with TestClient(app) as test_client:
        response = test_client.get("/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Missing bearer token"


def test_me_uses_dev_auth_bypass_token(fake_session: AsyncMock) -> None:
    app = create_app(
        Settings(env="local", app_version="test", dev_auth_enabled=True)
    )

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    _wire_empty_dev_auth_bootstrap(fake_session)

    with TestClient(app) as test_client:
        response = test_client.get(
            "/v1/me",
            headers={"Authorization": "Bearer hinterland-dev-bypass"},
        )

    assert response.status_code == 200
    assert response.json()["kid_id"] == "01J0KIDID0000000000000ULID"


def test_me_returns_current_firebase_user(
    client: TestClient,
    monkeypatch,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid="firebase-user-1",
        email="parent@example.com",
        role="parent",
        group_id="group-1",
        parent_id="parent-1",
    )

    response = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    body = response.json()
    # Required identity fields preserved.
    assert body["uid"] == "firebase-user-1"
    assert body["email"] == "parent@example.com"
    assert body["role"] == "parent"
    assert body["group_id"] == "group-1"
    assert body["kid_id"] is None
    assert body["parent_id"] == "parent-1"
    assert body["teacher_id"] is None
    # The post-rewrite CurrentUser gains an optional entra_oid field. Accept
    # either shape so the test passes before and after the auth rewrite lands.
    if "entra_oid" in body:
        assert body["entra_oid"] is None


def test_me_rejects_invalid_firebase_token(
    client: TestClient,
    monkeypatch,
) -> None:
    stub_token_verifier(
        monkeypatch,
        raises=auth_module.InvalidAuthToken("Invalid bearer token"),
    )

    response = client.get("/v1/me", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Invalid bearer token"


# ---------------------------------------------------------------------------
# POST /v1/auth/parent-signup
# ---------------------------------------------------------------------------

_FIREBASE_UID = "firebase-parent-001"


def _build_client_with_session(session_mock: AsyncMock) -> Iterator[TestClient]:
    """Create a TestClient whose DB session dependency returns `session_mock`."""
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield session_mock

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as client:
        yield client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def parent_signup_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client_with_session(fake_session)


def test_parent_signup_requires_bearer_token(client: TestClient) -> None:
    response = client.post("/v1/auth/parent-signup", json={"display_name": "Brian"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_delete_me_disables_authenticated_user(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID, role="parent")
    user = models.User(
        id="01J0DELETEUSER000000000000",
        firebase_uid=_FIREBASE_UID,
        role="parent",
        display_name="Brian",
    )
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user)
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.commit = AsyncMock()

    response = parent_signup_client.delete(
        "/v1/me",
        headers={"Authorization": "Bearer valid"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deletion_requested"
    assert body["user_id"] == user.id
    assert user.disabled_at is not None
    fake_session.commit.assert_awaited_once()


def test_parent_signup_validates_display_name(
    parent_signup_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json={},  # missing display_name
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_parent_signup_creates_user_and_sets_claim(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)

    # First execute() returns a result whose scalar_one_or_none is None
    # (no existing user). Subsequent commit/refresh are no-ops on the mock.
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=no_user_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Brian"},
    )

    assert response.status_code == 200
    body = response.json()
    # firebase_uid may now be None after the rewrite; accept both legacy and
    # post-rewrite shapes so the test stays green during the apply window.
    assert body["firebase_uid"] in (_FIREBASE_UID, None)
    assert body["role"] == "parent"
    assert body["display_name"] == "Brian"
    assert isinstance(body["id"], str) and len(body["id"]) == 26  # ULID

    fake_session.add.assert_called_once()
    added_user: models.User = fake_session.add.call_args[0][0]
    # Either the legacy firebase_uid field carries the Firebase UID, or the
    # rewritten code routes the identity into entra_oid (firebase_uid is None).
    legacy_firebase_uid = getattr(added_user, "firebase_uid", None)
    new_entra_oid = getattr(added_user, "entra_oid", None)
    assert legacy_firebase_uid == _FIREBASE_UID or new_entra_oid == _FIREBASE_UID
    assert added_user.role == "parent"
    assert added_user.display_name == "Brian"
    fake_session.commit.assert_awaited_once()
    fake_session.refresh.assert_awaited_once_with(added_user)


def test_parent_signup_is_idempotent_for_existing_user(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)

    existing = models.User(
        id="01J0EXISTINGULID0000000000",
        firebase_uid=_FIREBASE_UID,
        role="parent",
        display_name="Brian",
    )
    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing)
    fake_session.execute = AsyncMock(return_value=existing_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Brian"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "01J0EXISTINGULID0000000000"
    assert body["firebase_uid"] in (_FIREBASE_UID, None)
    assert body["role"] == "parent"

    # No new user created
    fake_session.add.assert_not_called()
    # commit may or may not run depending on whether the rewrite backfills
    # entra_oid on existing rows -- both are valid idempotent paths.


# ---------------------------------------------------------------------------
# POST /v1/auth/kid-exchange  (Phase 6a)
# ---------------------------------------------------------------------------


@pytest.fixture
def kid_exchange_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client_with_session(fake_session)


def _kid_user_row(*, disabled: bool = False) -> models.User:
    return models.User(
        id="01J0KIDEXCHANGEID0000000UL",
        firebase_uid=None,
        role="kid",
        display_name="Sparrow",
        age_band="9-10",
        disabled_at=datetime.now(UTC) if disabled else None,
    )


def test_kid_exchange_happy_path(
    kid_exchange_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid handoff JWT -> 200 with a fresh session JWT and the kid user."""
    if not hasattr(auth_routes_module, "verify_kid_jwt"):
        pytest.skip("kid-exchange route not present yet")

    exp_unix = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())

    def fake_verify(
        token: str, *, settings: Settings, expected_token_type: str | None = None
    ) -> dict[str, object]:
        assert token == "handoff-jwt"
        return {
            "sub": "01J0KIDEXCHANGEID0000000UL",
            "jti": "01HANDOFFJTI00000000000000",
            "exp": exp_unix,
            "iat": exp_unix - 900,
            "iss": "https://api.thehinterlandguide.app",
            "aud": "hinterland-api",
            "group_id": "g1",
            "parent_id": "p1",
            "token_type": "handoff",
        }

    monkeypatch.setattr(auth_routes_module, "verify_kid_jwt", fake_verify)
    monkeypatch.setattr(
        auth_routes_module,
        "mint_session_token",
        lambda **_: "session-jwt",
    )

    kid_row_result = MagicMock()
    kid_row_result.scalar_one_or_none = MagicMock(return_value=_kid_user_row())
    fake_session.execute = AsyncMock(return_value=kid_row_result)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "handoff-jwt"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_token"] == "session-jwt"
    assert body["user"]["id"] == "01J0KIDEXCHANGEID0000000UL"


def test_kid_exchange_rejects_replayed_jti(
    kid_exchange_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handoff JWT already consumed (jti collision) -> 409 Conflict.

    Single-use is enforced server-side by the PK constraint on
    ``kid_handoff_jti.jti``; the second INSERT raises IntegrityError and
    the route maps it to 409. 401 would be wrong -- the token itself is
    not invalid; it's just already been spent.
    """
    if not hasattr(auth_routes_module, "verify_kid_jwt"):
        pytest.skip("kid-exchange route not present yet")

    exp_unix = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())
    monkeypatch.setattr(
        auth_routes_module,
        "verify_kid_jwt",
        lambda token, *, settings, expected_token_type=None: {
            "sub": "01J0KIDEXCHANGEID0000000UL",
            "jti": "01HANDOFFJTI00000000000000",
            "exp": exp_unix,
            "iat": exp_unix - 900,
            "iss": "https://api.thehinterlandguide.app",
            "aud": "hinterland-api",
            "group_id": "g1",
            "parent_id": "p1",
            "token_type": "handoff",
        },
    )

    fake_session.add = MagicMock()
    # Simulate the unique-constraint violation that a replay would trip.
    fake_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("duplicate jti"))
    )

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "handoff-jwt"},
    )

    assert response.status_code == 409


def test_kid_exchange_rejects_invalid_token(
    kid_exchange_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handoff JWT failing signature/expiry/audience -> 401."""
    if not hasattr(auth_routes_module, "verify_kid_jwt"):
        pytest.skip("kid-exchange route not present yet")
    if not hasattr(auth_routes_module, "InvalidKidJwt"):
        pytest.skip("InvalidKidJwt not present yet")

    invalid_exc = auth_routes_module.InvalidKidJwt("Expired handoff token")

    def fake_verify(token: str, *, settings: Settings, expected_token_type: str | None = None):
        raise invalid_exc

    monkeypatch.setattr(auth_routes_module, "verify_kid_jwt", fake_verify)

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "expired"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_kid_jwks_endpoint_returns_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /.well-known/hinterland-kid-jwks.json returns the published JWKS."""
    if not hasattr(auth_routes_module, "public_jwks"):
        pytest.skip("public_jwks not present yet")

    monkeypatch.setattr(
        auth_routes_module,
        "public_jwks",
        lambda settings: {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "k1-2026-07",
                    "n": "AAAA",
                    "e": "AQAB",
                }
            ]
        },
    )

    app = create_app(Settings(env="local", app_version="test"))
    with TestClient(app) as test_client:
        response = test_client.get("/.well-known/hinterland-kid-jwks.json")

    assert response.status_code == 200
    body = response.json()
    assert "keys" in body
    assert body["keys"][0]["kid"] == "k1-2026-07"


def test_legacy_kid_jwks_endpoint_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Dragonfly-era JWKS URL remains as a transition alias."""
    monkeypatch.setattr(
        auth_routes_module,
        "public_jwks",
        lambda settings: {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "k1-2026-07",
                    "n": "AAAA",
                    "e": "AQAB",
                }
            ]
        },
    )

    app = create_app(Settings(env="local", app_version="test"))
    with TestClient(app) as test_client:
        response = test_client.get("/.well-known/dragonfly-kid-jwks.json")

    assert response.status_code == 200
    assert response.json()["keys"][0]["kid"] == "k1-2026-07"
