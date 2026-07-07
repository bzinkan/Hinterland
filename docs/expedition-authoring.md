# Expedition Authoring

Expeditions are the content that gives kids a reason to go outside. Each one is a short guided prompt ("find three things that fit these criteria") that the app runs against incoming observations, marking steps complete as matches come in and celebrating when the expedition finishes.

This doc is the reference for writing them: the file format, the match language, validation, and the sync pipeline that gets JSON from the repo to Postgres.

Related reading: `dispatcher.md` (how `ExpeditionHandler` interprets these files at observation time), `data-model.md` (how expedition progress is stored per-user).

## The file format

One JSON file per expedition, living in `content/expeditions/<tier>/<id>.json`. The filename stem must match the `id` field inside the file — the sync script enforces this.

```json
{
  "id": "backyard_starter",
  "title": "Start Where You Are",
  "subtitle": "Your first expedition",
  "tier": 1,
  "duration_minutes": 20,
  "environments": ["yard", "park", "street", "school", "other"],
  "intro": "Look around. Even the most familiar place is full of life you've never noticed. Find three living things and log them.",
  "outro": "You just contributed real data to science. Welcome to Hinterland.",
  "prerequisites": [],
  "steps": [
    {
      "id": "any_plant",
      "description": "Find a plant — any plant",
      "match": { "kind": "iconic_taxon", "value": "Plantae" },
      "hint": "Grass, a weed in a sidewalk crack, a tree, a houseplant by a window — all count."
    },
    {
      "id": "any_insect",
      "description": "Find an insect or bug",
      "match": { "kind": "iconic_taxon", "value": "Insecta" },
      "hint": "Check under a leaf, near flowers, or on a wall."
    },
    {
      "id": "wildcard",
      "description": "Find one more living thing — your choice",
      "match": { "kind": "any_organism" },
      "hint": "Bird, fungus, spider, snail — surprise us."
    }
  ]
}
```

### Top-level fields

