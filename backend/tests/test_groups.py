from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.groups as groups_routes_module
import app.core.auth as auth_module
from app.api.routes.groups import _JOIN_CODE_ALPHABET, generate_join_code
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app

_FIREBASE_UID = "firebase-parent-001"
_USER_ID = "01J0PARENTID0000000000ULID"


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch, uid: str = _FIREBASE_UID) -> None:
    """Replace the Firebase verifier with one that accepts any token for `uid`."""

    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": uid, "email": "parent@example.com"}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client_with_session(session_mock: AsyncMock) -> Iterator[TestClient]:
    """Create a TestClient whose DB session dependency returns `session_mock`."""
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield session_mock

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def groups_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client_with_session(fake_session)


def _user_row(role: str = "parent") -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role=role,
        display_name="Brian",
    )


def _set_session_lookups(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    join_code_collision: bool = False,
) -> None:
    """Wire up `session.execute(...)` to return user lookup then join-code lookup."""
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    code_result = MagicMock()
    # Return a sentinel id when the code is "taken" (collision branch). When
    # there's no collision the result is None and the route accepts the
    # candidate code.
    code_result.scalar_one_or_none = MagicMock(
        return_value=("collide" if join_code_collision else None)
    )

    fake_session.execute = AsyncMock(side_effect=[user_result, code_result])
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()


# ---------------------------------------------------------------------------
# generate_join_code unit tests
# ---------------------------------------------------------------------------


def test_generate_join_code_is_six_chars_from_alphabet() -> None:
    code = generate_join_code()
    assert len(code) == 6
    assert all(ch in _JOIN_CODE_ALPHABET for ch in code)


def test_generate_join_code_excludes_ambiguous_chars() -> None:
    # Crockford base32 omits I, L, O, U so codes can be read aloud.
    forbidden = {"I", "L", "O", "U"}
    for _ in range(200):
        code = generate_join_code()
        assert not (set(code) & forbidden)


# ---------------------------------------------------------------------------
# POST /v1/groups
# ---------------------------------------------------------------------------


