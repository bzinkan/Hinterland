#!/usr/bin/env python
"""Author-time expedition draft generator.

This is deliberately author-time only. It does not run from the backend,
does not import any agent framework, and is never imported by ``backend/app``
(ADR 0002, ADR 0007, internal/ai-agents/README.md). Authors use the generated
JSON as a starting point, edit it, then validate with
``python scripts/validate_content.py``.

Two providers:

* ``static`` (default): the deterministic 3-step template. No network calls,
  byte-identical output run-to-run -- safe for CI.
* ``anthropic``: drafts with a pinned Claude model for human review. Requires
  the ``anthropic`` SDK (an author-time-only install, never a backend
  dependency) and the ``ANTHROPIC_API_KEY`` environment variable. If the model
  output fails validation twice, the tool falls back to the static template.

Examples:
    python scripts/draft_expedition.py "city park insects" --environment park
    python scripts/draft_expedition.py "schoolyard trees" --id schoolyard_trees --out content/expeditions/starters/schoolyard_trees.json
    python scripts/draft_expedition.py "spring ephemerals" --provider anthropic --tier 2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.expedition import Expedition  # noqa: E402

_ENVIRONMENTS = ("yard", "park", "street", "school", "other")
_PROVIDERS = ("static", "anthropic")

_DEFAULT_TIER = 1
_DEFAULT_DURATION_MINUTES = 20
_DEFAULT_ENVIRONMENT = "other"

# Pinned author-time model (ADR 0002: pin model versions so reviewed output
# does not silently drift). Bump deliberately, never float to latest.
ANTHROPIC_MODEL = "claude-opus-4-8"
MAX_TOKENS = 8192

_SYSTEM_PROMPT = """\
You draft expedition content for Dragonfly, a citizen-science app where kids
aged 9-12 log real outdoor observations. A human author reviews and edits
every draft before it ships (ADR 0002: LLMs are author-time only).

Respond with exactly one JSON object and nothing else: no markdown fences,
no commentary, no trailing text.

The object must satisfy this schema (backend/app/models/expedition.py):
- "id": lowercase snake_case string (letters, digits, underscores).
- "title": string, 1-80 characters.
- "subtitle": optional string, at most 160 characters.
- "tier": integer 1-5 (1 = starter, 2 = unlocked later, 3+ = themed).
- "duration_minutes": integer 5-120 (an honest estimate).
- "environments": non-empty list; each entry is one of "yard", "park",
  "street", "school", "other".
- "intro": string, 1-600 characters, shown when the kid opens the expedition.
- "outro": string, 1-300 characters, shown on completion.
- "prerequisites": list, empty for starter expeditions. Each entry is one of:
  - {"kind": "dex_count_at_least", "value": <integer >= 1>}
  - {"kind": "completed_expedition", "value": "<expedition id>"}
- "steps": list of 1-5 steps, each:
  - "id": lowercase snake_case, unique within the expedition.
  - "description": 1-120 characters, imperative voice ("Find...", "Spot...").
  - "match": one match spec (see below).
  - "hint": optional string of concrete examples.

A match spec is a JSON object discriminated by its "kind". The seven kinds:
- {"kind": "iconic_taxon", "value": <taxon>} -- broad category match; the
  most forgiving kind. <taxon> is exactly one of "Plantae", "Insecta",
  "Aves", "Mammalia", "Reptilia", "Amphibia", "Actinopterygii", "Mollusca",
  "Arachnida", "Fungi", "Chromista", "Protozoa", "Animalia".
- {"kind": "taxon_id", "value": <iNaturalist taxon id, integer >= 1>,
  "include_descendants": true} -- a specific taxon or anything beneath it in
  the taxonomic tree. Only use ids you are certain of; prefer iconic_taxon
  when unsure.
- {"kind": "any_organism"} -- wildcard; any living observation counts.
- {"kind": "not_in_dex"} -- a species the kid has not logged before.
- {"kind": "not_within_radius_of_existing", "radius_meters": <1-10000>} --
  an observation away from all the kid's prior observations.
