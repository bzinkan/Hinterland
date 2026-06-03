"""Tests for `app/core/internal_auth.py` + the `/internal/*` route guard.

Coverage matches the requirements in the implementation brief:

- Local default does not require OIDC (no token needed).
- Non-local default requires OIDC.
- Missing / malformed `Authorization` header -> 401.
- Verifier raises `InternalAuthError` -> 401 (covers bad signature,
  audience mismatch, expiry, transport outage).
- Token decodes but has no `email` claim -> 401.
- Token decodes with email not on the allowlist -> 403.
- Token decodes with allowed email -> 200 (request reaches the route).
- Misconfigured audience or allowlist -> 503 (fail-closed).
- `/internal/moderation/process` + `/internal/inat/submit` reject
  missing auth BEFORE any worker side effect when OIDC is required.

The verifier is monkeypatched module-wide; no Google network calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.internal_auth as internal_auth_module
from app.core.config import Settings
from app.core.internal_auth import (
    InternalAuthError,
    InternalAuthMisconfigured,
    InternalPrincipal,
    require_internal_oidc,
)
from app.db.session import get_db_session
from app.main import create_app

# A throwaway router that mounts inside the test app so we can exercise
# the dependency without dragging real backend side effects in. The
# integration tests at the bottom exercise the real internal_* routes.
_PROBE_ROUTER = APIRouter(
    prefix="/probe",
    dependencies=[Depends(require_internal_oidc)],
)


@_PROBE_ROUTER.get("/internal-probe")
async def probe_endpoint() -> dict[str, str]:
    return {"ok": "true"}


# ---------------------------------------------------------------------------
# Test app + fixtures
# ---------------------------------------------------------------------------


def _build_client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.include_router(_PROBE_ROUTER)

    fake_session = AsyncMock(spec=AsyncSession)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        yield client


def _settings_local() -> Settings:
    return Settings(env="local", app_version="test")


def _settings_dev_oidc_on() -> Settings:
    return Settings(
        env="dev",
        app_version="test",
        internal_oidc_audience="https://api.dragonfly-app.net",
        internal_oidc_allowed_service_accounts=[
            "worker@dragonflyapp-495423.iam.gserviceaccount.com"
        ],
    )


def _settings_dev_explicit_off() -> Settings:
    return Settings(
        env="dev",
        app_version="test",
        internal_oidc_required=False,
    )


# ---------------------------------------------------------------------------
# `Settings.require_internal_oidc` resolution table
# ---------------------------------------------------------------------------


def test_settings_local_does_not_require_oidc() -> None:
    assert _settings_local().require_internal_oidc is False


def test_settings_dev_requires_oidc_by_default() -> None:
    s = Settings(env="dev", app_version="test")
    assert s.require_internal_oidc is True


def test_settings_prod_requires_oidc_by_default() -> None:
    s = Settings(env="prod", app_version="test")
    assert s.require_internal_oidc is True


def test_settings_explicit_off_wins_even_in_prod() -> None:
    s = Settings(env="prod", app_version="test", internal_oidc_required=False)
    assert s.require_internal_oidc is False


def test_settings_explicit_on_wins_even_in_local() -> None:
    s = Settings(env="local", app_version="test", internal_oidc_required=True)
    assert s.require_internal_oidc is True


# ---------------------------------------------------------------------------
# Local: dependency is a no-op
# ---------------------------------------------------------------------------


def test_local_skips_oidc_no_token_needed() -> None:
    client_iter = _build_client(_settings_local())
    client = next(client_iter)
    try:
        response = client.get("/probe/internal-probe")
        assert response.status_code == 200
        assert response.json()["ok"] == "true"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# When required: missing / malformed Authorization
# ---------------------------------------------------------------------------


def test_missing_authorization_header_is_401() -> None:
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get("/probe/internal-probe")
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "missing_bearer_token"
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        client.close()


def test_malformed_authorization_header_is_401() -> None:
    """Wrong scheme is treated as missing -- never tries to decode."""
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Basic abc:def"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "missing_bearer_token"
    finally:
        client.close()


def test_bearer_with_no_token_is_401() -> None:
    """`Authorization: Bearer ` (no token) is rejected without calling verifier."""
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "missing_bearer_token"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# When required: verifier outcomes
# ---------------------------------------------------------------------------


def test_invalid_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: str, __: str) -> dict[str, Any]:
        raise InternalAuthError("bad signature")

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", boom)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "invalid_internal_oidc_token"
        # 401 always advertises Bearer per RFC 6750.
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        client.close()


def test_audience_mismatch_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifier raises on audience mismatch -- mapped to 401."""

    def fake(_: str, audience: str) -> dict[str, Any]:
        raise InternalAuthError(f"audience mismatch: expected {audience}")

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "invalid_internal_oidc_token"
    finally:
        client.close()


def test_token_missing_email_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(_: str, __: str) -> dict[str, Any]:
        # Note: real verify_google_oidc_token would 401 a token with
        # email_verified=False before this check. The dependency-level
        # missing_email check guards the case where Google ever issues
        # a verified token without an email claim at all.
        return {
            "sub": "1234567890",
            "aud": "https://api.dragonfly-app.net",
            "email_verified": True,
            "iss": "https://accounts.google.com",
        }

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "internal_oidc_token_missing_email"
    finally:
        client.close()


