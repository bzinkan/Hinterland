"""Integration tests for POST /v1/auth/kid-exchange.

The endpoint accepts a Hinterland RS256 handoff JWT in the request body
(no Authorization header), verifies it, atomically claims the jti via
``kid_handoff_jti``, and mints a long-lived session JWT for the kid's
device.

The tests stub ``verify_dragonfly_jwt`` and ``mint_session_token`` so the
endpoint logic can be exercised without real Azure Key Vault credentials.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.auth as auth_routes_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_KID_USER_ID = "01J0KIDEXCHANGEID0000000UL"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_PARENT_ID = "01J0PARENTID0000000000ULID"
_HANDOFF_JTI = "01HANDOFFJTI00000000000000"


def _build_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def kid_exchange_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _kid_row(*, disabled: bool = False) -> models.User:
    return models.User(
        id=_KID_USER_ID,
        firebase_uid=None,
        role="kid",
        display_name="Sparrow",
        age_band="9-10",
        disabled_at=datetime.now(UTC) if disabled else None,
    )


def _valid_handoff_claims(*, exp_offset_seconds: int = 900) -> dict[str, object]:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=exp_offset_seconds)
    return {
        "sub": _KID_USER_ID,
        "jti": _HANDOFF_JTI,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "https://api.dragonfly-app.net",
        "aud": "dragonfly-api",
        "group_id": _GROUP_ID,
        "parent_id": _PARENT_ID,
        "token_type": "handoff",
        "role": "kid",
    }


def _route_ready() -> bool:
    return hasattr(auth_routes_module, "verify_dragonfly_jwt") and hasattr(
        auth_routes_module, "mint_session_token"
    )


def test_kid_exchange_happy_path(
    kid_exchange_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid handoff JWT -> 200 with a session token + kid user row."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")

    monkeypatch.setattr(
        auth_routes_module,
        "verify_dragonfly_jwt",
        lambda token, *, settings, expected_token_type=None: _valid_handoff_claims(),
    )
    monkeypatch.setattr(
        auth_routes_module,
        "mint_session_token",
        lambda **kwargs: "fresh-session-jwt",
    )

    kid_lookup = MagicMock()
    kid_lookup.scalar_one_or_none = MagicMock(return_value=_kid_row())
    fake_session.execute = AsyncMock(return_value=kid_lookup)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "valid-handoff-jwt"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_token"] == "fresh-session-jwt"
    assert body["user"]["id"] == _KID_USER_ID
    assert body["user"]["display_name"] == "Sparrow"

    # The route should have inserted a kid_handoff_jti row to enforce
    # single-use. The exact model varies, but session.add must have been
    # called at least once.
    fake_session.add.assert_called()


def test_kid_exchange_replay_attack_rejected(
    kid_exchange_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing the same handoff JWT (jti already claimed) -> 401."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")

    monkeypatch.setattr(
        auth_routes_module,
        "verify_dragonfly_jwt",
        lambda token, *, settings, expected_token_type=None: _valid_handoff_claims(),
    )

    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("duplicate jti"))
    )

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "replayed-handoff-jwt"},
    )

    # Either 401 (preferred per plan) or 409 (also acceptable for replay).
    assert response.status_code in (401, 409)
    if response.status_code == 401:
        assert response.headers.get("www-authenticate") == "Bearer"


def test_kid_exchange_expired_handoff_rejected(
    kid_exchange_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired handoff JWT bubbles InvalidDragonflyJwt -> 401."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")
    if not hasattr(auth_routes_module, "InvalidDragonflyJwt"):
        pytest.skip("InvalidDragonflyJwt not present yet")

    invalid = auth_routes_module.InvalidDragonflyJwt("Signature has expired")

    def fake_verify(token: str, *, settings: Settings, expected_token_type=None):
        raise invalid

    monkeypatch.setattr(auth_routes_module, "verify_dragonfly_jwt", fake_verify)

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "expired-jwt"},
    )

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_kid_exchange_wrong_audience_rejected(
    kid_exchange_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token failing audience check raises InvalidDragonflyJwt -> 401."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")
    if not hasattr(auth_routes_module, "InvalidDragonflyJwt"):
        pytest.skip("InvalidDragonflyJwt not present yet")

    invalid = auth_routes_module.InvalidDragonflyJwt("Invalid audience")

    def fake_verify(token: str, *, settings: Settings, expected_token_type=None):
        raise invalid

    monkeypatch.setattr(auth_routes_module, "verify_dragonfly_jwt", fake_verify)

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "wrong-aud-jwt"},
    )

    assert response.status_code == 401


def test_kid_exchange_missing_token_rejected(
    kid_exchange_client: TestClient,
) -> None:
    """Empty request body -> 422 (Pydantic validation)."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")

    response = kid_exchange_client.post("/v1/auth/kid-exchange", json={})
    assert response.status_code == 422


def test_kid_exchange_disabled_kid_rejected(
    kid_exchange_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kid user with disabled_at set must not receive a session JWT."""
    if not _route_ready():
        pytest.skip("kid-exchange route not present yet")

    monkeypatch.setattr(
        auth_routes_module,
        "verify_dragonfly_jwt",
        lambda token, *, settings, expected_token_type=None: _valid_handoff_claims(),
    )
    monkeypatch.setattr(
        auth_routes_module,
        "mint_session_token",
        lambda **kwargs: "should-not-be-issued",
    )

    kid_lookup = MagicMock()
    kid_lookup.scalar_one_or_none = MagicMock(return_value=_kid_row(disabled=True))
    fake_session.execute = AsyncMock(return_value=kid_lookup)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "valid-handoff-jwt"},
    )

    assert response.status_code == 403
