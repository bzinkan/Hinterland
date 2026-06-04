"""Process-level Sanctuary content cache.

Loads ``content/sanctuary/*.json`` once and exposes a typed
``SanctuaryContent`` wrapper with pre-built lookup indexes. Mirrors
``scripts/validate_content.py`` for the per-file shape (one key per file
naming the element kind; value is a list of item dicts validated through
the matching ``app.models.sanctuary`` element model; the union is then
assembled into a ``SanctuaryConfig`` for cross-reference validation).

No network calls. No LLM. The cache is read-once / write-once under a
threading lock; ``reset_sanctuary_content_cache()`` is exposed for tests.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.models.sanctuary import (
    CharismaticUnlock,
    CoarseUnlock,
    GuideLine,
    IdentityReflection,
    MysteryCue,
    RelationshipMoment,
    SanctuaryConfig,
    SanctuaryZone,
    SeasonalVariant,
    Soundscape,
    TinySurprise,
)
from app.sanctuary.types import ZoneId

# Resolve the repo root from this file's location:
#   backend/app/sanctuary/content.py
#   -> parents[0] = backend/app/sanctuary
#   -> parents[1] = backend/app
#   -> parents[2] = backend
#   -> parents[3] = REPO_ROOT
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTENT_ROOT = _REPO_ROOT / "content" / "sanctuary"

# Same routing table as scripts/validate_content.py. Each JSON file has one
# top-level key naming the kind; values are validated through the matching
# Pydantic element model.
_ITEM_MODELS: dict[str, type[BaseModel]] = {
    "zones": SanctuaryZone,
    "coarse_unlocks": CoarseUnlock,
    "charismatic_unlocks": CharismaticUnlock,
    "relationship_moments": RelationshipMoment,
    "guide_lines": GuideLine,
    "mystery_cues": MysteryCue,
    "tiny_surprises": TinySurprise,
    "seasonal_variants": SeasonalVariant,
    "identity_reflections": IdentityReflection,
    "soundscapes": Soundscape,
}


@dataclass(frozen=True, slots=True)
class SanctuaryContent:
    """Validated Sanctuary content + pre-built lookup indexes.

    ``coarse_by_iconic_taxon`` resolves a routing decision in one dict
    lookup: the first ``CoarseUnlock`` (in content file order) whose
    ``iconic_taxa`` includes the key. Authors can override routing by
    re-ordering ``content/sanctuary/coarse_unlocks.json`` -- the planner
    never iterates the full list at request time.
    """

    config: SanctuaryConfig
    zone_by_id: dict[ZoneId, SanctuaryZone]
    coarse_by_iconic_taxon: dict[str, CoarseUnlock]
    charismatic_by_taxon_id: dict[int, CharismaticUnlock]
    mystery_cue_by_zone: dict[ZoneId, MysteryCue]
    relationships: tuple[RelationshipMoment, ...]
    tiny_surprises: tuple[TinySurprise, ...]
    guide_lines: tuple[GuideLine, ...]
    seasonal_variants: tuple[SeasonalVariant, ...]
    coarse_by_id: dict[str, CoarseUnlock]
    charismatic_by_id: dict[str, CharismaticUnlock]
    identity_reflections: tuple[IdentityReflection, ...]
    soundscapes: tuple[Soundscape, ...]


_CACHE_LOCK = threading.Lock()
_CACHED_CONTENT: SanctuaryContent | None = None


def get_sanctuary_content() -> SanctuaryContent:
    """Return the process-level Sanctuary content cache.

    First call walks ``content/sanctuary/*.json``, validates each file
    against the matching Pydantic model, assembles a ``SanctuaryConfig``
    for whole-tree cross-reference validation, and builds the lookup
    indexes. Subsequent calls return the cached wrapper.
    """
    global _CACHED_CONTENT
    # Fast path: the cache is set, no lock needed.
    cached = _CACHED_CONTENT
    if cached is not None:
        return cached

    with _CACHE_LOCK:
        # Re-check under the lock; another thread may have loaded already.
        if _CACHED_CONTENT is not None:
            return _CACHED_CONTENT
        _CACHED_CONTENT = _load()
        return _CACHED_CONTENT


def reset_sanctuary_content_cache() -> None:
    """Clear the cache. Tests call this between content edits."""
    global _CACHED_CONTENT
    with _CACHE_LOCK:
        _CACHED_CONTENT = None


def _load() -> SanctuaryContent:
    if not _CONTENT_ROOT.is_dir():
        raise FileNotFoundError(f"sanctuary content directory not found at {_CONTENT_ROOT}")

    collected: dict[str, list[Any]] = {kind: [] for kind in _ITEM_MODELS}

    for path in sorted(_CONTENT_ROOT.rglob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or len(raw) != 1:
            raise ValueError(
                f"sanctuary content file {path} must have exactly one "
                "top-level key naming the element kind"
            )
        kind, items = next(iter(raw.items()))
        if kind not in _ITEM_MODELS:
            raise ValueError(
                f"sanctuary content file {path} has unknown kind {kind!r}; "
                f"expected one of {sorted(_ITEM_MODELS)}"
            )
        if not isinstance(items, list):
            raise ValueError(f"sanctuary content file {path} value for {kind!r} must be a list")
        model = _ITEM_MODELS[kind]
        for item in items:
            collected[kind].append(model.model_validate(item))

    config = SanctuaryConfig.model_validate(
        {kind: [item.model_dump() for item in collected[kind]] for kind in collected}
    )

    zone_by_id: dict[ZoneId, SanctuaryZone] = {z.id: z for z in config.zones}

    # First-match-wins routing in content order. The validator already
    # forbids unknown iconic taxa, so every key here is a real iNat string.
    coarse_by_iconic_taxon: dict[str, CoarseUnlock] = {}
    for coarse in config.coarse_unlocks:
        for taxon in coarse.iconic_taxa:
            coarse_by_iconic_taxon.setdefault(taxon, coarse)

    charismatic_by_taxon_id: dict[int, CharismaticUnlock] = {
        ch.taxon_id: ch for ch in config.charismatic_unlocks
    }

    mystery_cue_by_zone: dict[ZoneId, MysteryCue] = {m.zone: m for m in config.mystery_cues}

    coarse_by_id: dict[str, CoarseUnlock] = {c.id: c for c in config.coarse_unlocks}
    charismatic_by_id: dict[str, CharismaticUnlock] = {c.id: c for c in config.charismatic_unlocks}

    return SanctuaryContent(
        config=config,
        zone_by_id=zone_by_id,
        coarse_by_iconic_taxon=coarse_by_iconic_taxon,
        charismatic_by_taxon_id=charismatic_by_taxon_id,
        mystery_cue_by_zone=mystery_cue_by_zone,
        relationships=tuple(config.relationship_moments),
        tiny_surprises=tuple(config.tiny_surprises),
        guide_lines=tuple(config.guide_lines),
        seasonal_variants=tuple(config.seasonal_variants),
        coarse_by_id=coarse_by_id,
        charismatic_by_id=charismatic_by_id,
        identity_reflections=tuple(config.identity_reflections),
        soundscapes=tuple(config.soundscapes),
    )
