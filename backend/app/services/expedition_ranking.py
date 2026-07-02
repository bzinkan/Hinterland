"""Region-aware relevance ranking for `GET /v1/expeditions/available`.

Consumed by the /available route, and inert without its optional
`geohash4` query param -- old clients (and callers in cold-start
regions with no `rarity_cache` baseline) see exactly the original
(tier, id) order. Product decision: DOWNRANK, NEVER HIDE. Ill-fitting
expeditions sink to the bottom of the list but stay startable.

The regional signal is the set of iconic taxa people actually report
nearby, read from `rarity_cache` (populated nightly by
`app.rarity.refresh`). When the exact geohash-4 cell has no rows we
widen to a geohash-3 LIKE prefix (all sibling geohash-4 cells) --
analogous to, but WIDER than, the rarity dispatcher's exact
parent-region fallback, which only matches materialized geohash-3 rows
that the refresh never writes today. For an iconic-presence signal the
sibling union is the behavior that actually works.
"""

from __future__ import annotations

import re

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.models.expedition import (
    Expedition,
    MatchAllOf,
    MatchAnyOf,
    MatchIconicTaxon,
    MatchSpec,
)

# Geohash base32 alphabet -- no a/i/l/o.
GEOHASH4_RE = re.compile(r"^[0-9bcdefghjkmnpqrstuvwxyz]{4}$")

# Kid-friendly names for iconic taxa, used in relevance reasons.
ICONIC_FRIENDLY: dict[str, str] = {
    "Aves": "birds",
    "Insecta": "insects",
    "Plantae": "plants",
    "Fungi": "mushrooms and fungi",
    "Mammalia": "mammals",
    "Reptilia": "reptiles",
    "Amphibia": "amphibians",
    "Arachnida": "spiders",
    "Mollusca": "snails and shells",
    "Actinopterygii": "fish",
    "Animalia": "animals",
    "Protozoa": "tiny life",
    "Chromista": "algae",
}


async def region_iconic_taxa(session: AsyncSession, geohash4: str) -> frozenset[str] | None:
    """Iconic taxa with any `rarity_cache` baseline near this geohash-4 cell.

    Tries the exact geohash-4 region first; when it has no rows, widens
    to every region sharing the geohash-3 prefix (the union of sibling
    geohash-4 cells -- wider than the dispatcher's exact parent-region
    lookup, deliberately). Returns None when neither has data -- cold
    start, indistinguishable from "no data", which callers treat as
    unranked.
    """
    exact = await _distinct_iconic_taxa(session, models.RarityCache.region_geohash == geohash4)
    if exact:
        return exact
    prefixed = await _distinct_iconic_taxa(
        session, models.RarityCache.region_geohash.like(f"{geohash4[:3]}%")
    )
    if prefixed:
        return prefixed
    return None


async def _distinct_iconic_taxa(
    session: AsyncSession, region_clause: ColumnElement[bool]
) -> frozenset[str]:
    rows = (
        (
            await session.execute(
                select(models.RarityCache.iconic_taxon)
                .where(region_clause, models.RarityCache.iconic_taxon.is_not(None))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return frozenset(value for value in rows if value is not None)


def required_iconic_taxa(exp: Expedition) -> frozenset[str]:
    """Every iconic taxon any step's match spec asks for.

    `all_of` / `any_of` recurse; `iconic_taxon` leaves contribute their
    value; every other kind (taxon_id / any_organism / not_in_dex /
    not_within_radius_of_existing) imposes no regional constraint at
    this layer.
    """
    found: set[str] = set()
    for step in exp.steps:
        _collect_iconic_taxa(step.match, found)
    return frozenset(found)


def _collect_iconic_taxa(spec: MatchSpec, found: set[str]) -> None:
    if isinstance(spec, MatchIconicTaxon):
        found.add(spec.value)
    elif isinstance(spec, MatchAllOf | MatchAnyOf):
        for sub in spec.matches:
            _collect_iconic_taxa(sub, found)


def relevance_for(
    required: frozenset[str], region: frozenset[str] | None
) -> tuple[int, str, str | None]:
    """Sort bucket + relevance level + kid-facing reason for one expedition.

    Bucket 0 keeps the original slot, higher buckets sink. Downrank,
    never hide: the bucket only reorders, nothing is filtered out.
    """
    if region is None or not required:
        # Cold start (no regional baseline) or no regional requirement
        # at all -- keep the original order and say nothing.
        return (0, "unknown", None)
    present = required & region
    if present == required:
        return (0, "great_here", _great_here_reason(required))
    if present:
        return (1, "unknown", None)
    return (2, "tricky_here", _tricky_here_reason(required))


def _friendly_names(taxa: frozenset[str]) -> str:
    names = sorted(ICONIC_FRIENDLY.get(taxon, taxon.lower()) for taxon in taxa)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _great_here_reason(required: frozenset[str]) -> str:
    return f"People spot {_friendly_names(required)} near you a lot"


def _tricky_here_reason(required: frozenset[str]) -> str:
    names = _friendly_names(required)
    return f"{names[0].upper()}{names[1:]} are rarely reported near you -- this one is a challenge"