| Field              | Type          | Notes                                                           |
|--------------------|---------------|-----------------------------------------------------------------|
| `id`               | string        | Snake_case, unique, stable forever (it keys expedition progress rows) |
| `title`            | string        | Shown large at the top of the expedition card                   |
| `subtitle`         | string        | Optional one-liner under the title                              |
| `tier`             | integer       | 1 = starter, 2 = unlocked after completing any tier 1, 3+ = themed |
| `duration_minutes` | integer       | Honest estimate — kids hate being told "quick" when it isn't    |
| `environments`     | list[string]  | Any of: `yard`, `park`, `street`, `school`, `other`. Filters which expeditions appear in the onboarding picker |
| `intro`            | string        | Shown when the kid opens the expedition                         |
| `outro`            | string        | Shown on completion, after the celebration sequence             |
| `prerequisites`    | list[object]  | See [Prerequisites](#prerequisites); empty for starters         |
| `steps`            | list[object]  | 2–5 steps is the sweet spot. More than 5 and kids drop off      |

### Step fields

| Field         | Type    | Notes                                                                          |
|---------------|---------|--------------------------------------------------------------------------------|
| `id`          | string  | Unique *within the expedition*; used in the progress row's completion map      |
| `description` | string  | One line, imperative voice ("Find…", "Spot…", "Look for…")                     |
| `match`       | object  | The match spec — see below                                                     |
| `hint`        | string  | Optional; shown if the kid taps the step. Concrete examples, not abstract help |

## The match language

Every step has a `match` block with a `kind` and kind-specific fields. When an observation comes in, `ExpeditionHandler` walks each of the user's active expeditions, finds the first incomplete step, and checks whether the observation satisfies that step's match. If it does, the step is marked complete. A single observation can complete at most one step per expedition but can progress multiple expeditions simultaneously.

**Matching is deliberately simple.** The match language is a small declarative vocabulary interpreted by the matcher registry in `app/matchers/`, not a full expression engine. If a match you want to express can't be written with one of the kinds below, the answer is usually "add a new kind" (small, tested, reviewable) not "invent a DSL."

### Phase 1 match kinds

| Kind                          | When to reach for it                                    |
|-------------------------------|---------------------------------------------------------|
| `iconic_taxon`                | Broad category like Plantae, Insecta, Aves, Fungi       |
| `taxon_id`                    | A specific species or genus from iNaturalist            |
| `any_organism`                | Wildcard — any living observation counts                |
| `not_in_dex`                  | Nudge toward finding something new to the user          |
| `not_within_radius_of_existing` | Nudge toward geographic variety                       |

#### `iconic_taxon`

Matches if the observation's taxon belongs to one of iNaturalist's top-level iconic categories. The most useful kind for starter expeditions because it's forgiving.

```json
{ "kind": "iconic_taxon", "value": "Plantae" }
```

Valid values: `Plantae`, `Insecta`, `Aves`, `Mammalia`, `Reptilia`, `Amphibia`, `Actinopterygii` (ray-finned fish), `Mollusca`, `Arachnida`, `Fungi`, `Chromista`, `Protozoa`, `Animalia` (catch-all for animals not in a more specific iconic taxon).

#### `taxon_id`

Matches a specific iNat taxon or anything under it in the taxonomic tree. Use when an expedition is themed around a clade.

```json
{ "kind": "taxon_id", "value": 47157, "include_descendants": true }
```

Looking up the right `value`: search the species on [iNaturalist](https://www.inaturalist.org) and copy the numeric ID from the URL (`/taxa/47157` → `47157`). Put the common and scientific name in a comment at the top of the expedition file for future-you. `include_descendants: true` is the common case ("any butterfly" = taxon ID for order Lepidoptera, descendants included).

#### `any_organism`

Matches anything. Use for wildcard slots in starter expeditions where the goal is momentum, not specificity.

```json
{ "kind": "any_organism" }
```

No other fields. Seemingly trivial, but it's the difference between a kid completing their first expedition and giving up on step 3.

#### `not_in_dex`

Matches any observation of a species not already in this user's Dex. Use to encourage new finds, especially in tier-2+ expeditions where repeat-find grinding would be boring.

```json
{ "kind": "not_in_dex" }
```

#### `not_within_radius_of_existing`

Matches any observation at least `radius_meters` away from any prior observation by this user. Use to push kids to explore rather than photograph the same tree.

```json
{ "kind": "not_within_radius_of_existing", "radius_meters": 50 }
```

### Composing kinds

A match spec can be wrapped in `all_of` or `any_of` to combine:

```json
{
  "kind": "all_of",
  "matches": [
    { "kind": "iconic_taxon", "value": "Plantae" },
    { "kind": "not_in_dex" }
  ]
}
```

This matches "a plant the kid hasn't logged before." Combinators nest; keep nesting shallow (two levels max) or the step becomes hard to reason about.

## Prerequisites

The `prerequisites` field on the top-level expedition controls when it becomes visible. Empty list = always available. The Phase 1 prerequisite kinds:

```json
{ "kind": "dex_count_at_least", "value": 5 }
```

Kid must have at least 5 species in their Dex. Used to gate tier-2 expeditions behind tier-1 completion.

```json
{ "kind": "completed_expedition", "value": "backyard_starter" }
```

Kid must have completed the named expedition. Used for direct-sequel expeditions.

Prerequisites are ANDed together: all must be satisfied for the expedition to appear. Kids don't see locked expeditions in the UI — they just appear when unlocked, so "where did that new one come from?" is part of the experience.

## Validation

Every expedition file is validated against a Pydantic model at three points:

1. **Author-time**: `make validate-content` runs the validator across `content/expeditions/` and reports broken files.
2. **CI**: `.github/workflows/content-validate.yml` runs the same check on every PR. A broken expedition fails the build and never merges.
3. **App boot**: at API startup, the matcher registry rejects any match `kind` not registered in code. Boot fails loudly rather than serving a broken expedition at runtime.

The canonical model (source of truth — the doc follows this, not the other way around):

```python
# backend/app/models/expedition.py
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field, field_validator

IconicTaxon = Literal[
    "Plantae", "Insecta", "Aves", "Mammalia", "Reptilia", "Amphibia",
    "Actinopterygii", "Mollusca", "Arachnida", "Fungi", "Chromista",
    "Protozoa", "Animalia",
]

class MatchIconicTaxon(BaseModel):
    kind: Literal["iconic_taxon"]
    value: IconicTaxon

class MatchTaxonId(BaseModel):
    kind: Literal["taxon_id"]
    value: int
    include_descendants: bool = True

class MatchAnyOrganism(BaseModel):
    kind: Literal["any_organism"]

class MatchNotInDex(BaseModel):
    kind: Literal["not_in_dex"]

class MatchNotWithinRadius(BaseModel):
    kind: Literal["not_within_radius_of_existing"]
    radius_meters: Annotated[int, Field(ge=1, le=10_000)]

class MatchAllOf(BaseModel):
    kind: Literal["all_of"]
    # min_length: an empty all_of would vacuously match ANY photo
    matches: Annotated[list["MatchSpec"], Field(min_length=1)]  # forward ref

class MatchAnyOf(BaseModel):
    kind: Literal["any_of"]
    matches: Annotated[list["MatchSpec"], Field(min_length=1)]

MatchSpec = Annotated[
    Union[
        MatchIconicTaxon, MatchTaxonId, MatchAnyOrganism,
        MatchNotInDex, MatchNotWithinRadius,
        MatchAllOf, MatchAnyOf,
    ],
    Field(discriminator="kind"),
]

class Step(BaseModel):
    id: str
    description: str
    match: MatchSpec
    hint: str | None = None

class PrereqDexCount(BaseModel):
    kind: Literal["dex_count_at_least"]
    value: int

class PrereqCompleted(BaseModel):
    kind: Literal["completed_expedition"]
    value: str

Prerequisite = Annotated[
    Union[PrereqDexCount, PrereqCompleted],
    Field(discriminator="kind"),
]

class Expedition(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    tier: Annotated[int, Field(ge=1, le=5)]
    duration_minutes: Annotated[int, Field(ge=5, le=120)]
    environments: list[Literal["yard", "park", "street", "school", "other"]]
    intro: str
    outro: str
    prerequisites: list[Prerequisite] = []
    steps: Annotated[list[Step], Field(min_length=1, max_length=5)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or v != v.lower():
            raise ValueError("id must be lowercase snake_case")
        return v
```

A matching JSON Schema lives at `content/schema/expedition.schema.json` (generated from the Pydantic model via `scripts/regenerate_schema.py`). Point your editor at it for autocomplete and inline validation while authoring.

## Sync pipeline

The repo is the source of truth. Postgres is a materialized view. The only write path is deploy.

```
content/expeditions/*.json
         │
         ▼  (validated in CI: content-validate.yml)
  hinterland-api image build         — repo-root context bakes content into the image
         │
         ▼  (Container Apps Job `hinterland-sync-expeditions`, started after each deploy when provisioned)
  admin/sync_expeditions.py  ───────▶  Postgres (`expedition_content` table)
         │
         ├─ validates every file with the Pydantic model (any broken file aborts the run)
         ├─ computes content_hash per file
         ├─ skips rows whose hash hasn't changed
         └─ never deletes and never resurrects (tombstoning = the `archived` flag; revival = `--unarchive`)
```

`scripts/sync_expeditions.py` is the local shim around the same module, pointed at the repo checkout instead of the baked-in `/app/content/expeditions`.

**Never edit `expedition_content` rows directly.** The rule is enforced by habit, not by grants (that's an overreaction for a solo build), but the moment two authors exist it becomes a real constraint. Lose the source of truth in the repo and you lose reproducibility, PR review, and the ability to roll back.

**Never edit the `id` of a live expedition.** Expedition progress is keyed on `(user_id, expedition_id)`. Change the id and every kid's progress vanishes from the UI. If an expedition needs retiring, tombstone it (set the row's `archived` flag — see runbook) rather than renaming; the sync job's `--unarchive <id>` flag revives it later.

## Adding a new match kind — the recipe

Follows the same pattern as adding a dispatcher handler: small, local, reviewed.

1. **Define the spec.** Add a new `Match<Name>` Pydantic model in `models/expedition.py`. Add it to the `MatchSpec` union. This is the only place the schema is defined.
2. **Implement the matcher.** Add a file in `app/matchers/kinds/<name>.py` with a function that takes `(spec: Match<Name>, observation: Observation, user_state: UserState) -> bool`. Keep it pure — no DB calls if at all possible.
3. **Register it.** One line in `app/matchers/registry.py`.
4. **Test it.** Unit test the matcher against 5–10 observation fixtures covering positive, negative, and edge cases (e.g. missing fields in the observation). Snapshot-test an expedition that uses it through the dispatcher test harness.
5. **Document it.** Add a row to the Phase 1 match kinds table above, with an example.
6. **Regenerate the JSON Schema.** `python scripts/regenerate_schema.py`. Commit both the Pydantic change and the schema regeneration in the same PR.

No step 7. No touching `ExpeditionHandler` — it dispatches against the registry, never against specific kinds.

## Voice and tone

The copy a kid reads matters as much as the mechanics. A few principles that should survive every author edit:

**Respect the reader.** Kids 9–12 know when they're being talked down to. "Even the most familiar place is full of life you've never noticed" trusts them; "Let's have fun exploring the outdoors!" does not.

**Show, don't abstract.** Hints should be concrete examples, not categories. "Grass, a weed in a sidewalk crack, a tree, a houseplant by a window — all count" beats "Plants are everywhere — keep your eyes open!"

**Name the science.** Every outro should remind the kid that what they just did was real. "You just contributed real data to science" is the load-bearing sentence. Don't hedge it with "kind of" or "in a way."

**No exclamation point inflation.** One per expedition at most, usually in the outro. Excitement on every line numbs to neutrality.

**Avoid gendered or culturally-specific defaults.** "Your yard" excludes apartment kids; "wherever you're standing" doesn't. "Mom and Dad" excludes many families; "your grown-up" covers all of them.

**Length targets.** Intro: 1–2 sentences. Outro: 1 sentence. Step description: under 10 words. Hint: under 20 words. Brevity is a feature for this audience.

## The five starter expeditions (reference)

Each starter is tier 1, no prerequisites, `duration_minutes: 20`. Filenames under `content/expeditions/starters/`.

1. **`backyard_starter.json` — Start Where You Are.** The canonical example above. Plant / insect / surprise. `environments: ["yard", "park", "street", "school", "other"]` — the only starter that works anywhere, surfaced as the default.

2. **`park_starter.json` — Park Patrol.** Tree (Plantae) / something flying (`any_of` Aves, Insecta) / something on the ground the kid hasn't logged before (plain `not_in_dex` — no taxon exclusion). `environments: ["park"]`.

3. **`street_starter.json` — Sidewalk Science.** A plant in a crack (Plantae) / a bug on a wall (Insecta) / a bird overhead (Aves). The copy leans into how cities are habitats too — this is the hardest environment to feel "natural" in, so the voice matters most here. `environments: ["street"]`.

4. **`school_starter.json` — Schoolyard Survey.** Something near the fence / something near a door / something nobody else has noticed (uses `not_within_radius_of_existing` at 50m to push away from the popular tree). `environments: ["school"]`.

5. **`anywhere_starter.json` — Found Anywhere.** Three observations, no taxon constraint, `not_in_dex` on each. The fallback — works in a car, a boat, a grandma's apartment. `environments: ["other"]`.

## The five tier-2 sequels (reference)

Each starter unlocks exactly one tier-2 sequel via a `completed_expedition` prerequisite, giving an unbroken ladder from "first observation" to "themed challenge." Filenames under `content/expeditions/tier2/`.

1. **`backyard_closeup.json` — Look Closer.** Unlocked by `backyard_starter`. Beetle (`taxon_id` 47208, Coleoptera) / spider (`taxon_id` 47118, Araneae) / a plant new to the Dex (`all_of` not_in_dex + Plantae) / smallest-thing wildcard. `environments: ["yard"]`.

2. **`park_pollinators.json` — Pollinator Patrol.** Unlocked by `park_starter`. A bloom (Plantae) / butterfly or moth (`taxon_id` 47157, Lepidoptera) / bee, wasp, or ant (`taxon_id` 47201, Hymenoptera) / a new flower visitor (`not_in_dex`). `environments: ["park"]`.

3. **`street_survivors.json` — Urban Survivors.** Unlocked by `street_starter`. City bird or mammal (`any_of` Aves, Mammalia) / spider (Arachnida) / a new-to-the-Dex pavement plant (`all_of` not_in_dex + Plantae). `environments: ["street"]`.

4. **`school_census.json` — Schoolyard Census.** Unlocked by `school_starter`. Fungus or lichen (Fungi) / a new insect (`all_of` not_in_dex + Insecta) / a bird on the building (Aves) / a 150m radius push (`not_within_radius_of_existing`, escalating the starter's 50m). `environments: ["school"]`.

5. **`anywhere_collector.json` — Nothing But New.** Unlocked by `anywhere_starter` **plus** `dex_count_at_least: 5` — the only tier-2 carrying the Dex gate, because every step demands a species new to the Dex (including a dragonfly/damselfly-or-lepidopteran step, `taxon_id` 47792/47157, and a closing new-find 100m from all prior observations). Environments: all five.
