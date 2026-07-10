#!/usr/bin/env python
"""Validate reviewed taxonomy packs, manifests, and Expedition references."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field, ValidationError

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

from admin.taxa_catalog_ingest import TaxonPack  # noqa: E402


class PackManifest(BaseModel):
    pack_id: str
    scope: str
    version: str
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    taxon_count: int = Field(gt=0)


class CatalogManifest(BaseModel):
    catalog_version: str
    packs: list[PackManifest]


def _walk(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def main() -> int:
    taxonomy_root = _ROOT / "content" / "taxa"
    failures: list[str] = []
    try:
        manifest = CatalogManifest.model_validate_json(
            (taxonomy_root / "manifest.json").read_bytes()
        )
    except (OSError, ValidationError, ValueError) as exc:
        print(f"taxonomy manifest invalid: {exc}")
        return 1

    pack_ids: set[str] = set()
    catalog_taxon_ids: set[int] = set()
    iconic_taxa: set[str] = set()
    species_count = 0
    for entry in manifest.packs:
        if entry.pack_id in pack_ids:
            failures.append(f"duplicate pack_id {entry.pack_id!r}")
            continue
        pack_ids.add(entry.pack_id)
        path = taxonomy_root / entry.path
        try:
            raw = path.read_bytes()
            pack = TaxonPack.model_validate_json(raw)
        except (OSError, ValidationError, ValueError) as exc:
            failures.append(f"{entry.path}: {exc}")
            continue

        checks = {
            "pack_id": (pack.pack_id, entry.pack_id),
            "scope": (pack.scope, entry.scope),
            "version": (pack.version, entry.version),
            "sha256": (hashlib.sha256(raw).hexdigest(), entry.sha256),
            "size_bytes": (len(raw), entry.size_bytes),
            "taxon_count": (len(pack.taxa), entry.taxon_count),
        }
        for label, (actual, expected) in checks.items():
            if actual != expected:
                failures.append(
                    f"{entry.path}: {label} is {actual!r}; manifest says {expected!r}"
                )

        ids = [taxon.taxon_id for taxon in pack.taxa]
        if len(ids) != len(set(ids)):
            failures.append(f"{entry.path}: duplicate taxon_id")
        catalog_taxon_ids.update(ids)
        iconic_taxa.update(
            taxon.iconic_taxon for taxon in pack.taxa if taxon.iconic_taxon
        )
        species_count += sum(taxon.rank == "species" for taxon in pack.taxa)

    if manifest.catalog_version not in {entry.version for entry in manifest.packs}:
        failures.append("catalog_version does not name any published pack version")
    if species_count < 5:
        failures.append("reviewed packs must include at least five starter species")

    taxon_sets_path = _ROOT / "content" / "expedition_taxon_sets.json"
    try:
        taxon_sets_payload = json.loads(taxon_sets_path.read_text(encoding="utf-8"))
        taxon_sets = taxon_sets_payload["taxon_sets"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        failures.append(f"content/expedition_taxon_sets.json: {exc}")
        taxon_sets = []

    known_set_ids: set[str] = set()
    for taxon_set in taxon_sets:
        if not isinstance(taxon_set, dict) or not isinstance(taxon_set.get("id"), str):
            failures.append("content/expedition_taxon_sets.json: invalid set entry")
            continue
        set_id = taxon_set["id"]
        if set_id in known_set_ids:
            failures.append(f"duplicate expedition taxon set {set_id!r}")
        known_set_ids.add(set_id)
        for taxon in taxon_set.get("taxa", []):
            taxon_id = taxon.get("taxon_id") if isinstance(taxon, dict) else None
            if taxon_id not in catalog_taxon_ids:
                failures.append(
                    "content/expedition_taxon_sets.json: "
                    f"{set_id} taxon_id {taxon_id!r} is not in a pack"
                )

    for path in sorted((_ROOT / "content" / "expeditions").rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # expedition validator reports the primary parse error
        for node in _walk(value):
            kind = node.get("kind")
            referenced = node.get("value")
            if kind == "taxon_id" and referenced not in catalog_taxon_ids:
                failures.append(
                    f"{path.relative_to(_ROOT)}: taxon_id {referenced!r} is not in a pack"
                )
            if kind == "iconic_taxon" and referenced not in iconic_taxa:
                failures.append(
                    f"{path.relative_to(_ROOT)}: iconic taxon {referenced!r} is not in a pack"
                )
            if kind == "taxon_set" and referenced not in known_set_ids:
                failures.append(
                    f"{path.relative_to(_ROOT)}: taxon set {referenced!r} is undefined"
                )

    if failures:
        print("taxonomy validation failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"OK: {len(manifest.packs)} taxonomy pack(s), "
        f"{len(catalog_taxon_ids)} taxa, {species_count} starter species, "
        f"and {len(known_set_ids)} Expedition taxon sets validated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
