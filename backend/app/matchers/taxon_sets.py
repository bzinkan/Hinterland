"""Runtime loader for curated Expedition taxon sets."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import structlog

from app.models.expedition_taxon_set import ExpeditionTaxonSetConfig

log = structlog.get_logger()

_TAXON_SET_FILENAME = "expedition_taxon_sets.json"


def _candidate_paths() -> list[Path]:
    override = os.environ.get("HINTERLAND_TAXON_SETS_PATH")
    paths: list[Path] = []
    if override:
        paths.append(Path(override))

    here = Path(__file__).resolve()
    paths.extend(
        [
            Path.cwd() / "content" / _TAXON_SET_FILENAME,
            here.parents[2] / "content" / _TAXON_SET_FILENAME,
            here.parents[3] / "content" / _TAXON_SET_FILENAME,
        ]
    )
    return paths


@lru_cache(maxsize=1)
def load_taxon_set_index() -> dict[str, frozenset[int]]:
    """Load curated taxon sets as `{set_id: taxon_ids}`.

    Missing content degrades to an empty index so old/local installs can
    still boot; content validation and deploy smoke catch packaging drift.
    """
    for path in _candidate_paths():
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = ExpeditionTaxonSetConfig.model_validate(raw)
        index = {
            taxon_set.id: frozenset(taxon.taxon_id for taxon in taxon_set.taxa)
            for taxon_set in config.taxon_sets
        }
        log.info("expedition_taxon_sets.loaded", path=str(path), count=len(index))
        return index

    log.warning(
        "expedition_taxon_sets.missing",
        candidates=[str(path) for path in _candidate_paths()],
    )
    return {}
