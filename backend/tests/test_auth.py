from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.auth as auth_routes_module
import app.core.auth as auth_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app


def test_me_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Missing bearer token"


def test_me_returns_current_firebase_user(
    client: TestClient,
    monkeypatch,
) -> None:
    def fake_verify_id_token(token: str, settings: Settings) -> dict[str, object]:
        assert token == "valid-token"
        assert settings.env == "local"
        return {
            "uid": "firebase-user-1",
            "email": "parent@example.com",
            "role": "parent",
            "group_id": "group-1",
            "parent_id": "parent-1",
        }

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify_id_token)

    response = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    assert response.json() == {
        "uid": "firebase-user-1",
        "email": "parent@example.com",
        "role": "parent",
        "group_id": "group-1",
        "kid_id": None,
        "parent_id": "parent-1",
        "teacher_id": None,
    }


def test_me_rejects_invalid_firebase_token(
    client: TestClient,
    monkeypatch,
) -> None:
    def fake_verify_id_token(token: str, settings: Settings) -> dict[str, object]:
        raise auth_module.InvalidAuthToken("Invalid bearer token")

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify_id_token)

    response = client.get("/v1/me", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Invalid bearer token"


# ---------------------------------------------------------------------------
# POST /v1/auth/parent-signup
# ---------------------------------------------------------------------------

_FIREBASE_UID = "firebase-parent-001"


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch, uid: str = _FIREBASE_UID) -> None:
    """Replace the Firebase verifier with one that accepts any token for `uid`."""

    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": uid, "email": "parent@example.com"}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _stub_set_claims(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    """Replace `set_firebase_custom_claims` with a recorder; return the call log."""
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_set(uid: str, claims: dict[str, object], settings: Settings) -> None:
        calls.append((uid, claims))

    monkeypatch.setattr(auth_routes_module, "set_firebase_custom_claims", fake_set)
    return calls


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


def test_parent_signup_validates_display_name(
    parent_signup_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    _stub_set_claims(monkeypatch)

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
    _stub_token_verifier(monkeypatch)
    set_claims_calls = _stub_set_claims(monkeypatch)

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
    assert body["firebase_uid"] == _FIREBASE_UID
    assert body["role"] == "parent"
    assert body["display_name"] == "Brian"
    assert isinstance(body["id"], str) and len(body["id"]) == 26  # ULID

    fake_session.add.assert_called_once()
    added_user: models.User = fake_session.add.call_args[0][0]
    assert added_user.firebase_uid == _FIREBASE_UID
    assert added_user.role == "parent"
    assert added_user.display_name == "Brian"
    fake_session.commit.assert_awaited_once()
    fake_session.refresh.assert_awaited_once_with(added_user)

    assert set_claims_calls == [(_FIREBASE_UID, {"role": "parent"})]


def test_parent_signup_is_idempotent_for_existing_user(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    set_claims_calls = _stub_set_claims(monkeypatch)

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
    assert body["firebase_uid"] == _FIREBASE_UID
    assert body["role"] == "parent"

    # No new user created
    fake_session.add.assert_not_called()
    fake_session.commit.assert_not_called()
    fake_session.refresh.assert_not_called()

    # Custom claim still re-set (recovers from drift)
    assert set_claims_calls == [(_FIREBASE_UID, {"role": "parent"})]
