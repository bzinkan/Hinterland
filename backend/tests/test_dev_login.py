"""Tests for POST /v1/auth/dev-login -- fail-closed dev auto-login.

The route mints a kid session JWT for a fixed, well-known sandbox lineage
(dev parent -> dev group -> dev kid -> kid membership) so pre-production
mobile builds can sign in silently at boot. The gate stack is fail-closed:
default-off flag, mandatory non-empty shared key, and an unconditional 404
on env=prod. When closed, the response must be indistinguishable from an
unregistered route.

The tests stub ``mint_session_token`` (module-global lookup in the route)
so no Azure Key Vault credentials are needed, and drive the DB through an
``AsyncMock(spec=AsyncSession)`` like tests/test_kid_exchange.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

import app.api.routes.auth as auth_routes_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_DEV_KEY = "test-dev-login-key"


def _enabled_settings(**overrides: Any) -> Settings:
    params: dict[str, Any] = {
        "env": "local",
        "app_version": "test",
        "dev_login_enabled": True,
        "dev_login_key": _DEV_KEY,
    }
    params.update(overrides)
    return Settings(**params)


@contextmanager
def _client(settings: Settings, fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(settings)

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client


def _fake_session(*, get_side_effect: Any = None) -> AsyncMock:
    """AsyncSession mock: `get` defaults to an empty DB (returns None)."""
    session = AsyncMock(spec=AsyncSession)
    if get_side_effect is None:
        session.get = AsyncMock(return_value=None)
    else:
        session.get = AsyncMock(side_effect=get_side_effect)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _existing_rows() -> dict[str, Any]:
    """The five sandbox rows as they would exist after a first call."""
    return {
        auth_routes_module.DEV_PARENT_USER_ID: models.User(
            id=auth_routes_module.DEV_PARENT_USER_ID,
            firebase_uid=None,
            role="parent",
            display_name="Dev Parent",
        ),
        auth_routes_module.DEV_GROUP_ID: models.Group(
            id=auth_routes_module.DEV_GROUP_ID,
            name="Dev Group",
            join_code=auth_routes_module._dev_join_code(_DEV_KEY),
            owner_user_id=auth_routes_module.DEV_PARENT_USER_ID,
        ),
        auth_routes_module.DEV_KID_USER_ID: models.User(
            id=auth_routes_module.DEV_KID_USER_ID,
            firebase_uid=None,
            role="kid",
            display_name="Dev Kid",
            age_band="11-12",
            parent_user_id=auth_routes_module.DEV_PARENT_USER_ID,
            consent_granted_at=datetime.now(UTC),
        ),
        auth_routes_module.DEV_MEMBERSHIP_ID: models.Membership(
            id=auth_routes_module.DEV_MEMBERSHIP_ID,
            group_id=auth_routes_module.DEV_GROUP_ID,
            user_id=auth_routes_module.DEV_KID_USER_ID,
            role="kid",
        ),
        auth_routes_module.DEV_PARENT_MEMBERSHIP_ID: models.Membership(
            id=auth_routes_module.DEV_PARENT_MEMBERSHIP_ID,
            group_id=auth_routes_module.DEV_GROUP_ID,
            user_id=auth_routes_module.DEV_PARENT_USER_ID,
            role="parent",
        ),
    }


def _stub_mint(monkeypatch: pytest.MonkeyPatch, token: str = "dev-session-jwt") -> None:
    monkeypatch.setattr(
        auth_routes_module,
        "mint_session_token",
        lambda **kwargs: token,
    )


# ---------------------------------------------------------------------------
# Fail-closed gates
# ---------------------------------------------------------------------------


def test_dev_login_disabled_by_default_is_404() -> None:
    """No flags set -> 404, byte-compatible with an unregistered route."""
    fake_session = _fake_session()
    with _client(Settings(env="local", app_version="test"), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})
        absent_route = client.post("/v1/auth/no-such-route")

    assert response.status_code == 404
    assert absent_route.status_code == 404
    # Same error envelope (code + message) as a route that does not exist;
    # request_id differs per request, so compare the stable fields.
    assert response.json()["error"]["code"] == absent_route.json()["error"]["code"]
    assert response.json()["error"]["message"] == absent_route.json()["error"]["message"]
    fake_session.add.assert_not_called()


@pytest.mark.parametrize("key_value", [None, ""])
def test_dev_login_enabled_without_key_is_404(key_value: str | None) -> None:
    """Enabled but key unset/empty is a misconfiguration -> still 404."""
    fake_session = _fake_session()
    settings = _enabled_settings(dev_login_key=key_value)
    with _client(settings, fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 404
    fake_session.add.assert_not_called()


def test_dev_login_prod_env_is_404_even_when_enabled() -> None:
    """env=prod overrides the flag AND the key -> 404."""
    fake_session = _fake_session()
    settings = _enabled_settings(env="prod")
    with _client(settings, fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 404
    fake_session.add.assert_not_called()


def test_dev_login_wrong_key_is_401() -> None:
    fake_session = _fake_session()
    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": "wrong-key"})

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    fake_session.add.assert_not_called()


def test_dev_login_missing_key_header_is_401() -> None:
    fake_session = _fake_session()
    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login")

    assert response.status_code == 401
    fake_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------


def test_dev_login_first_call_provisions_sandbox_and_mints_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty DB -> all four sandbox rows created, token + user returned."""
    _stub_mint(monkeypatch)
    fake_session = _fake_session()  # every `get` returns None
    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["session_token"] == "dev-session-jwt"
    assert body["user"]["id"] == auth_routes_module.DEV_KID_USER_ID
    assert body["user"]["role"] == "kid"
    assert body["user"]["display_name"] == "Dev Kid"
    assert body["expires_at"] is not None

    added = [call.args[0] for call in fake_session.add.call_args_list]
    added_ids = {row.id for row in added}
    assert added_ids == {
        auth_routes_module.DEV_PARENT_USER_ID,
        auth_routes_module.DEV_GROUP_ID,
        auth_routes_module.DEV_KID_USER_ID,
        auth_routes_module.DEV_MEMBERSHIP_ID,
        auth_routes_module.DEV_PARENT_MEMBERSHIP_ID,
    }
    kid_rows = [row for row in added if row.id == auth_routes_module.DEV_KID_USER_ID]
    assert kid_rows[0].role == "kid"
    assert kid_rows[0].age_band == "11-12"
    assert kid_rows[0].parent_user_id == auth_routes_module.DEV_PARENT_USER_ID
    assert kid_rows[0].consent_granted_at is not None  # pre-consented sandbox
    fake_session.commit.assert_awaited()


