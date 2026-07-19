from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.routes.groups as groups_routes
from app.api.routes.groups import AdultInviteRedeemRequest, GroupJoinRequest
from app.core.auth import CurrentUser
from app.core.config import Settings
from app.db import models

_OWNER_ID = "01J0OWNER00000000000000000"
_PARENT_ID = "01J0PARENT0000000000000000"
_GROUP_ID = "01J0GROUP00000000000000000"


def _parent(user_id: str = _OWNER_ID) -> models.User:
    return models.User(
        id=user_id,
        firebase_uid=f"firebase-{user_id}",
        role="parent",
        display_name="Parent",
    )


def _group(*, sharing_started: bool = False) -> models.Group:
    return models.Group(
        id=_GROUP_ID,
        name="Saturday Nature Club",
        join_code="ABC123",
        owner_user_id=_OWNER_ID,
        shared_groups_enabled_at=(datetime.now(UTC) if sharing_started else None),
    )


def _current(user_id: str = _OWNER_ID) -> CurrentUser:
    return CurrentUser(uid=user_id, id=user_id, role="parent")


def _shared_settings(enabled: bool = True) -> Settings:
    return Settings(
        env="local",
        app_version="shared-groups-test",
        shared_groups_enabled=enabled,
        parent_web_base_url="https://parents.example.test",
    )


@pytest.fixture
def session() -> AsyncMock:
    value = AsyncMock(spec=AsyncSession)
    value.add = MagicMock()
    value.commit = AsyncMock()
    value.flush = AsyncMock()

    async def refresh(row: object) -> None:
        if getattr(row, "created_at", None) is None:
            row.created_at = datetime.now(UTC)  # type: ignore[attr-defined]

    value.refresh = AsyncMock(side_effect=refresh)
    return value


async def test_first_invite_durably_retires_join_code_and_stores_only_digest(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _parent()
    group = _group()
    token = "invite-secret-" + "x" * 32
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=owner))
    monkeypatch.setattr(groups_routes, "_load_owned_group", AsyncMock(return_value=group))
    monkeypatch.setattr(groups_routes.secrets, "token_urlsafe", lambda _n: token)

    response = await groups_routes.create_adult_invitation(
        group.id, _current(), session, _shared_settings(), Response()
    )

    assert group.shared_groups_enabled_at is not None
    invite = session.add.call_args.args[0]
    assert isinstance(invite, models.GroupAdultInvite)
    assert invite.token_sha256 != token
    assert len(invite.token_sha256) == 64
    assert invite.expires_at - group.shared_groups_enabled_at == timedelta(hours=72)
    assert response.invite_url == (f"https://parents.example.test/group-invite#token={token}")
    assert token not in response.model_dump_json(exclude={"invite_url"})


async def test_invite_list_is_bounded_to_newest_one_hundred(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        groups_routes, "resolve_current_user_row", AsyncMock(return_value=_parent())
    )
    monkeypatch.setattr(groups_routes, "_load_owned_group", AsyncMock(return_value=_group()))
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)

    response = await groups_routes.list_adult_invitations(
        _GROUP_ID, _current(), session, _shared_settings(), Response()
    )

    assert response.items == []
    statement = session.execute.await_args.args[0]
    sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "limit 100" in sql


async def test_started_group_can_list_and_revoke_invites_after_flag_rollback(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _parent()
    group = _group(sharing_started=True)
    now = datetime.now(UTC)
    invite = models.GroupAdultInvite(
        id="01J0INVITE0000000000000000",
        group_id=group.id,
        created_by_user_id=owner.id,
        token_sha256="a" * 64,
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=owner))
    monkeypatch.setattr(groups_routes, "_load_owned_group", AsyncMock(return_value=group))
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [invite]
    revoke_result = MagicMock()
    revoke_result.scalar_one_or_none.return_value = invite
    session.execute = AsyncMock(side_effect=[list_result, revoke_result])

    listed = await groups_routes.list_adult_invitations(
        group.id,
        _current(),
        session,
        _shared_settings(enabled=False),
        Response(),
    )
    revoked = await groups_routes.revoke_adult_invitation(
        group.id,
        invite.id,
        _current(),
        session,
        _shared_settings(enabled=False),
    )

    assert [item.id for item in listed.items] == [invite.id]
    assert revoked.status_code == 204
    assert invite.revoked_at is not None
    assert invite.revoked_by_user_id == owner.id
    session.commit.assert_awaited_once()


