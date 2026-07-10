"""Tests for canonical route-level user resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, resolve_current_user_row
from app.db import models


def _user(*, user_id: str = "01J0USERID000000000000ULID", role: str = "kid") -> models.User:
    return models.User(
        id=user_id,
        firebase_uid="legacy-firebase-uid",
        entra_oid="entra-oid-1",
        role=role,
        display_name="Tester",
    )


def _wire_user(fake_session: AsyncMock, user: models.User | None) -> None:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=user)
    fake_session.execute = AsyncMock(return_value=result)


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_resolves_real_hinterland_local_user_id(fake_session: AsyncMock) -> None:
    user = _user(user_id="01J0KIDID0000000000000ULID")
    _wire_user(fake_session, user)

    resolved = await resolve_current_user_row(
        fake_session,
        CurrentUser(uid="01J0KIDID0000000000000ULID", role="kid"),
    )

    assert resolved is user


async def test_resolves_real_entra_user_from_local_id_and_oid(fake_session: AsyncMock) -> None:
    user = _user(user_id="01J0PARENTID000000000ULID", role="parent")
    _wire_user(fake_session, user)

    resolved = await resolve_current_user_row(
        fake_session,
        CurrentUser(
            uid="01J0PARENTID000000000ULID",
            role="parent",
            entra_oid="entra-oid-1",
        ),
        allowed_roles=frozenset({"parent", "teacher"}),
    )

    assert resolved is user


async def test_keeps_legacy_firebase_uid_fallback(fake_session: AsyncMock) -> None:
    user = _user()
    _wire_user(fake_session, user)

    resolved = await resolve_current_user_row(
        fake_session,
        CurrentUser(uid="legacy-firebase-uid", role="kid"),
    )

    assert resolved is user


async def test_rejects_disallowed_role(fake_session: AsyncMock) -> None:
    _wire_user(fake_session, _user(role="kid"))

    with pytest.raises(HTTPException) as exc_info:
        await resolve_current_user_row(
            fake_session,
            CurrentUser(uid="01J0KIDID0000000000000ULID", role="kid"),
            allowed_roles=frozenset({"parent", "teacher"}),
        )

    assert exc_info.value.status_code == 403
