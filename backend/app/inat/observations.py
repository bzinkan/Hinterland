"""iNat /v1/observations/species_counts wrapper.

Used by the rarity-refresh nightly job to ask iNat: "for this geohash
cell, what species are present and at what counts?" Per
`docs/rarity-pipeline.md` we then bucket counts into tiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx
import structlog

from app.inat.client import InatUnavailable

log = structlog.get_logger()


@dataclass(frozen=True)
class SpeciesCount:
    taxon_id: int
    count: int


async def get_species_counts(
    client: httpx.AsyncClient,
    *,
    bbox_swlat: float,
    bbox_swlng: float,
    bbox_nelat: float,
    bbox_nelng: float,
    quality_grade: str = "research,needs_id",
    days: int = 1825,  # 5 years per docs/rarity-pipeline.md
) -> list[SpeciesCount]:
    """Return all species iNat has seen in the bounding box, with counts."""
    params = {
        "swlat": bbox_swlat,
        "swlng": bbox_swlng,
        "nelat": bbox_nelat,
        "nelng": bbox_nelng,
        "quality_grade": quality_grade,
        "verifiable": "true",
        "d1": _days_ago_iso(days),
        "per_page": 500,
    }
    try:
        res = await client.get("/observations/species_counts", params=params)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        log.warning("inat.species_counts.transport_error", error=str(exc))
        raise InatUnavailable("iNat species_counts transport error") from exc

    if res.status_code in (401, 403):
        raise InatUnavailable(f"iNat species_counts unauthorized: {res.status_code}")
    if res.status_code >= 500:
        raise InatUnavailable(f"iNat species_counts server error: {res.status_code}")
    if res.status_code != 200:
        log.warning("inat.species_counts.client_error", status=res.status_code, body=res.text[:200])
        return []

    payload = cast(dict[str, object], res.json())
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []

    out: list[SpeciesCount] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        taxon = item.get("taxon")
        if not isinstance(taxon, dict):
            continue
        taxon_id = taxon.get("id")
        count = item.get("count")
        if not isinstance(taxon_id, int) or not isinstance(count, int):
            continue
        out.append(SpeciesCount(taxon_id=taxon_id, count=count))
    return out


def _days_ago_iso(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