- {"kind": "all_of", "matches": [<match specs>]} -- every nested spec must
  match.
- {"kind": "any_of", "matches": [<match specs>]} -- at least one nested spec
  must match.
Combinators may nest, but keep nesting shallow (two levels at most).

Authoring guidelines (docs/expedition-authoring.md):
- Every step must be completable with a real outdoor observation, in the
  field, in the order given; 2-5 steps is the sweet spot, and an easy win
  first keeps kids going.
- Kid-appropriate copy: respect readers aged 9-12 (never talk down), make
  hints concrete examples rather than abstract advice, name the science in
  the outro, and use at most one exclamation point per expedition.

Example of a valid expedition:
{
  "id": "backyard_starter",
  "title": "Start Where You Are",
  "subtitle": "Your first expedition",
  "tier": 1,
  "duration_minutes": 20,
  "environments": ["yard", "park", "street", "school", "other"],
  "intro": "Look around. Even the most familiar place is full of life you've never noticed. Find three living things and log them.",
  "outro": "You just contributed real data to science. Welcome to Dragonfly.",
  "prerequisites": [],
  "steps": [
    {
      "id": "any_plant",
      "description": "Find a plant -- any plant",
      "match": {"kind": "iconic_taxon", "value": "Plantae"},
      "hint": "Grass, a weed in a sidewalk crack, a tree -- all count."
    },
    {
      "id": "new_insect",
      "description": "Find an insect you have not logged before",
      "match": {"kind": "all_of", "matches": [
        {"kind": "iconic_taxon", "value": "Insecta"},
        {"kind": "not_in_dex"}
      ]},
      "hint": "Check under a leaf, near flowers, or on a wall."
    },
    {
      "id": "wildcard",
      "description": "Find one more living thing -- your choice",
      "match": {"kind": "any_organism"},
      "hint": "Bird, fungus, spider, snail -- surprise us."
    }
  ]
}
"""


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:48].strip("_") or "draft_expedition"


def _title(text: str) -> str:
    words = re.sub(r"[_-]+", " ", text).strip().split()
    if not words:
        return "Draft Expedition"
    titled = " ".join(w.capitalize() for w in words)
    return titled[:80]


def _draft(args: argparse.Namespace) -> dict[str, Any]:
    expedition_id = args.id or _slug(args.prompt)
    title = args.title or _title(args.prompt)
    tier = args.tier if args.tier is not None else _DEFAULT_TIER
    duration_minutes = (
        args.duration_minutes if args.duration_minutes is not None else _DEFAULT_DURATION_MINUTES
    )
    environment = args.environment or _DEFAULT_ENVIRONMENT
    return {
        "id": expedition_id,
        "title": title,
        "subtitle": "A reviewed author-time draft. Edit before publishing.",
        "tier": tier,
        "duration_minutes": duration_minutes,
        "environments": [environment],
        "intro": (
            "Take your time and look closely. Every step should use a real "
            "outdoor observation, not a staged photo."
        ),
        "outro": "Nice field work. Your observations are now part of your Dex.",
        "prerequisites": [],
        "steps": [
            {
                "id": "first_observation",
                "description": "Find one living thing that catches your eye.",
                "match": {"kind": "any_organism"},
                "hint": "Plants, insects, birds, fungi, and tracks all count.",
            },
            {
                "id": "new_to_you",
                "description": "Find something that is not already in your Dex.",
                "match": {"kind": "not_in_dex"},
                "hint": "Look under leaves, along edges, or near a different plant.",
            },
            {
                "id": "different_spot",
                "description": "Make an observation from a different nearby spot.",
                "match": {
                    "kind": "not_within_radius_of_existing",
                    "radius_meters": 25,
                },
                "hint": "Move a short walk away and look again.",
            },
        ],
    }


def build_system_prompt() -> str:
    """System prompt teaching the model the Expedition schema and house style."""
    return _SYSTEM_PROMPT


def build_user_prompt(args: argparse.Namespace) -> str:
    """The theme plus any explicit CLI overrides the author passed."""
    lines = [f"Draft one expedition for this theme: {args.prompt}"]
    if args.id:
        lines.append(f'Use "id": "{args.id}".')
    if args.title:
        lines.append(f'Use "title": "{args.title}".')
    if args.tier is not None:
        lines.append(f'Use "tier": {args.tier}.')
    if args.duration_minutes is not None:
        lines.append(f'Use "duration_minutes": {args.duration_minutes}.')
    if args.environment:
        lines.append(f'Use "environments": ["{args.environment}"].')
    return "\n".join(lines)


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse one JSON object from model output, tolerating markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("model output must be a single JSON object")
    return data


def apply_overrides(data: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Apply explicit CLI flags on top of model output, so flags always win."""
    merged = dict(data)
    if args.id:
        merged["id"] = args.id
    if args.title:
        merged["title"] = args.title
    if args.tier is not None:
        merged["tier"] = args.tier
    if args.duration_minutes is not None:
        merged["duration_minutes"] = args.duration_minutes
    if args.environment:
        merged["environments"] = [args.environment]
    return merged


