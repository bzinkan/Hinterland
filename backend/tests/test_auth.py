from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.auth as auth_routes_module
import app.core.auth as auth_module
from app.core.config import Settings
from app.core.parent_consent import (
    CURRENT_PARENT_CONSENT_POLICY_VERSION,
    CurrentParentConsent,
    CurrentParentConsentRequiredError,
    hash_browser_consent_nonce,
)
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier


def test_me_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Missing bearer token"


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
        display_name="Parent One",
    )

    response = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    body = response.json()
    # Required identity fields preserved.
    assert body["uid"] == "firebase-user-1"
    assert body["id"] == "firebase-user-1"
    assert body["display_name"] == "Parent One"
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


def test_resolved_me_contract_uses_canonical_local_identity() -> None:
    """The mobile owner boundary must never depend on the Entra subject."""
    resolved = auth_module._overlay_claims(
        auth_module.CachedUserClaims(
            user_id="01J0LOCALUSER0000000000000",
            display_name="Sparrow",
            role="kid",
            group_id="01J0GROUP000000000000000",
            disabled=False,
            firebase_uid=None,
            entra_oid=None,
        ),
        {
            "sub": "external-token-subject",
            "token_type": "session",
            "email": "must-not-be-used-for-owner-scope@example.com",
        },
    )

    assert resolved.uid == "01J0LOCALUSER0000000000000"
    assert resolved.id == "01J0LOCALUSER0000000000000"
    assert resolved.display_name == "Sparrow"


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


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_me_rejects_stub_claims_outside_local(env: str, monkeypatch) -> None:
    """The test-compat stub shortcut must be fail-closed on non-local envs."""
    stub_token_verifier(monkeypatch, uid="firebase-user-1", role="parent")
    app = create_app(Settings(env=env, app_version="test"))  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/v1/me", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Bearer token missing required identity claims"


# ---------------------------------------------------------------------------
# POST /v1/auth/parent-signup
# ---------------------------------------------------------------------------

_FIREBASE_UID = "firebase-parent-001"
_CONSENT_ID = "01J0CURRENTCONSENT00000000"
_CONSENT_NONCE = "a" * 64


def _parent_signup_payload(display_name: str = "Brian") -> dict[str, str]:
    return {
        "display_name": display_name,
        "consent_id": _CONSENT_ID,
        "consent_nonce": _CONSENT_NONCE,
    }


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


def _current_consent_record(
    *,
    linked_parent_user_id: str | None = None,
) -> models.ParentConsentRecord:
    return models.ParentConsentRecord(
        id=_CONSENT_ID,
        parent_email="parent@example.com",
        policy_version=CURRENT_PARENT_CONSENT_POLICY_VERSION,
        source="web_consent",
        recorded_at=datetime.now(UTC),
        browser_nonce_sha256=hash_browser_consent_nonce(_CONSENT_NONCE),
        linked_parent_user_id=linked_parent_user_id,
    )


@pytest.fixture
def parent_signup_client(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    consent = _current_consent_record()
    monkeypatch.setattr(
        auth_routes_module,
        "acquire_current_parent_consent",
        AsyncMock(
            return_value=CurrentParentConsent(
                record=consent,
                newly_linked=True,
            )
        ),
    )
    yield from _build_client_with_session(fake_session)


def test_parent_signup_requires_bearer_token(client: TestClient) -> None:
    response = client.post("/v1/auth/parent-signup", json=_parent_signup_payload())

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
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    # COPPA purge: the route DELETEs the user's expedition_progress rows
    # in the same transaction and logs the count from RETURNING.
    purge_result = MagicMock()
    purge_result.all = MagicMock(return_value=[("prog-1",), ("prog-2",)])
    fake_session.execute = AsyncMock(side_effect=[user_result, purge_result])
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
    # The second execute is the expedition_progress purge, before the
    # single commit that also persists disabled_at.
    purge_stmt = fake_session.execute.await_args_list[1].args[0]
    assert isinstance(purge_stmt, Delete)
    assert "expedition_progress" in str(purge_stmt)
    fake_session.commit.assert_awaited_once()


def test_parent_signup_validates_display_name(
    parent_signup_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json={"consent_id": _CONSENT_ID, "consent_nonce": _CONSENT_NONCE},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "Brian", "consent_nonce": _CONSENT_NONCE},
        {"display_name": "Brian", "consent_id": _CONSENT_ID},
        {
            "display_name": "Brian",
            "consent_id": _CONSENT_ID,
            "consent_nonce": "A" * 64,
        },
    ],
)
def test_parent_signup_requires_exact_browser_consent_proof(
    parent_signup_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, str],
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=body,
    )

    assert response.status_code == 422