def test_create_group_requires_bearer_token(groups_client: TestClient) -> None:
    response = groups_client.post("/v1/groups", json={"name": "Family"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_group_validates_name(
    groups_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)

    response = groups_client.post(
        "/v1/groups",
        headers={"Authorization": "Bearer valid"},
        json={},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_group_returns_404_when_user_row_missing(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)

    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=user_result)

    response = groups_client.post(
        "/v1/groups",
        headers={"Authorization": "Bearer valid"},
        json={"name": "Family"},
    )

    assert response.status_code == 404
    assert "parent-signup" in response.json()["error"]["message"]


def test_create_group_rejects_kid_role(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    _set_session_lookups(fake_session, user=_user_row(role="kid"))

    response = groups_client.post(
        "/v1/groups",
        headers={"Authorization": "Bearer valid"},
        json={"name": "Sneaky Family"},
    )

    assert response.status_code == 403
    assert "'kid'" in response.json()["error"]["message"]
    fake_session.add.assert_not_called()


@pytest.mark.parametrize("role", ["parent", "teacher"])
def test_create_group_happy_path(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    _stub_token_verifier(monkeypatch)
    _set_session_lookups(fake_session, user=_user_row(role=role))

    response = groups_client.post(
        "/v1/groups",
        headers={"Authorization": "Bearer valid"},
        json={"name": f"{role.capitalize()} Group"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == f"{role.capitalize()} Group"
    assert body["owner_user_id"] == _USER_ID
    assert len(body["join_code"]) == 6
    assert all(ch in _JOIN_CODE_ALPHABET for ch in body["join_code"])
    assert isinstance(body["id"], str) and len(body["id"]) == 26  # ULID

    # Group + Membership added; both share the new group's id.
    assert fake_session.add.call_count == 2
    added_group: models.Group = fake_session.add.call_args_list[0].args[0]
    added_membership: models.Membership = fake_session.add.call_args_list[1].args[0]
    assert added_group.name == f"{role.capitalize()} Group"
    assert added_group.owner_user_id == _USER_ID
    assert added_group.join_code == body["join_code"]
    assert added_membership.group_id == added_group.id
    assert added_membership.user_id == _USER_ID
    assert added_membership.role == role
    fake_session.commit.assert_awaited_once()
    fake_session.refresh.assert_awaited_once_with(added_group)


# ---------------------------------------------------------------------------
# POST /v1/groups/{group_id}/kids
# ---------------------------------------------------------------------------

_GROUP_ID = "01J0GROUPIDABCDEFGHIJKLMNO"
_KID_FIREBASE_UID = "firebase-kid-xyz"
_KID_CUSTOM_TOKEN = "fake-custom-token-for-kid"


def _group_row(*, owner_user_id: str = _USER_ID) -> models.Group:
    return models.Group(
        id=_GROUP_ID,
        name="Family",
        join_code="ABC123",
        owner_user_id=owner_user_id,
    )


def _stub_firebase_admin(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Patch Firebase Admin wrappers used by the kid-create route.

    Returns a dict of call logs keyed by function name.
    """
    calls: dict[str, list[object]] = {
        "create_user": [],
        "set_claims": [],
        "create_token": [],
        "delete_user": [],
    }

    def fake_create_user(*, display_name: str, settings: Settings) -> str:
        calls["create_user"].append(display_name)
        return _KID_FIREBASE_UID

    def fake_set_claims(uid: str, claims: dict[str, object], settings: Settings) -> None:
        calls["set_claims"].append((uid, claims))

    def fake_create_token(uid: str, settings: Settings) -> str:
        calls["create_token"].append(uid)
        return _KID_CUSTOM_TOKEN

    def fake_delete_user(uid: str, settings: Settings) -> None:
        calls["delete_user"].append(uid)

    monkeypatch.setattr(groups_routes_module, "create_firebase_user", fake_create_user)
    monkeypatch.setattr(groups_routes_module, "set_firebase_custom_claims", fake_set_claims)
    monkeypatch.setattr(groups_routes_module, "create_firebase_custom_token", fake_create_token)
    monkeypatch.setattr(groups_routes_module, "delete_firebase_user", fake_delete_user)
    return calls


def _set_kid_session_lookups(
    fake_session: AsyncMock,
    *,
    caller: models.User | None,
    group: models.Group | None,
) -> None:
    """Wire `session.execute(...)` for caller lookup, then group lookup."""
    caller_result = MagicMock()
    caller_result.scalar_one_or_none = MagicMock(return_value=caller)

    group_result = MagicMock()
    group_result.scalar_one_or_none = MagicMock(return_value=group)

    fake_session.execute = AsyncMock(side_effect=[caller_result, group_result])
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()


def test_create_kid_requires_bearer_token(groups_client: TestClient) -> None:
    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_kid_validates_age_band(
    groups_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    _stub_firebase_admin(monkeypatch)

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "5-8"},  # invalid
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_kid_returns_404_when_caller_user_row_missing(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    _stub_firebase_admin(monkeypatch)

    caller_result = MagicMock()
    caller_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=caller_result)

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 404


def test_create_kid_rejects_kid_caller_role(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    fb_calls = _stub_firebase_admin(monkeypatch)
    _set_kid_session_lookups(
        fake_session,
        caller=_user_row(role="kid"),
        group=_group_row(),
    )

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 403
    assert "'kid'" in response.json()["error"]["message"]
    assert fb_calls["create_user"] == []  # no Firebase user created


def test_create_kid_returns_404_when_group_missing(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    fb_calls = _stub_firebase_admin(monkeypatch)
    _set_kid_session_lookups(
        fake_session,
        caller=_user_row(role="parent"),
        group=None,
    )

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 404
    assert _GROUP_ID in response.json()["error"]["message"]
    assert fb_calls["create_user"] == []


def test_create_kid_rejects_non_owner(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    fb_calls = _stub_firebase_admin(monkeypatch)
    other_owner_id = "01J0OTHEROWNERID00000000UL"
    _set_kid_session_lookups(
        fake_session,
        caller=_user_row(role="parent"),
        group=_group_row(owner_user_id=other_owner_id),
    )

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 403
    assert fb_calls["create_user"] == []


def test_create_kid_happy_path(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    fb_calls = _stub_firebase_admin(monkeypatch)
    _set_kid_session_lookups(
        fake_session,
        caller=_user_row(role="parent"),
        group=_group_row(),
    )

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["firebase_uid"] == _KID_FIREBASE_UID
    assert body["display_name"] == "Sparrow"
    assert body["age_band"] == "9-10"
    assert body["custom_token"] == _KID_CUSTOM_TOKEN
    assert isinstance(body["id"], str) and len(body["id"]) == 26  # ULID

    # Firebase Admin called in the right order with the right args
    assert fb_calls["create_user"] == ["Sparrow"]
    assert len(fb_calls["set_claims"]) == 1
    set_uid, set_claims = fb_calls["set_claims"][0]
    assert set_uid == _KID_FIREBASE_UID
    assert set_claims == {
        "role": "kid",
        "group_id": _GROUP_ID,
        "parent_user_id": _USER_ID,
    }
    assert fb_calls["create_token"] == [_KID_FIREBASE_UID]
    assert fb_calls["delete_user"] == []  # no cleanup needed on success

    # users + memberships rows added
    assert fake_session.add.call_count == 2
    added_kid: models.User = fake_session.add.call_args_list[0].args[0]
    added_membership: models.Membership = fake_session.add.call_args_list[1].args[0]
    assert added_kid.firebase_uid == _KID_FIREBASE_UID
    assert added_kid.role == "kid"
    assert added_kid.display_name == "Sparrow"
    assert added_kid.age_band == "9-10"
    assert added_kid.parent_user_id == _USER_ID
    assert added_membership.group_id == _GROUP_ID
    assert added_membership.user_id == added_kid.id
    assert added_membership.role == "kid"
    fake_session.commit.assert_awaited_once()
    fake_session.refresh.assert_awaited_once_with(added_kid)


def test_create_kid_cleans_up_firebase_user_on_db_failure(
    groups_client: TestClient,
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_token_verifier(monkeypatch)
    fb_calls = _stub_firebase_admin(monkeypatch)
    _set_kid_session_lookups(
        fake_session,
        caller=_user_row(role="parent"),
        group=_group_row(),
    )
    fake_session.commit = AsyncMock(side_effect=RuntimeError("simulated DB failure"))

    response = groups_client.post(
        f"/v1/groups/{_GROUP_ID}/kids",
        headers={"Authorization": "Bearer valid"},
        json={"display_name": "Sparrow", "age_band": "9-10"},
    )

    assert response.status_code == 500
    # Firebase user was created, then deleted on the cleanup path.
    assert fb_calls["create_user"] == ["Sparrow"]
    assert fb_calls["delete_user"] == [_KID_FIREBASE_UID]
    # Custom token was NOT minted (failure happens before that step).
    assert fb_calls["create_token"] == []
