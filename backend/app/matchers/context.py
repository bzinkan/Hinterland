"""Pure-data inputs to the matcher functions.

Keeping the matcher inputs as a frozen dataclass (not the dispatcher
Context, which has the live DB session) lets matchers stay pure and
trivially unit-testable. The MatcherInputs object is built by the
ExpeditionHandler from data it already has loaded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaxonInfo:
    taxon_id: int
    iconic_taxon: str | None
    ancestor_ids: tuple[int, ...]
    """The full ancestor chain (root -> immediate parent), used by
    `taxon_id` matches with include_descendants=True."""


@dataclass(frozen=True)
class PriorObservation:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class MatcherInputs:
    """Everything a matcher might need to decide. Pure data, no DB."""

    taxon: TaxonInfo | None
    """None when the observation has no taxon -- e.g. manual species_name only."""

    user_dex_taxon_ids: frozenset[int]
    """Taxa the user already has in their Dex. Used by not_in_dex."""

    user_prior_observations: tuple[PriorObservation, ...]
    """All of the user's prior observations (for not_within_radius)."""

    obs_latitude: float | None
    obs_longitude: float | None
    """Legacy precise coordinates, when available.

    New observations retain geohash-4 only, so radius matchers must decline
    when these values are absent rather than inventing a point.
    """

    taxon_sets: Mapping[str, frozenset[int]] = field(default_factory=dict)
    """Curated taxon sets keyed by content id. Used by taxon_set."""

    current_expedition_taxon_ids: frozenset[int] = frozenset()
    """Taxa already credited inside the expedition currently being evaluated."""

    ecology_tags: Mapping[str, str] = field(default_factory=dict)
    """Closed-choice tags saved on the observation. Used by observation_tag."""