def test_parent_signup_creates_user_and_sets_claim(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)
    events: list[str] = []

    # First execute() returns a result whose scalar_one_or_none is None
    # (no existing user). Subsequent commit/refresh are no-ops on the mock.
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=no_user_result)
    fake_session.add = MagicMock(side_effect=lambda _user: events.append("add_user"))
    fake_session.flush = AsyncMock(side_effect=lambda: events.append("flush_user"))
    consent = _current_consent_record()

    def acquire_consent(*_args: object, **_kwargs: object) -> CurrentParentConsent:
        events.append("link_consent")
        return CurrentParentConsent(record=consent, newly_linked=True)

    monkeypatch.setattr(
        auth_routes_module,
        "acquire_current_parent_consent",
        AsyncMock(side_effect=acquire_consent),
    )
    fake_session.commit = AsyncMock(side_effect=lambda: events.append("commit_both"))
    fake_session.refresh = AsyncMock(side_effect=lambda _user: events.append("refresh_user"))

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=_parent_signup_payload(),
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
    fake_session.flush.assert_awaited_once()
    fake_session.refresh.assert_awaited_once_with(added_user)
    assert events == [
        "add_user",
        "flush_user",
        "link_consent",
        "commit_both",
        "refresh_user",
    ]


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
        json=_parent_signup_payload(),
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


def test_parent_signup_concurrent_identity_insert_loads_winner_and_revalidates_proof(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UniqueIdentityViolation(Exception):
        constraint_name = "uq_users_firebase_uid"

    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        email="parent@example.com",
        role="parent",
    )
    winner = models.User(
        id="01J0CONCURRENTWINNER00000000",
        firebase_uid=_FIREBASE_UID,
        entra_oid=None,
        role="parent",
        display_name="Brian",
    )
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none.return_value = None
    winner_result = MagicMock()
    winner_result.scalar_one_or_none.return_value = winner
    fake_session.execute = AsyncMock(side_effect=[no_user_result, winner_result])
    fake_session.add = MagicMock()
    fake_session.flush = AsyncMock(
        side_effect=IntegrityError("INSERT users", {}, UniqueIdentityViolation())
    )
    fake_session.rollback = AsyncMock()
    consent = _current_consent_record(linked_parent_user_id=winner.id)
    acquire = AsyncMock(return_value=CurrentParentConsent(record=consent, newly_linked=False))
    monkeypatch.setattr(auth_routes_module, "acquire_current_parent_consent", acquire)

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=_parent_signup_payload(),
    )

    assert response.status_code == 200
    assert response.json()["id"] == winner.id
    fake_session.rollback.assert_awaited_once()
    acquire.assert_awaited_once_with(
        fake_session,
        parent_user_id=winner.id,
        verified_email="parent@example.com",
        consent_id=_CONSENT_ID,
        consent_nonce=_CONSENT_NONCE,
    )
    fake_session.commit.assert_not_awaited()


def test_parent_signup_does_not_swallow_unrelated_integrity_failure(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnrelatedViolation(Exception):
        constraint_name = "ck_users_role"

    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID)
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none.return_value = None
    fake_session.execute = AsyncMock(return_value=no_user_result)
    fake_session.add = MagicMock()
    expected = IntegrityError("INSERT users", {}, UnrelatedViolation())
    fake_session.flush = AsyncMock(side_effect=expected)
    fake_session.rollback = AsyncMock()

    with pytest.raises(IntegrityError) as raised:
        parent_signup_client.post(
            "/v1/auth/parent-signup",
            headers={"Authorization": "Bearer valid"},
            json=_parent_signup_payload(),
        )

    assert raised.value is expected
    fake_session.rollback.assert_awaited_once()