def test_unallowed_service_account_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(_: str, __: str) -> dict[str, Any]:
        return {
            "email": "attacker@dragonflyapp-495423.iam.gserviceaccount.com",
            "email_verified": True,
            "aud": "https://api.dragonfly-app.net",
            "iss": "https://accounts.google.com",
        }

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["message"] == "internal_oidc_principal_forbidden"
    finally:
        client.close()


def test_unexpected_issuer_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth iss pin: claims with a non-Google issuer never reach the route."""

    def fake(_: str, audience: str) -> dict[str, Any]:
        return {
            "email": "worker@dragonflyapp-495423.iam.gserviceaccount.com",
            "email_verified": True,
            "aud": audience,
            # Wrong issuer (could be MS Entra, another Google KMS tenant, etc.)
            "iss": "https://login.microsoftonline.com/abc/v2.0",
        }

    # The real verify_google_oidc_token re-validates the iss claim after
    # the upstream library hands back a decoded dict. Tests monkeypatch
    # the seam so we have to do the same check here -- assert the
    # monkeypatch replaces the FULL verifier, including the iss check.
    # Simulate by raising InternalAuthError directly (what
    # verify_google_oidc_token would do internally given a wrong iss).
    monkeypatch.setattr(
        internal_auth_module,
        "verify_google_oidc_token",
        lambda _t, _a: (_ for _ in ()).throw(InternalAuthError("unexpected issuer")),
    )
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "invalid_internal_oidc_token"
    finally:
        client.close()


def test_unverified_email_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth email_verified gate: token email must be verified."""

    monkeypatch.setattr(
        internal_auth_module,
        "verify_google_oidc_token",
        lambda _t, _a: (_ for _ in ()).throw(InternalAuthError("token email is not verified")),
    )
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "invalid_internal_oidc_token"
    finally:
        client.close()


def test_verifier_library_missing_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """If google-auth is gone from the image, alert the operator about the deploy, not the caller.

    The 503 contract means Eventarc sees the same failure bucket as
    audience/allowlist drift -- it doesn't get to retry-spin against
    a 401 that no caller can fix.
    """

    def fake(_: str, __: str) -> dict[str, Any]:
        raise InternalAuthMisconfigured("google-auth missing")

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 503
        assert "internal_oidc_misconfigured" in response.json()["error"]["message"]
    finally:
        client.close()


