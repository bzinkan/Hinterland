"""Read-through cache for iNat taxa.

Hits the local `species_cache` Postgres table first; on miss, fetches from
iNat and writes a row. Per ADR 0006, taxa changes rarely so an entry
without an `expires_at` is treated as fresh indefinitely. Callers that
want a refresh should `delete()` the row first.

The cache is also our species-name source for observation rows -- when a
kid picks an iNat-suggested taxon, we look up the taxon here to populate
`observations.species_name` consistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.inat.taxa import get_taxon

log = structlog.get_logger()


@dataclass(frozen=True)
class CachedSpecies:
    taxon_id: int
    scientific_name: str | None
    common_name: str | None
    iconic_taxon: str | None
    ancestor_ids: tuple[int, ...]


def ancestor_ids_from_payload(payload: object, *, taxon_id: int) -> tuple[int, ...]:
    """Extract the iNat ancestor chain from a raw `/v1/taxa` payload.

    iNat sometimes includes the taxon's own id as the last element of
    `ancestor_ids`; we exclude it so the result is strictly ancestors.
    Non-int entries (including bools, which are ints to `isinstance`)
    are dropped. Returns () for any payload that isn't a dict with a
    list under "ancestor_ids".
    """
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("ancestor_ids")
    if not isinstance(raw, list):
        return ()
    return tuple(x for x in raw if isinstance(x, int) and not isinstance(x, bool) and x != taxon_id)


async def get_or_fill(
    session: AsyncSession,
    inat_client: httpx.AsyncClient,
    taxon_id: int,
) -> CachedSpecies | None:
    """Return the species, fetching + caching from iNat if not present."""
    row = (
        await session.execute(
            select(models.SpeciesCache).where(models.SpeciesCache.taxon_id == taxon_id)
        )
    ).scalar_one_or_none()
    if row is not None:
        return CachedSpecies(
            taxon_id=row.taxon_id,
            scientific_name=row.scientific_name,
            common_name=row.common_name,
            iconic_taxon=row.iconic_taxon,
            ancestor_ids=ancestor_ids_from_payload(row.source_payload, taxon_id=row.taxon_id),
        )

    info = await get_taxon(inat_client, taxon_id)
    if info is None:
        return None

    cached = models.SpeciesCache(
        taxon_id=taxon_id,
        scientific_name=info.scientific_name,
        common_name=info.common_name,
        iconic_taxon=info.iconic_taxon,
        source_payload=info.raw,
    )
    session.add(cached)
    try:
        await session.flush()
    except Exception:
        # Concurrent writer beat us. Roll back the partial flush and
        # re-read; whatever they wrote is acceptable.
        await session.rollback()
        row = (
            await session.execute(
                select(models.SpeciesCache).where(models.SpeciesCache.taxon_id == taxon_id)
            )
        ).scalar_one_or_none()
        if row is None:
            log.warning("species_cache.race_lost_then_missing", taxon_id=taxon_id)
            return None
        return CachedSpecies(
            taxon_id=row.taxon_id,
            scientific_name=row.scientific_name,
            common_name=row.common_name,
            iconic_taxon=row.iconic_taxon,
            ancestor_ids=ancestor_ids_from_payload(row.source_payload, taxon_id=row.taxon_id),
        )

    log.info("species_cache.filled", taxon_id=taxon_id)
    return CachedSpecies(
        taxon_id=taxon_id,
        scientific_name=info.scientific_name,
        common_name=info.common_name,
        iconic_taxon=info.iconic_taxon,
        ancestor_ids=ancestor_ids_from_payload(info.raw, taxon_id=taxon_id),
    )


# Used in tests to seed without a real iNat call.
async def upsert_for_tests(
    session: AsyncSession,
    *,
    taxon_id: int,
    scientific_name: str | None = None,
    common_name: str | None = None,
    iconic_taxon: str | None = None,
    ancestor_ids: list[int] | None = None,
) -> None:
    source_payload: dict[str, object] = (
        {"ancestor_ids": list(ancestor_ids)} if ancestor_ids is not None else {}
    )
    session.add(
        models.SpeciesCache(
            taxon_id=taxon_id,
            scientific_name=scientific_name,
            common_name=common_name,
            iconic_taxon=iconic_taxon,
            source_payload=source_payload,
            expires_at=datetime.now().astimezone(),
        )
    )
    await session.flush()
