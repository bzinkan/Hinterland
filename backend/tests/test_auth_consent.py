"""Tests for POST /v1/auth/consent (public unauthenticated).

The endpoint persists a `parent_consent_records` row as the durable
audit-of-record and ALSO emits the structured log event so existing
log-based audits keep working. Tests assert both the row was added
(DI-mock spy) and the response shape the parent-signup flow will read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.auth as auth_routes_module
from app.core.config import Settings
from app.core.parent_consent import hash_browser_consent_nonce
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

CURRENT_POLICY_VERSION = auth_routes_module._CURRENT_POLICY_VERSION
CONSENT_NONCE = "a" * 64


def _payload(email: str = "parent@example.com", **extra: object) -> dict[str, object]:
    return {
        "email": email,
        "policy_version": CURRENT_POLICY_VERSION,
        "consent_nonce": CONSENT_NONCE,
        **extra,
    }


def _result(value: models.ParentConsentRecord | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
def fake_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    # `session.add` is a plain (non-async) method on AsyncSession; the
    # AsyncMock spec turns it into an async-by-default attribute, which
    # would make the route's `session.add(record)` return a coroutine
    # nobody awaits. Force it to a sync MagicMock so the call records
    # the argument synchronously, the way the real engine does.
    session.add = MagicMock()
    session.execute = AsyncMock(return_value=_result(None))
    return session


@pytest.fixture
def consent_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def _added_consent_row(fake_session: AsyncMock) -> models.ParentConsentRecord:
    """Pull the single ParentConsentRecord the route added during the call.

    Raises a clear AssertionError if the route added zero or multiple
    rows -- both indicate a regression in the persistence path.
    """
    matches = [
        call.args[0]
        for call in fake_session.add.call_args_list
        if call.args and isinstance(call.args[0], models.ParentConsentRecord)
    ]
    assert len(matches) == 1, (
        f"Expected exactly one ParentConsentRecord added, got {len(matches)}: "
        f"all add() calls = {fake_session.add.call_args_list}"
    )
    return matches[0]


def test_consent_records_with_no_auth(consent_client: TestClient, fake_session: AsyncMock) -> None:
    """Public endpoint -- no Authorization header required."""
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["recorded_at"]
    assert body["policy_version"]

    row = _added_consent_row(fake_session)
    assert row.parent_email == "parent@example.com"
    assert row.source == "web_consent"
    assert row.policy_version == body["policy_version"]
    assert row.browser_nonce_sha256 == hash_browser_consent_nonce(CONSENT_NONCE)
    assert row.browser_nonce_sha256 != CONSENT_NONCE
    assert row.recorded_at is not None
    fake_session.commit.assert_awaited()


def test_consent_current_policy_version_applied(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    """The displayed policy version is echoed into the durable receipt."""
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(),
    )
    assert response.status_code == 200
    # Don't hardcode the literal string -- the constant in auth.py is
    # the source of truth and bumps independently. Just assert the row
    # and response agree and the value is non-empty.
    assert response.json()["policy_version"] == CURRENT_POLICY_VERSION
    row = _added_consent_row(fake_session)
    assert row.policy_version == CURRENT_POLICY_VERSION


def test_consent_requires_policy_version(consent_client: TestClient) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com", "consent_nonce": CONSENT_NONCE},
    )
    assert response.status_code == 422


def test_consent_rejects_stale_or_unrecognized_policy_version(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={
            "email": "parent@example.com",
            "policy_version": "2027-01-01",
            "consent_nonce": CONSENT_NONCE,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Consent policy version is not current"
    fake_session.add.assert_not_called()
    fake_session.commit.assert_not_awaited()


def test_consent_accepts_optional_kid_display_name(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(kid_display_name="Sparrow"),
    )
    assert response.status_code == 200
    row = _added_consent_row(fake_session)
    assert row.kid_display_name == "Sparrow"


def test_consent_response_shape(consent_client: TestClient, fake_session: AsyncMock) -> None:
    """Response carries id + recorded_at + policy_version, no extras."""
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"id", "recorded_at", "policy_version"}
    # The row id and response id agree -- this is the join key the
    # client will later pass back to parent-signup.
    row = _added_consent_row(fake_session)
    assert row.id == body["id"]


def test_consent_requires_exact_256_bit_lowercase_hex_nonce(consent_client: TestClient) -> None:
    for nonce in (None, "short", "!" * 64, "a" * 63, "a" * 65, "A" * 64):
        payload = {"email": "parent@example.com", "policy_version": CURRENT_POLICY_VERSION}
        if nonce is not None:
            payload["consent_nonce"] = nonce
        response = consent_client.post("/v1/auth/consent", json=payload)
        assert response.status_code == 422


def test_consent_replay_returns_same_receipt_without_nonce_or_hash(
    consent_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    recorded_at = auth_routes_module.datetime.now(auth_routes_module.UTC)
    existing = models.ParentConsentRecord(
        id="01J0CONSENTREPLAY0000000000",
        parent_email="parent@example.com",
        kid_display_name="Original optional label",
        policy_version=CURRENT_POLICY_VERSION,
        source="web_consent",
        recorded_at=recorded_at,
        browser_nonce_sha256=hash_browser_consent_nonce(CONSENT_NONCE),
    )
    fake_session.execute = AsyncMock(return_value=_result(existing))

    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(email="PARENT@example.com", kid_display_name="Changed optional label"),
    )

    assert response.status_code == 200
    assert response.headers["idempotency-replayed"] == "true"
    assert response.json() == {
        "id": existing.id,
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "policy_version": CURRENT_POLICY_VERSION,
    }
    assert "nonce" not in response.text
    assert hash_browser_consent_nonce(CONSENT_NONCE) not in response.text
    fake_session.add.assert_not_called()
    fake_session.flush.assert_not_awaited()
    fake_session.commit.assert_not_awaited()


def test_consent_replay_with_changed_details_conflicts(
    consent_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    existing = models.ParentConsentRecord(
        id="01J0CONSENTREPLAY0000000000",
        parent_email="other@example.com",
        kid_display_name=None,
        policy_version=CURRENT_POLICY_VERSION,
        source="web_consent",
        recorded_at=auth_routes_module.datetime.now(auth_routes_module.UTC),
        browser_nonce_sha256=hash_browser_consent_nonce(CONSENT_NONCE),
    )
    fake_session.execute = AsyncMock(return_value=_result(existing))

    response = consent_client.post("/v1/auth/consent", json=_payload())

    assert response.status_code == 409
    assert "different details" in response.json()["error"]["message"]
    fake_session.add.assert_not_called()


def test_concurrent_consent_retry_loads_unique_winner(
    consent_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    class UniqueNonceViolation(Exception):
        constraint_name = "uq_parent_consent_browser_nonce_sha256"

    winner = models.ParentConsentRecord(
        id="01J0CONSENTWINNER0000000000",
        parent_email="parent@example.com",
        kid_display_name=None,
        policy_version=CURRENT_POLICY_VERSION,
        source="web_consent",
        recorded_at=auth_routes_module.datetime.now(auth_routes_module.UTC),
        browser_nonce_sha256=hash_browser_consent_nonce(CONSENT_NONCE),
    )
    fake_session.execute = AsyncMock(side_effect=[_result(None), _result(winner)])
    fake_session.flush = AsyncMock(side_effect=IntegrityError("INSERT", {}, UniqueNonceViolation()))

    response = consent_client.post("/v1/auth/consent", json=_payload())

    assert response.status_code == 200
    assert response.json()["id"] == winner.id
    assert response.headers["idempotency-replayed"] == "true"
    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_awaited()


def test_consent_422_on_missing_email(consent_client: TestClient) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={"policy_version": CURRENT_POLICY_VERSION},
    )
    assert response.status_code == 422


def test_consent_422_on_malformed_email(consent_client: TestClient) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(email="not-an-email"),
    )
    assert response.status_code == 422


def test_consent_422_on_overlong_kid_display_name(consent_client: TestClient) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(kid_display_name="x" * 81),
    )
    assert response.status_code == 422


def test_consent_log_omits_parent_email_and_kid_text(
    consent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = MagicMock()
    monkeypatch.setattr(auth_routes_module.log, "info", info)

    response = consent_client.post(
        "/v1/auth/consent",
        json=_payload(email="private@example.com", kid_display_name="Sparrow"),
    )

    assert response.status_code == 200
    logged = info.call_args.kwargs
    assert "email" not in logged
    assert "email_hash" not in logged
    assert "kid_display_name" not in logged