def generate_anthropic(client: Any, args: argparse.Namespace) -> Expedition | None:
    """Draft one expedition with an injected Anthropic client.

    Model output is parsed defensively, CLI overrides are applied on top
    (flags win), and the result is re-validated through
    ``Expedition.model_validate``. On a validation failure the error text is
    fed back to the model once; after a second failure this returns ``None``
    and the caller falls back to the static template.
    """
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(args)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    last_error = ""
    for _attempt in range(2):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt,
            messages=messages,
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        try:
            data = apply_overrides(parse_model_json(text), args)
            return Expedition.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            messages = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": text or "(no text returned)"},
                {
                    "role": "user",
                    "content": (
                        "That draft failed validation:\n"
                        f"{last_error}\n"
                        "Return one corrected JSON object, no markdown fences."
                    ),
                },
            ]
    print(f"error: anthropic draft failed validation twice:\n{last_error}", file=sys.stderr)
    return None


def _create_client() -> Any:
    """Lazily import the anthropic SDK. Author-time only, never a backend dep."""
    import anthropic

    return anthropic.Anthropic()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Short theme prompt for the expedition.")
    parser.add_argument("--id", help="Snake-case expedition id. Defaults to a slug.")
    parser.add_argument("--title", help="Display title. Defaults to title-cased prompt.")
    parser.add_argument("--tier", type=int, choices=range(1, 6), help="Tier 1-5. Defaults to 1.")
    parser.add_argument("--duration-minutes", type=int, help="Minutes 5-120. Defaults to 20.")
    parser.add_argument(
        "--environment", choices=_ENVIRONMENTS, help="Environment. Defaults to other."
    )
    parser.add_argument(
        "--provider",
        choices=_PROVIDERS,
        default="static",
        help="Draft source: deterministic template (default) or the pinned Anthropic model.",
    )
    parser.add_argument("--out", type=Path, help="Optional output JSON path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    expedition: Expedition | None = None
    provider_used = args.provider
    if args.provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "error: --provider anthropic needs ANTHROPIC_API_KEY set in the "
                "environment (no API call was made).",
                file=sys.stderr,
            )
            return 1
        try:
            client = _create_client()
        except ImportError:
            print(
                "error: --provider anthropic needs the anthropic SDK, an "
                "author-time-only dependency. Install it with: pip install anthropic",
                file=sys.stderr,
            )
            return 1
        expedition = generate_anthropic(client, args)
        if expedition is None:
            print("note: falling back to the static template.", file=sys.stderr)
            provider_used = "static (anthropic fallback)"

    if expedition is None:
        expedition = Expedition.model_validate(_draft(args))

    rendered = json.dumps(
        expedition.model_dump(mode="json", exclude_none=True),
        indent=2,
        sort_keys=False,
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(rendered)
    print(f"note: draft generated by provider: {provider_used}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