def test_allowlist_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed-case allowlist entries do not silently 403 the legitimate caller.

    Operator pastes 'Worker@Dragonfly.iam.gserviceaccount.com' into the
    env var; Google reports the SA email lowercased. The dependency
    normalizes both sides so the call succeeds.
    """
    s = Settings(
        env="dev",
        app_version="test",
        internal_oidc_audience="https://api.dragonfly-app.net",
        internal_oidc_allowed_service_accounts=[
            "Worker@DRAGONFLYAPP-495423.iam.gserviceaccount.com",
        ],
    )

    def fake(_: str, audience: str) -> dict[str, Any]:
        return {
            "email": "worker@dragonflyapp-495423.iam.gserviceaccount.com",
            "email_verified": True,
            "aud": audience,
            "iss": "https://accounts.google.com",
        }

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(s)
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 200
    finally:
        client.close()


def test_allowed_service_account_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake(token: str, audience: str) -> dict[str, Any]:
        captured["token"] = token
        captured["audience"] = audience
        return {
            "email": "worker@dragonflyapp-495423.iam.gserviceaccount.com",
            "email_verified": True,
            "aud": audience,
            "iss": "https://accounts.google.com",
        }

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)
    client_iter = _build_client(_settings_dev_oidc_on())
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] == "true"
        # The verifier was called with the configured audience.
        assert captured["audience"] == "https://api.dragonfly-app.net"
        assert captured["token"] == "fake.token.here"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Fail-closed config drift
# ---------------------------------------------------------------------------


def test_missing_audience_returns_503() -> None:
    s = Settings(
        env="dev",
        app_version="test",
        internal_oidc_allowed_service_accounts=["worker@example.iam.gserviceaccount.com"],
    )
    client_iter = _build_client(s)
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 503
        assert "internal_oidc_misconfigured" in response.json()["error"]["message"]
    finally:
        client.close()


def test_empty_allowlist_returns_503() -> None:
    s = Settings(
        env="dev",
        app_version="test",
        internal_oidc_audience="https://api.dragonfly-app.net",
    )
    client_iter = _build_client(s)
    client = next(client_iter)
    try:
        response = client.get(
            "/probe/internal-probe",
            headers={"Authorization": "Bearer fake.token.here"},
        )
        assert response.status_code == 503
        assert "internal_oidc_misconfigured" in response.json()["error"]["message"]
    finally:
        client.close()


def test_misconfigured_503_fires_without_token() -> None:
    """Fail-closed check happens BEFORE bearer extraction.

    Catches an operator who turned require=True without configuring the
    rest -- callers see a config error rather than auth errors that
    might be silently retried away by Eventarc.
    """
    s = Settings(env="dev", app_version="test")
    client_iter = _build_client(s)
    client = next(client_iter)
    try:
        response = client.get("/probe/internal-probe")
        assert response.status_code == 503
        assert "internal_oidc_misconfigured" in response.json()["error"]["message"]
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Explicit off in non-local env: route reachable without token
# ---------------------------------------------------------------------------


def test_explicit_off_dev_skips_oidc() -> None:
    client_iter = _build_client(_settings_dev_explicit_off())
    client = next(client_iter)
    try:
        response = client.get("/probe/internal-probe")
        assert response.status_code == 200
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Integration: real /internal/* routes reject missing auth BEFORE side effects
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


class _NullStorage:
    """Stub storage so the moderation route's DI graph resolves without Azure.

    Without it, get_signed_url_generator would try to construct the real
    BlobSignedUrlGenerator and fail at `blob_account_endpoint` validation
    -- which would mask the auth-related assertions we actually want.
    """

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        raise NotImplementedError

    def copy_object(self, **_: Any) -> None:
        raise NotImplementedError

    def delete_object(self, **_: Any) -> None:
        raise NotImplementedError

    def generate_get_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError


def _build_real_internal_client(
    settings: Settings,
    session: AsyncMock,
) -> Iterator[TestClient]:
    app = create_app(settings)
    # Pre-seed app.state so get_signed_url_generator returns the stub
    # before reaching the real Blob constructor.
    app.state.signed_url_generator = _NullStorage()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as client:
        yield client


def test_internal_moderation_rejects_missing_auth_before_db(
    fake_session: AsyncMock,
) -> None:
    """No token + OIDC required -> 401, and the DB session is never touched."""
    fake_session.execute = AsyncMock(side_effect=AssertionError("should not query DB"))
    client_iter = _build_real_internal_client(_settings_dev_oidc_on(), fake_session)
    client = next(client_iter)
    try:
        response = client.post(
            "/internal/moderation/process",
            json={"bucket": "ignored", "object_name": "ignored.jpg"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "missing_bearer_token"
        fake_session.execute.assert_not_called()
    finally:
        client.close()


def test_internal_inat_rejects_missing_auth_before_db(
    fake_session: AsyncMock,
) -> None:
    fake_session.execute = AsyncMock(side_effect=AssertionError("should not query DB"))
    client_iter = _build_real_internal_client(_settings_dev_oidc_on(), fake_session)
    client = next(client_iter)
    try:
        response = client.post(
            "/internal/inat/submit",
            json={"observation_id": "01J0OBSERVATIONID000000ULI"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "missing_bearer_token"
        fake_session.execute.assert_not_called()
    finally:
        client.close()


def test_internal_moderation_local_skips_auth_check(
    fake_session: AsyncMock,
) -> None:
    """In env=local the dependency is a no-op; the route is reachable.

    The DB lookup will surface a separate failure (no Photo row), but
    that's a route-level concern -- the assertion here is that the
    auth dependency did NOT block the request before it.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=user_result)
    client_iter = _build_real_internal_client(_settings_local(), fake_session)
    client = next(client_iter)
    try:
        response = client.post(
            "/internal/moderation/process",
            json={
                "bucket": "dragonfly-photos-test",
                "object_name": "pending/missing.jpg",
            },
        )
        # Route-level 404 (or 503 if storage is missing) is the expected
        # downstream behavior; 401 / 403 / 503-misconfigured would mean
        # the auth dependency wrongly engaged in env=local.
        assert response.status_code not in (401, 403)
        if response.status_code == 503:
            assert "internal_oidc_misconfigured" not in response.json()["error"]["message"]
    finally:
        client.close()


# ---------------------------------------------------------------------------
# InternalPrincipal shape (smoke)
# ---------------------------------------------------------------------------


def test_internal_principal_carries_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dependency's return value is observable via FastAPI's DI machinery."""
    captured: list[InternalPrincipal | None] = []

    def fake(_: str, audience: str) -> dict[str, Any]:
        return {
            "email": "worker@dragonflyapp-495423.iam.gserviceaccount.com",
            "aud": audience,
            "iss": "https://accounts.google.com",
            "sub": "123",
        }

    monkeypatch.setattr(internal_auth_module, "verify_google_oidc_token", fake)

    capture_router = APIRouter(prefix="/probe2")

    @capture_router.get("/who")
    def who(
        principal: Annotated[InternalPrincipal | None, Depends(require_internal_oidc)],
    ) -> dict[str, str]:
        captured.append(principal)
        return {"email": principal.email if principal else ""}

    app = create_app(_settings_dev_oidc_on())
    app.include_router(capture_router)
    with TestClient(app) as client:
        response = client.get(
            "/probe2/who",
            headers={"Authorization": "Bearer fake.token.here"},
        )
    assert response.status_code == 200
    assert response.json()["email"] == "worker@dragonflyapp-495423.iam.gserviceaccount.com"
    assert captured and captured[0] is not None
    assert captured[0].audience == "https://api.dragonfly-app.net"
    assert captured[0].claims["sub"] == "123"