def test_dev_login_heals_disabled_sandbox_kid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 'Request account deletion' tap inside the sandbox must not brick
    dev-login deployment-wide: provisioning clears disabled_at on the
    synthetic kid instead of minting a token that would 403 on first use."""
    _stub_mint(monkeypatch)
    rows = _existing_rows()
    rows[auth_routes_module.DEV_KID_USER_ID].disabled_at = datetime.now(UTC)

    async def get_existing(model: Any, pk: str) -> Any:
        return rows.get(pk)

    fake_session = _fake_session(get_side_effect=get_existing)
    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 200
    assert rows[auth_routes_module.DEV_KID_USER_ID].disabled_at is None
    fake_session.add.assert_not_called()


def test_dev_login_second_call_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five rows already exist -> no inserts, same fixed ids returned."""
    _stub_mint(monkeypatch)
    rows = _existing_rows()

    async def get_existing(model: Any, pk: str) -> Any:
        return rows.get(pk)

    fake_session = _fake_session(get_side_effect=get_existing)
    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == auth_routes_module.DEV_KID_USER_ID
    fake_session.add.assert_not_called()


def test_dev_login_concurrent_first_call_race_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IntegrityError on the first commit -> rollback -> re-fetch -> 200."""
    _stub_mint(monkeypatch)
    rows = _existing_rows()
    lookup_order = [
        auth_routes_module.DEV_PARENT_USER_ID,
        auth_routes_module.DEV_GROUP_ID,
        auth_routes_module.DEV_KID_USER_ID,
        auth_routes_module.DEV_MEMBERSHIP_ID,
        auth_routes_module.DEV_PARENT_MEMBERSHIP_ID,
    ]
    # First pass sees an empty DB; after the losing commit + rollback the
    # second pass finds the winner's rows.
    get_results = [None] * 5 + [rows[pk] for pk in lookup_order]
    fake_session = _fake_session(get_side_effect=get_results)
    fake_session.commit = AsyncMock(
        side_effect=[IntegrityError("INSERT", {}, Exception("duplicate pk")), None]
    )

    with _client(_enabled_settings(), fake_session) as client:
        response = client.post("/v1/auth/dev-login", headers={"X-Dev-Login-Key": _DEV_KEY})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == auth_routes_module.DEV_KID_USER_ID
    fake_session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Surface checks
# ---------------------------------------------------------------------------


def test_dev_login_absent_from_openapi_schema() -> None:
    """Even when enabled, the route must not appear in the OpenAPI schema."""
    fake_session = _fake_session()
    with _client(_enabled_settings(), fake_session) as client:
        schema = client.get("/openapi.json").json()

    assert "/v1/auth/dev-login" not in schema["paths"]


def test_dev_sandbox_ulid_constants_are_valid() -> None:
    """Fixed ids must be real 26-char Crockford base32 ULIDs (no I L O U)."""
    for constant in (
        auth_routes_module.DEV_PARENT_USER_ID,
        auth_routes_module.DEV_GROUP_ID,
        auth_routes_module.DEV_KID_USER_ID,
        auth_routes_module.DEV_MEMBERSHIP_ID,
        auth_routes_module.DEV_PARENT_MEMBERSHIP_ID,
    ):
        assert len(constant) == 26
        assert not set(constant) & set("ILOU")
        assert str(ULID.from_str(constant)) == constant

    # The derived join code matches the generate_join_code() shape:
    # 6 Crockford chars, deterministic per key, distinct across keys.
    code = auth_routes_module._dev_join_code(_DEV_KEY)
    assert len(code) == 6
    assert not set(code) & set("ILOU")
    assert code == auth_routes_module._dev_join_code(_DEV_KEY)
    assert code != auth_routes_module._dev_join_code("some-other-key")