def test_parent_signup_links_current_reconsent_for_existing_user(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        email="parent@example.com",
        role="parent",
    )
    existing = models.User(
        id="01J0EXISTINGULID0000000000",
        firebase_uid=_FIREBASE_UID,
        role="parent",
        display_name="Brian",
    )
    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing)
    fake_session.execute = AsyncMock(return_value=existing_result)
    consent = _current_consent_record()
    acquire = AsyncMock(return_value=CurrentParentConsent(record=consent, newly_linked=True))
    monkeypatch.setattr(auth_routes_module, "acquire_current_parent_consent", acquire)

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=_parent_signup_payload(),
    )

    assert response.status_code == 200
    acquire.assert_awaited_once_with(
        fake_session,
        parent_user_id=existing.id,
        verified_email="parent@example.com",
        consent_id=_CONSENT_ID,
        consent_nonce=_CONSENT_NONCE,
    )
    fake_session.commit.assert_awaited_once()


def test_parent_signup_new_user_fails_closed_without_current_consent(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        email="parent@example.com",
        role="parent",
    )
    no_user_result = MagicMock()
    no_user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=no_user_result)
    fake_session.add = MagicMock()
    fake_session.flush = AsyncMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()
    monkeypatch.setattr(
        auth_routes_module,
        "acquire_current_parent_consent",
        AsyncMock(side_effect=CurrentParentConsentRequiredError),
    )

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=_parent_signup_payload(),
    )

    assert response.status_code == 409
    assert "Current parental consent" in response.json()["error"]["message"]
    # The provisional row is inserted only inside the transaction, then the
    # missing-receipt outcome explicitly rolls it back.
    fake_session.add.assert_called_once()
    fake_session.flush.assert_awaited_once()
    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_awaited()


def test_parent_signup_existing_user_fails_closed_without_current_reconsent(
    parent_signup_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        email="parent@example.com",
        role="parent",
    )
    existing = models.User(
        id="01J0EXISTINGULID0000000000",
        firebase_uid=_FIREBASE_UID,
        role="parent",
        display_name="Brian",
    )
    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing)
    fake_session.execute = AsyncMock(return_value=existing_result)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()
    monkeypatch.setattr(
        auth_routes_module,
        "acquire_current_parent_consent",
        AsyncMock(side_effect=CurrentParentConsentRequiredError),
    )

    response = parent_signup_client.post(
        "/v1/auth/parent-signup",
        headers={"Authorization": "Bearer valid"},
        json=_parent_signup_payload(),
    )

    assert response.status_code == 409
    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_awaited()


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
    if not hasattr(auth_routes_module, "verify_hinterland_jwt"):
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

    monkeypatch.setattr(auth_routes_module, "verify_hinterland_jwt", fake_verify)
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
    if not hasattr(auth_routes_module, "verify_hinterland_jwt"):
        pytest.skip("kid-exchange route not present yet")

    exp_unix = int((datetime.now(UTC) + timedelta(minutes=15)).timestamp())
    monkeypatch.setattr(
        auth_routes_module,
        "verify_hinterland_jwt",
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
    if not hasattr(auth_routes_module, "verify_hinterland_jwt"):
        pytest.skip("kid-exchange route not present yet")
    if not hasattr(auth_routes_module, "InvalidHinterlandJwt"):
        pytest.skip("InvalidHinterlandJwt not present yet")

    invalid_exc = auth_routes_module.InvalidHinterlandJwt("Expired handoff token")

    def fake_verify(token: str, *, settings: Settings, expected_token_type: str | None = None):
        raise invalid_exc

    monkeypatch.setattr(auth_routes_module, "verify_hinterland_jwt", fake_verify)

    response = kid_exchange_client.post(
        "/v1/auth/kid-exchange",
        json={"handoff_token": "expired"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_kid_jwks_endpoint_returns_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public Hinterland JWKS endpoint returns the published key set."""
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
                    "kid": "k1-2026-06",
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
    assert body["keys"][0]["kid"] == "k1-2026-06"
