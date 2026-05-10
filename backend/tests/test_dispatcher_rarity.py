"""Unit tests for RarityHandler."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.handlers.rarity import RarityHandler
from app.dispatcher.types import Context

_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID0000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid="fb-1", role="kid", display_name="Kid")


def _group() -> models.Group:
    return models.Group(id=_GROUP_ID, name="Family", join_code="ABC123", owner_user_id=_USER_ID)


def _obs(*, taxon_id: int | None = 12345, geohash4: str | None = "dnp1") -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name="Northern Cardinal" if taxon_id else None,
        geohash4=geohash4,
    )
    obs.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return obs


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _ctx(
    fake_session: AsyncMock,
    *,
    taxon_id: int | None = 12345,
    geohash4: str | None = "dnp1",
    with_group: bool = True,
) -> Context:
    obs = _obs(taxon_id=taxon_id, geohash4=geohash4)
    return Context(
        db=fake_session,
        user=_user(),
        group=_group() if with_group else None,
        observation=obs,
        photo=models.Photo(
            id=_PHOTO_ID,
            user_id=_USER_ID,
            bucket="b",
            object_name=f"observations/{_PHOTO_ID}.jpg",
            status="clean",
        ),
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    species_row: models.RarityCache | None,
    region_seen: bool = True,
) -> None:
    """Stub the species-by-(region, taxon) lookup, the region-existence
    fallback, and (when applicable) the rarest_tier UPDATE."""
    species_result = MagicMock()
    species_result.scalar_one_or_none = MagicMock(return_value=species_row)

    region_result = MagicMock()
    region_result.scalar_one_or_none = MagicMock(return_value="dnp1" if region_seen else None)

    update_result = MagicMock()

    side_effects: list[object] = [species_result]
    if species_row is None:
        side_effects.append(region_result)
    side_effects.append(update_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


def _rarity_row(tier: str) -> models.RarityCache:
    return models.RarityCache(
        id=f"dnp1:{12345}",
        region_geohash="dnp1",
        taxon_id=12345,
        tier=tier,
        observation_count=1,
        refreshed_at=datetime(2026, 5, 9, 3, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------


async def test_skips_when_no_taxon_id(fake_session: AsyncMock) -> None:
    handler = RarityHandler()
    ctx = _ctx(fake_session, taxon_id=None)
    result = await handler.handle(ctx)
    assert result.rewards == []
    fake_session.execute.assert_not_called()


async def test_skips_when_no_geohash4(fake_session: AsyncMock) -> None:
    handler = RarityHandler()
    ctx = _ctx(fake_session, geohash4=None)
    result = await handler.handle(ctx)
    assert result.rewards == []
    fake_session.execute.assert_not_called()


async def test_legendary_emits_high_weight_reward(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, species_row=_rarity_row("legendary"))
    handler = RarityHandler()
    ctx = _ctx(fake_session)

    result = await handler.handle(ctx)
    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "rarity_tier"
    assert reward.weight == 60
    assert reward.payload == {"tier": "legendary", "region": "dnp1"}
    assert result.state["tier"] == "legendary"


async def test_rare_emits_mid_weight_reward(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, species_row=_rarity_row("rare"))
    handler = RarityHandler()
    result = await handler.handle(_ctx(fake_session))
    assert result.rewards[0].weight == 40


async def test_common_emits_low_weight_reward(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, species_row=_rarity_row("common"))
    handler = RarityHandler()
    result = await handler.handle(_ctx(fake_session))
    assert result.rewards[0].weight == 10


async def test_abundant_suppressed_no_reward(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, species_row=_rarity_row("abundant"))
    handler = RarityHandler()
    result = await handler.handle(_ctx(fake_session))
    # Per docs/dispatcher.md: abundant tier emits no reward.
    assert result.rewards == []
    # But the rarest_tier update still ran with abundant as observed_tier.
    assert result.state["tier"] == "abundant"


async def test_unrecorded_emits_weight_100_reward(fake_session: AsyncMock) -> None:
    """Region has data but our species isn't in it -> first-in-region."""
    _wire_session(fake_session, species_row=None, region_seen=True)
    handler = RarityHandler()

    result = await handler.handle(_ctx(fake_session))
    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "unrecorded"
    assert reward.weight == 100
    assert result.state["tier"] == "unrecorded"


async def test_cold_start_region_no_rewards(fake_session: AsyncMock) -> None:
    """Region has zero rarity_cache rows -> can't say anything, skip both."""
    _wire_session(fake_session, species_row=None, region_seen=False)
    handler = RarityHandler()

    result = await handler.handle(_ctx(fake_session))
    assert result.rewards == []
    assert result.state["tier"] is None
