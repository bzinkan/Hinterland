"""Tier computation + cache upsert."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import geohash
import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.inat.client import InatUnavailable
from app.inat.observations import get_species_counts

log = structlog.get_logger()

Tier = Literal["abundant", "common", "rare", "epic", "legendary"]

# Per docs/rarity-pipeline.md "The algorithm"
_TIER_THRESHOLDS: tuple[tuple[Tier, float], ...] = (
    ("abundant", 0.20),
    ("common", 0.05),
    ("rare", 0.01),
    ("epic", 0.001),
    ("legendary", 0.0),
)

_LOW_DATA_THRESHOLD = 50  # cell N below this -> skip per-species tiering
_GEOHASH_PRECISION = 4


def tier_for_share(share: float) -> Tier:
    """Bucket a per-species share-of-cell into a tier."""
    for tier, threshold in _TIER_THRESHOLDS:
        if share >= threshold:
            return tier
    return "legendary"


def geohash_bbox(gh: str) -> tuple[float, float, float, float]:
    """Return (sw_lat, sw_lng, ne_lat, ne_lng) for a geohash cell."""
    lat, lng, lat_err, lng_err = geohash.decode_exactly(gh)
    return (lat - lat_err, lng - lng_err, lat + lat_err, lng + lng_err)


@dataclass(frozen=True)
class RegionRefreshResult:
    region: str
    species_count: int
    low_data: bool


async def discover_active_regions(session: AsyncSession) -> list[str]:
    """Return geohash-4 cells where Dragonfly has at least one observation.

    Phase 8 simplification: no `last 90 days` filter -- we're at zero-
    observations scale. Add the date predicate once we have meaningful
    historical depth.
    """
    rows = (
        await session.execute(
            select(models.Observation.geohash4)
            .where(models.Observation.geohash4.is_not(None))
            .distinct()
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def refresh_region(
    session: AsyncSession,
    inat_client: httpx.AsyncClient,
    region_geohash: str,
) -> RegionRefreshResult:
    """Pull species_counts for the cell, tier them, upsert to rarity_cache."""
    sw_lat, sw_lng, ne_lat, ne_lng = geohash_bbox(region_geohash)
    counts = await get_species_counts(
        inat_client,
        bbox_swlat=sw_lat,
        bbox_swlng=sw_lng,
        bbox_nelat=ne_lat,
        bbox_nelng=ne_lng,
    )
    n_total = sum(c.count for c in counts)
    if n_total < _LOW_DATA_THRESHOLD:
        # Don't write per-species tiers for sparse cells. RarityHandler
        # will fall back to geohash-3 (Phase 8 follow-up).
        log.info(
            "rarity.region.low_data",
            region=region_geohash,
            n_total=n_total,
            species_count=len(counts),
        )
        return RegionRefreshResult(region=region_geohash, species_count=0, low_data=True)

    now = datetime.now(UTC)
    upsert_rows = [
        {
            "id": f"{region_geohash}:{c.taxon_id}",
            "region_geohash": region_geohash,
            "taxon_id": c.taxon_id,
            "tier": tier_for_share(c.count / n_total),
            "observation_count": c.count,
            "iconic_taxon": c.iconic_taxon_name,
            "refreshed_at": now,
        }
        for c in counts
    ]

    if upsert_rows:
        stmt = pg_insert(models.RarityCache).values(upsert_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["region_geohash", "taxon_id"],
            set_={
                "tier": stmt.excluded.tier,
                "observation_count": stmt.excluded.observation_count,
                "iconic_taxon": stmt.excluded.iconic_taxon,
                "refreshed_at": stmt.excluded.refreshed_at,
            },
        )
        await session.execute(stmt)

    await session.commit()
    log.info(
        "rarity.region.refreshed",
        region=region_geohash,
        n_total=n_total,
        species_count=len(counts),
    )
    return RegionRefreshResult(region=region_geohash, species_count=len(counts), low_data=False)


async def run_refresh(
    session: AsyncSession,
    inat_client: httpx.AsyncClient,
) -> list[RegionRefreshResult]:
    """Single-pass nightly refresh.

    Yields per-region results. iNat outages on a single region log + skip
    that region; the next nightly run picks them up. We do NOT raise on
    individual-region failures because that would burn the whole run.
    """
    regions = await discover_active_regions(session)
    log.info("rarity.run.start", regions_total=len(regions))
    results: list[RegionRefreshResult] = []

    for region in regions:
        try:
            result = await refresh_region(session, inat_client, region)
            results.append(result)
        except InatUnavailable as exc:
            log.warning("rarity.region.inat_unavailable", region=region, reason=str(exc))
            continue

    log.info(
        "rarity.run.complete",
        regions_processed=len(results),
        regions_total=len(regions),
    )
    return results
