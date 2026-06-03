"""Tests for POST /v1/auth/consent (public unauthenticated).

The endpoint persists a `parent_consent_records` row as the durable
audit-of-record and ALSO emits the structured log event so existing
log-based audits keep working. Tests assert both the row was added
(DI-mock spy) and the response shape the parent-signup flow will read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app


@pytest.fixture
def fake_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    # `session.add` is a plain (non-async) method on AsyncSession; the
    # AsyncMock spec turns it into an async-by-default attribute, which
    # would make the route's `session.add(record)` return a coroutine
    # nobody awaits. Force it to a sync MagicMock so the call records
    # the argument synchronously, the way the real engine does.
    from unittest.mock import MagicMock

    session.add = MagicMock()
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


def test_consent_records_with_no_auth(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    """Public endpoint -- no Authorization header required."""
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com"},
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
    assert row.recorded_at is not None
    fake_session.commit.assert_awaited()


def test_consent_default_policy_version_applied(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    """When the client omits policy_version, the server stamps the current one."""
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com"},
    )
    assert response.status_code == 200
    # Don't hardcode the literal string -- the constant in auth.py is
    # the source of truth and bumps independently. Just assert the row
    # and response agree and the value is non-empty.
    from app.api.routes.auth import _CURRENT_POLICY_VERSION

    assert response.json()["policy_version"] == _CURRENT_POLICY_VERSION
    row = _added_consent_row(fake_session)
    assert row.policy_version == _CURRENT_POLICY_VERSION


def test_consent_accepts_explicit_policy_version(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com", "policy_version": "2027-01-01"},
    )
    assert response.status_code == 200
    assert response.json()["policy_version"] == "2027-01-01"
    row = _added_consent_row(fake_session)
    assert row.policy_version == "2027-01-01"


def test_consent_accepts_optional_kid_display_name(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com", "kid_display_name": "Sparrow"},
    )
    assert response.status_code == 200
    row = _added_consent_row(fake_session)
    assert row.kid_display_name == "Sparrow"


def test_consent_response_shape(
    consent_client: TestClient, fake_session: AsyncMock
) -> None:
    """Response carries id + recorded_at + policy_version, no extras."""
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"id", "recorded_at", "policy_version"}
    # The row id and response id agree -- this is the join key the
    # client will later pass back to parent-signup.
    row = _added_consent_row(fake_session)
    assert row.id == body["id"]


def test_consent_422_on_missing_email(consent_client: TestClient) -> None:
    response = consent_client.post("/v1/auth/consent", json={})
    assert response.status_code == 422


def test_consent_422_on_malformed_email(consent_client: TestClient) -> None:
    response = consent_client.post("/v1/auth/consent", json={"email": "not-an-email"})
    assert response.status_code == 422


def test_consent_422_on_overlong_kid_display_name(consent_client: TestClient) -> None:
    response = consent_client.post(
        "/v1/auth/consent",
        json={"email": "parent@example.com", "kid_display_name": "x" * 81},
    )
    assert response.status_code == 422
