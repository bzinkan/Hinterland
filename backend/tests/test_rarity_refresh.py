"""Tests for the rarity refresh module.

Covers the pure tier-computation function exhaustively; uses respx for
the iNat species_counts call inside refresh_region; and stubs the
SQLAlchemy session for the refresh-region orchestration tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.inat.client import InatUnavailable, build_inat_client
from app.rarity.refresh import (
    discover_active_regions,
    refresh_region,
    run_refresh,
    tier_for_share,
)

# ---------------------------------------------------------------------------
# tier_for_share
# ---------------------------------------------------------------------------


def test_tier_for_share_thresholds() -> None:
    # >= 20% -> abundant
    assert tier_for_share(0.50) == "abundant"
    assert tier_for_share(0.20) == "abundant"
    # 5-20% -> common
    assert tier_for_share(0.19) == "common"
    assert tier_for_share(0.05) == "common"
    # 1-5% -> rare
    assert tier_for_share(0.049) == "rare"
    assert tier_for_share(0.01) == "rare"
    # 0.1-1% -> epic
    assert tier_for_share(0.009) == "epic"
    assert tier_for_share(0.001) == "epic"
    # below 0.1% -> legendary
    assert tier_for_share(0.0001) == "legendary"


# ---------------------------------------------------------------------------
# discover_active_regions
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_discover_returns_distinct_geohash4(fake_session: AsyncMock) -> None:
    result = MagicMock()
    result.all = MagicMock(return_value=[("dnp1",), ("dnp1",), ("dnp2",), (None,)])
    fake_session.execute = AsyncMock(return_value=result)

    # The query already does DISTINCT + WHERE NOT NULL; we just verify
    # the function returns the iterable shape it should.
    regions = await discover_active_regions(fake_session)
    assert "dnp1" in regions
    assert "dnp2" in regions
    assert None not in regions


# ---------------------------------------------------------------------------
# refresh_region
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local", inat_oauth_token="test-token")


@pytest.fixture
def inat_client(settings: Settings) -> httpx.AsyncClient:
    return build_inat_client(settings)


@respx.mock
async def test_refresh_region_low_data_skips_upsert(
    fake_session: AsyncMock, inat_client: httpx.AsyncClient
) -> None:
    """Cell with N < 50 -> no per-species rows written, low_data=True."""
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"taxon": {"id": 1}, "count": 10},
                    {"taxon": {"id": 2}, "count": 20},
                ]
            },
        )
    )
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    result = await refresh_region(fake_session, inat_client, "dnp1")
    assert result.low_data is True
    assert result.species_count == 0
    fake_session.commit.assert_not_called()  # no upsert


@respx.mock
async def test_refresh_region_writes_tiered_rows(
    fake_session: AsyncMock, inat_client: httpx.AsyncClient
) -> None:
    """Healthy cell: tier each species by share, upsert."""
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"taxon": {"id": 1}, "count": 60},  # 60% -> abundant
                    {"taxon": {"id": 2}, "count": 30},  # 30% -> abundant
                    {"taxon": {"id": 3}, "count": 8},  # 8%  -> common
                    {"taxon": {"id": 4}, "count": 2},  # 2%  -> rare
                ]
            },
        )
    )
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    result = await refresh_region(fake_session, inat_client, "dnp1")
    assert result.low_data is False
    assert result.species_count == 4
    fake_session.execute.assert_awaited_once()
    fake_session.commit.assert_awaited_once()


@respx.mock
async def test_refresh_region_inat_5xx_raises(
    fake_session: AsyncMock, inat_client: httpx.AsyncClient
) -> None:
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(InatUnavailable):
        await refresh_region(fake_session, inat_client, "dnp1")


# ---------------------------------------------------------------------------
# run_refresh -- end-to-end orchestration
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_refresh_isolates_per_region_failures(
    inat_client: httpx.AsyncClient,
) -> None:
    """If region A iNat-fails, region B still gets processed."""
    fake_session = AsyncMock(spec=AsyncSession)

    # discover_active_regions returns ["dnp1", "dnp2"]
    regions_result = MagicMock()
    regions_result.all = MagicMock(return_value=[("dnp1",), ("dnp2",)])
    # commit + execute mocked beyond that point
    fake_session.execute = AsyncMock(side_effect=[regions_result, AsyncMock()])
    fake_session.commit = AsyncMock()

    # First region 503, second region returns enough counts to be
    # low_data (no commit on that path).
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200,
                json={"results": [{"taxon": {"id": 1}, "count": 5}]},
            ),
        ]
    )

    results = await run_refresh(fake_session, inat_client)
    # First region failed; second succeeded but was low_data
    assert len(results) == 1
    assert results[0].region == "dnp2"
    assert results[0].low_data is True
