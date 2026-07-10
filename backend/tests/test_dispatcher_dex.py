"""Unit tests for DexHandler.

Verifies the first-find vs repeat-find branching, the atomic insert
contract, and the membership counter bump on first find.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.types import Context

_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID0000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid="fb-1", role="kid", display_name="Kid")


def _group() -> models.Group:
    return models.Group(id=_GROUP_ID, name="Family", join_code="ABC123", owner_user_id=_USER_ID)


def _obs(*, taxon_id: int | None = 12345) -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name="Northern Cardinal" if taxon_id else None,
    )
    obs.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return obs


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _ctx(fake_session: AsyncMock, *, taxon_id: int | None = 12345) -> Context:
    obs = _obs(taxon_id=taxon_id)
    return Context(
        db=fake_session,
        user=_user(),
        group=_group(),
        observation=obs,
        photo=models.Photo(
            id=_PHOTO_ID,
            user_id=_USER_ID,
            bucket="b",
            object_name=f"pending/{_PHOTO_ID}.jpg",
            status="pending",
        ),
    )


def _wire_session(fake_session: AsyncMock, *, inserted_id: str | None) -> None:
    """Stub the dex INSERT ... RETURNING to either return an id (first find)
    or None (ON CONFLICT fired -> repeat find)."""
    insert_result = MagicMock()
    insert_result.scalar_one_or_none = MagicMock(return_value=inserted_id)
    update_result = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[insert_result, update_result])
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------


async def test_first_find_emits_first_find_reward_and_bumps_counter(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, inserted_id="dex-row-1")
    handler = DexHandler()
    ctx = _ctx(fake_session)

    result = await handler.handle(ctx)

    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "first_find"
    assert reward.weight == 80
    assert reward.detail == "First Northern Cardinal in your Dex"
    assert reward.payload == {"taxon_id": 12345}
    assert result.state[DexHandler.STATE_IS_FIRST_FIND] is True

    # Two execute calls: dex insert + counter update.
    assert fake_session.execute.await_count == 2
    # The dispatcher owns the surrounding savepoint and commit.
    fake_session.commit.assert_not_awaited()


async def test_repeat_find_emits_repeat_reward_and_no_counter_bump(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, inserted_id=None)
    handler = DexHandler()
    ctx = _ctx(fake_session)

    result = await handler.handle(ctx)

    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "repeat_find"
    assert reward.weight == 10
    assert reward.detail == "Another Northern Cardinal"
    assert result.state[DexHandler.STATE_IS_FIRST_FIND] is False

    # Only the dex insert executes; no counter bump on repeat.
    assert fake_session.execute.await_count == 1
    fake_session.commit.assert_not_awaited()


async def test_observation_with_no_taxon_id_skips_handler(
    fake_session: AsyncMock,
) -> None:
    """No taxon means no Dex entry -- nothing meaningful for the handler to do."""
    handler = DexHandler()
    ctx = _ctx(fake_session, taxon_id=None)

    result = await handler.handle(ctx)

    assert result.rewards == []
    assert result.state[DexHandler.STATE_IS_FIRST_FIND] is False
    fake_session.execute.assert_not_called()
    fake_session.commit.assert_not_called()


async def test_first_find_falls_back_to_generic_detail_when_no_species_name(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, inserted_id="dex-row-2")
    handler = DexHandler()
    ctx = _ctx(fake_session)
    ctx.observation.species_name = None  # taxon known, but no display name yet

    result = await handler.handle(ctx)
    assert result.rewards[0].detail == "First this species in your Dex"
