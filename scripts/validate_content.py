#!/usr/bin/env python
"""Validate JSON content under content/expeditions/ and content/sanctuary/.

Exits 0 iff:
  * all expedition files parse against `app.models.expedition.Expedition`, AND
  * all sanctuary files parse against the matching per-element models in
    `app.models.sanctuary`, AND
  * the assembled `SanctuaryConfig` cross-references resolve.

Exits 1 with a per-file report otherwise.

The CI workflow `.github/workflows/content-validate.yml` runs this on
every PR that touches `content/**` or either model. Authors run
`python scripts/validate_content.py` locally before pushing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

# Path manipulation so the script works whether invoked from repo root
# or from backend/ (CI uses the latter).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

from app.models.expedition import Expedition  # noqa: E402
from app.models.sanctuary import (  # noqa: E402
    CharismaticUnlock,
    CoarseUnlock,
    GuideLine,
    IdentityReflection,
    MysteryCue,
    RelationshipMoment,
    SanctuaryConfig,
    SanctuarySouvenir,
    SanctuaryZone,
    SeasonalVariant,
    Soundscape,
    TinySurprise,
)

# Mapping from the single top-level key in each sanctuary JSON file to the
# Pydantic element model for the items it contains. Each sanctuary JSON file
# is shaped like `{"<kind>": [ {item}, {item}, ... ]}` where `<kind>` is
# exactly one of these keys.
_SANCTUARY_ITEM_MODELS: dict[str, type[BaseModel]] = {
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
    "souvenirs": SanctuarySouvenir,
}


def _validate_expeditions(failures: list[tuple[Path, str]]) -> tuple[int, set[str]]:
    """Validate expedition JSON files.

    Returns the count of files considered and the set of expedition ids
    that parsed cleanly (consumed by the souvenir cross-file check).
    """
    content_root = _REPO_ROOT / "content" / "expeditions"
    expedition_ids: set[str] = set()
    if not content_root.exists():
        print(f"No expedition content found at {content_root}; nothing to validate.")
        return 0, expedition_ids

    files = sorted(content_root.rglob("*.json"))
    if not files:
        print(f"No expedition JSON files in {content_root}; nothing to validate.")
        return 0, expedition_ids

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append((path, f"invalid JSON: {exc}"))
            continue

        try:
            exp = Expedition.model_validate(data)
        except ValidationError as exc:
            failures.append((path, f"schema mismatch:\n{exc}"))
            continue

        # Filename stem must equal the expedition id.
        if path.stem != exp.id:
            failures.append((path, f"filename stem '{path.stem}' must equal id '{exp.id}'"))

        expedition_ids.add(exp.id)

    return len(files), expedition_ids


def _validate_sanctuary(failures: list[tuple[Path, str]]) -> tuple[int, list[SanctuarySouvenir]]:
    """Validate sanctuary JSON files.

    Returns the count of files considered and the parsed souvenirs
    (consumed by the expedition cross-file check in ``main``).

    Sanctuary content lives at content/sanctuary/<kind>.json -- one file
    per content kind in the flat layout documented in docs/sanctuary.md.
    `rglob` is used (not `glob`) so per-element-file sharding remains an
    open future option without a tooling change; subdirs are simply
    walked recursively today.

    Each file is a dict with one key naming the element kind ("zones",
    "coarse_unlocks", ...) whose value is a list of items. Items are
    per-file validated through the matching element model; the union is
    then handed to `SanctuaryConfig` for whole-tree cross-reference
    validation.
    """
    content_root = _REPO_ROOT / "content" / "sanctuary"
    if not content_root.exists():
        print(f"No sanctuary content found at {content_root}; nothing to validate.")
        return 0, []

    files = sorted(content_root.rglob("*.json"))
    if not files:
        print(f"No sanctuary JSON files in {content_root}; nothing to validate.")
        return 0, []

    # Accumulators for the assembled SanctuaryConfig.
    collected: dict[str, list[Any]] = {kind: [] for kind in _SANCTUARY_ITEM_MODELS}
    any_file_failed = False

    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append((path, f"invalid JSON: {exc}"))
            any_file_failed = True
            continue

        if not isinstance(raw, dict):
            failures.append(
                (
                    path,
                    "sanctuary file must be a JSON object with one top-level "
                    "key naming the element kind",
                )
            )
            any_file_failed = True
            continue

        if len(raw) != 1:
            failures.append(
                (
                    path,
                    "sanctuary file must have exactly one top-level key naming "
                    f"the element kind; got keys: {sorted(raw)}",
                )
            )
            any_file_failed = True
            continue

        kind, items = next(iter(raw.items()))

        if kind not in _SANCTUARY_ITEM_MODELS:
            failures.append(
                (
                    path,
                    f"unknown sanctuary element kind {kind!r}; expected one of "
                    f"{sorted(_SANCTUARY_ITEM_MODELS)}",
                )
            )
            any_file_failed = True
            continue

        if not isinstance(items, list):
            failures.append((path, f"value of {kind!r} must be a list of element objects"))
            any_file_failed = True
            continue

        model = _SANCTUARY_ITEM_MODELS[kind]
        parsed: list[Any] = []
        file_had_error = False
        for index, item in enumerate(items):
            try:
                parsed.append(model.model_validate(item))
            except ValidationError as exc:
                failures.append((path, f"schema mismatch at {kind}[{index}]:\n{exc}"))
                file_had_error = True
                continue

        if file_had_error:
            any_file_failed = True
            continue

        # Filename stem must equal the content kind (e.g. zones.json holds
        # the "zones" collection). Per-element-file sharding can later use
        # per-element stems; for now, the flat layout is what we validate.
        if path.parent == content_root and path.stem != kind:
            failures.append(
                (
                    path,
                    f"filename stem '{path.stem}' must equal content kind '{kind}'",
                )
            )
            any_file_failed = True
            continue

        collected[kind].extend(parsed)

    # Only run the whole-tree validation if every per-file parse succeeded.
    # Cross-reference errors are noisy and misleading when some files were
    # rejected upstream.
    if not any_file_failed:
        try:
            SanctuaryConfig.model_validate(
                {kind: [item.model_dump() for item in collected[kind]] for kind in collected}
            )
        except ValidationError as exc:
            failures.append(
                (
                    content_root,
                    f"sanctuary cross-reference validation failed:\n{exc}",
                )
            )

    return len(files), list(collected["souvenirs"])


def _validate_souvenir_expedition_refs(
    failures: list[tuple[Path, str]],
    souvenirs: list[SanctuarySouvenir],
    expedition_ids: set[str],
) -> None:
    """Cross-FILE checks the Pydantic models cannot express alone.

    ``SanctuaryConfig`` deliberately does not resolve souvenir
    ``expedition_id``s (a deployed backend may see content trail), but the
    repo tree itself must be self-consistent: every authored souvenir must
    point at a real ``content/expeditions/**`` id, and its icon must
    follow the ``sanctuary.souvenir.<expedition_id>`` asset-key
    convention. Zone existence is already enforced by
    ``SanctuaryConfig.cross_references_resolve``.
    """
    souvenirs_path = _REPO_ROOT / "content" / "sanctuary" / "souvenirs.json"
    for souvenir in souvenirs:
        if souvenir.expedition_id not in expedition_ids:
            failures.append(
                (
                    souvenirs_path,
                    f"souvenir {souvenir.id!r} references unknown expedition "
                    f"{souvenir.expedition_id!r} (no matching file under "
                    f"content/expeditions/)",
                )
            )
        expected_icon = f"sanctuary.souvenir.{souvenir.expedition_id}"
        if souvenir.icon != expected_icon:
            failures.append(
                (
                    souvenirs_path,
                    f"souvenir {souvenir.id!r} icon {souvenir.icon!r} must be "
                    f"{expected_icon!r} (sanctuary.souvenir.<expedition_id>)",
                )
            )


def main() -> int:
    failures: list[tuple[Path, str]] = []

    expedition_count, expedition_ids = _validate_expeditions(failures)
    sanctuary_count, souvenirs = _validate_sanctuary(failures)
    _validate_souvenir_expedition_refs(failures, souvenirs, expedition_ids)

    if failures:
        print(f"\n{len(failures)} content file(s) failed validation:\n")
        for path, message in failures:
            print(f"  - {path.relative_to(_REPO_ROOT)}")
            for line in str(message).splitlines():
                print(f"      {line}")
        return 1

    print(
        f"OK: {expedition_count} expedition file(s) and "
        f"{sanctuary_count} sanctuary file(s) validated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