async def test_flag_rollback_denies_new_invite_creation_and_redemption(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _parent()
    group = _group(sharing_started=True)
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=owner))
    monkeypatch.setattr(groups_routes, "_load_owned_group", AsyncMock(return_value=group))

    with pytest.raises(HTTPException) as create_error:
        await groups_routes.create_adult_invitation(
            group.id,
            _current(),
            session,
            _shared_settings(enabled=False),
            Response(),
        )
    assert create_error.value.status_code == 404
    session.add.assert_not_called()

    parent = _parent(_PARENT_ID)
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    digest_result = MagicMock()
    digest_result.scalar_one_or_none.return_value = group.id
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = group
    session.execute = AsyncMock(side_effect=[digest_result, group_result])
    with pytest.raises(HTTPException) as redeem_error:
        await groups_routes.redeem_adult_invitation(
            AdultInviteRedeemRequest(token="x" * 43),
            _current(parent.id),
            session,
            _shared_settings(enabled=False),
        )
    assert redeem_error.value.status_code == 404
    assert session.execute.await_count == 2


async def test_same_parent_invite_replay_is_minimized_and_idempotent(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent(_PARENT_ID)
    now = datetime.now(UTC)
    invite = models.GroupAdultInvite(
        id="01J0INVITE0000000000000000",
        group_id=_GROUP_ID,
        created_by_user_id=_OWNER_ID,
        token_sha256="a" * 64,
        expires_at=now + timedelta(hours=1),
        redeemed_at=now,
        redeemed_by_user_id=parent.id,
    )
    membership = models.Membership(
        id="01J0MEMBERSHIP000000000000",
        group_id=_GROUP_ID,
        user_id=parent.id,
        role="parent",
        status="active",
    )
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    digest_result = MagicMock()
    digest_result.scalar_one_or_none.return_value = invite.group_id
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    invite_result = MagicMock()
    invite_result.scalar_one_or_none.return_value = invite
    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership
    session.execute = AsyncMock(
        side_effect=[digest_result, group_result, invite_result, membership_result]
    )

    response = await groups_routes.redeem_adult_invitation(
        AdultInviteRedeemRequest(token="x" * 43),
        _current(parent.id),
        session,
        _shared_settings(),
    )

    assert response.model_dump() == {
        "group_id": _GROUP_ID,
        "joined": True,
        "replayed": True,
    }
    assert "user_id" not in response.model_dump()
    assert "membership_id" not in response.model_dump()
    session.commit.assert_not_awaited()
    statements = [call.args[0] for call in session.execute.await_args_list]
    compiled = [
        str(statement.compile(dialect=postgresql.dialect())).upper() for statement in statements
    ]
    assert "FOR UPDATE" not in compiled[0]
    assert "FROM GROUPS" in compiled[1] and "FOR UPDATE" in compiled[1]
    assert "FROM GROUP_ADULT_INVITES" in compiled[2] and "FOR UPDATE" in compiled[2]
    assert "FROM MEMBERSHIPS" in compiled[3] and "FOR UPDATE" in compiled[3]


async def test_forwarded_redeemed_invite_conflicts_without_joining(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forwarded_to = _parent(_PARENT_ID)
    now = datetime.now(UTC)
    invite = models.GroupAdultInvite(
        id="01J0INVITE0000000000000000",
        group_id=_GROUP_ID,
        created_by_user_id=_OWNER_ID,
        token_sha256="a" * 64,
        expires_at=now + timedelta(hours=1),
        redeemed_at=now,
        redeemed_by_user_id="01J0SOMEONEELSE0000000000",
    )
    monkeypatch.setattr(
        groups_routes,
        "resolve_current_user_row",
        AsyncMock(return_value=forwarded_to),
    )
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    digest_result = MagicMock()
    digest_result.scalar_one_or_none.return_value = invite.group_id
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    invite_result = MagicMock()
    invite_result.scalar_one_or_none.return_value = invite
    session.execute = AsyncMock(side_effect=[digest_result, group_result, invite_result])

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.redeem_adult_invitation(
            AdultInviteRedeemRequest(token="x" * 43),
            _current(forwarded_to.id),
            session,
            _shared_settings(),
        )
    assert exc_info.value.status_code == 409
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("terminal_state", ["expired", "revoked"])
async def test_expired_or_revoked_invite_cannot_create_membership(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    parent = _parent(_PARENT_ID)
    now = datetime.now(UTC)
    invite = models.GroupAdultInvite(
        id="01J0INVITE0000000000000000",
        group_id=_GROUP_ID,
        created_by_user_id=_OWNER_ID,
        token_sha256="a" * 64,
        expires_at=(
            now - timedelta(seconds=1) if terminal_state == "expired" else now + timedelta(hours=1)
        ),
        revoked_at=(now if terminal_state == "revoked" else None),
        revoked_by_user_id=(_OWNER_ID if terminal_state == "revoked" else None),
    )
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    digest_result = MagicMock()
    digest_result.scalar_one_or_none.return_value = invite.group_id
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    invite_result = MagicMock()
    invite_result.scalar_one_or_none.return_value = invite
    session.execute = AsyncMock(side_effect=[digest_result, group_result, invite_result])

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.redeem_adult_invitation(
            AdultInviteRedeemRequest(token="x" * 43),
            _current(parent.id),
            session,
            _shared_settings(),
        )

    assert exc_info.value.status_code == 410
    assert session.execute.await_count == 3
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


async def test_legacy_join_stays_retired_after_feature_flag_rollback(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        groups_routes, "resolve_current_user_row", AsyncMock(return_value=_parent())
    )
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    session.execute = AsyncMock(return_value=group_result)

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.join_group(
            GroupJoinRequest(join_code="ABC123"),
            _current(),
            session,
            _shared_settings(enabled=False),
        )
    assert exc_info.value.status_code == 410
    session.add.assert_not_called()


async def test_adult_removal_is_hidden_while_shared_groups_disabled(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        groups_routes, "resolve_current_user_row", AsyncMock(return_value=_parent())
    )
    monkeypatch.setattr(groups_routes, "_load_owned_group", AsyncMock(return_value=_group()))

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.remove_adult_member(
            _GROUP_ID,
            "a" * 32,
            _current(),
            session,
            _shared_settings(enabled=False),
        )
    assert exc_info.value.status_code == 404
    session.execute.assert_not_awaited()


async def test_repeated_adult_removal_repairs_surviving_active_child(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _parent()
    removed_parent = _parent(_PARENT_ID)
    removed_membership = models.Membership(
        id="01J0REMOVEDADULTMEMBERSHIP0",
        group_id=_GROUP_ID,
        user_id=removed_parent.id,
        role="parent",
        status="left",
        left_at=datetime.now(UTC) - timedelta(minutes=1),
        management_ref="a" * 32,
    )
    child = models.User(
        id="01J0SURVIVINGCHILD000000001",
        firebase_uid=None,
        role="kid",
        display_name="Aster",
        age_band="9-10",
        parent_user_id=removed_parent.id,
    )
    child_membership = models.Membership(
        id="01J0SURVIVINGMEMBERSHIP0001",
        group_id=_GROUP_ID,
        user_id=child.id,
        role="kid",
        status="active",
        session_version=4,
    )
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=owner))
    monkeypatch.setattr(
        groups_routes,
        "_load_owned_group",
        AsyncMock(return_value=_group(sharing_started=True)),
    )
    busted: list[str] = []
    monkeypatch.setattr(
        groups_routes,
        "bust_user_cache",
        lambda user_id, **_kwargs: busted.append(user_id),
    )
    adult_result = MagicMock()
    adult_result.one_or_none.return_value = (removed_membership, removed_parent)
    children_result = MagicMock()
    children_result.all.return_value = [(child_membership, child)]
    session.execute = AsyncMock(side_effect=[adult_result, children_result])

    response = await groups_routes.remove_adult_member(
        _GROUP_ID,
        removed_membership.management_ref,
        _current(),
        session,
        _shared_settings(enabled=False),
    )

    assert response.status_code == 204
    assert removed_membership.status == "left"
    assert child_membership.status == "left"
    assert child_membership.left_at is not None
    assert child_membership.session_version == 5
    session.commit.assert_awaited_once()
    assert busted == [removed_parent.id, child.id]


def test_started_group_permissions_separate_invite_creation_from_safety_controls() -> None:
    owner = _parent()
    permissions = groups_routes._group_permissions(
        _group(sharing_started=True),
        owner,
        _shared_settings(enabled=False),
    )

    assert permissions.can_invite_parents is False
    assert permissions.can_manage_invitations is True
    assert permissions.can_remove_adults is True


async def test_rehome_locks_group_before_rechecking_parent_membership(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent(_PARENT_ID)
    child = models.User(
        id="01J0OWNCHILD00000000000001",
        firebase_uid=None,
        role="kid",
        display_name="Fern",
        age_band="11-12",
        parent_user_id=parent.id,
    )
    adult_membership = models.Membership(
        id="01J0ADULTMEMBERSHIP00000000",
        group_id=_GROUP_ID,
        user_id=parent.id,
        role="parent",
        status="active",
    )
    previous_membership = models.Membership(
        id="01J0CHILDMEMBERSHIP00000000",
        group_id=_GROUP_ID,
        user_id=child.id,
        role="kid",
        status="left",
        left_at=datetime.now(UTC),
        session_version=2,
    )
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    adult_result = MagicMock()
    adult_result.scalar_one_or_none.return_value = adult_membership
    child_result = MagicMock()
    child_result.scalar_one_or_none.return_value = child
    memberships_result = MagicMock()
    memberships_result.scalars.return_value.all.return_value = [previous_membership]
    session.execute = AsyncMock(
        side_effect=[group_result, adult_result, child_result, memberships_result]
    )

    response = await groups_routes.place_existing_child_in_group(
        _GROUP_ID,
        child.id,
        _current(parent.id),
        session,
        _shared_settings(),
    )

    assert response.status_code == 204
    statements = [call.args[0] for call in session.execute.await_args_list]
    compiled = [
        str(statement.compile(dialect=postgresql.dialect())).upper() for statement in statements
    ]
    assert "FROM GROUPS" in compiled[0] and "FOR UPDATE" in compiled[0]
    assert "FROM MEMBERSHIPS" in compiled[1]
    assert previous_membership.status == "active"
    assert previous_membership.left_at is None
    assert previous_membership.session_version == 3


async def test_removed_parent_cannot_rehome_child(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent(_PARENT_ID)
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = _group(sharing_started=True)
    removed_membership_result = MagicMock()
    removed_membership_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(side_effect=[group_result, removed_membership_result])

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.place_existing_child_in_group(
            _GROUP_ID,
            "01J0OWNCHILD00000000000001",
            _current(parent.id),
            session,
            _shared_settings(),
        )

    assert exc_info.value.status_code == 404
    assert (
        "FOR UPDATE"
        in str(
            session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect())
        ).upper()
    )
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


async def test_archived_group_blocks_invite_before_invite_lock_or_membership_write(
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent(_PARENT_ID)
    archived_group = _group(sharing_started=True)
    archived_group.archived_at = datetime.now(UTC)
    monkeypatch.setattr(groups_routes, "resolve_current_user_row", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        groups_routes,
        "require_linked_current_parent_consent",
        AsyncMock(return_value=MagicMock()),
    )
    digest_result = MagicMock()
    digest_result.scalar_one_or_none.return_value = _GROUP_ID
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = archived_group
    session.execute = AsyncMock(side_effect=[digest_result, group_result])

    with pytest.raises(HTTPException) as exc_info:
        await groups_routes.redeem_adult_invitation(
            AdultInviteRedeemRequest(token="x" * 43),
            _current(parent.id),
            session,
            _shared_settings(),
        )

    assert exc_info.value.status_code == 410
    assert session.execute.await_count == 2
    first_sql, group_sql = [
        str(call.args[0].compile(dialect=postgresql.dialect())).upper()
        for call in session.execute.await_args_list
    ]
    assert "FOR UPDATE" not in first_sql
    assert "FROM GROUPS" in group_sql and "FOR UPDATE" in group_sql
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
