# The Sanctuary

The Sanctuary is Dragonfly's stylized, persistent in-app habitat scene.
It grows as a kid logs **real-world** observations, gives kids an
indoor reason to reopen the app, and refuses to let them progress
indoors. The whole feature is built around one rule: **the Sanctuary
reflects what the kid observed outside; it never substitutes for
going outside.**

This doc is the contract. Schema column names, table names, EAS
profiles, and reward-payload fields named below are indicative; the
binding shapes land in the SQLAlchemy + Alembic PR and the
`WorldHandler` PR (see section 12).

Supersedes [`docs/world.md`](world.md), which used legacy
DynamoDB-shaped `WORLD#` single-table key language pre-ADR-0005. The
Postgres-only model in [`docs/data-model.md`](data-model.md) is the
real persistence; the `WORLD#` wording in `AGENTS.md`'s Phase 2 bullet
is conceptual only.

Related reading: [`AGENTS.md`](../AGENTS.md) (Phase 2 candidates,
non-negotiable invariants), [`docs/dispatcher.md`](dispatcher.md)
(where `WorldHandler` plugs in), [`docs/data-model.md`](data-model.md)
(the Postgres tables the schema PR introduces),
[`docs/mobile.md`](mobile.md) (Expo Router screens, the 2-second
celebration budget), [`docs/onboarding.md`](onboarding.md)
(empty-state-fatigue rule),
[`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
(the location-policy decision the Sanctuary is explicitly invariant
under), [`docs/app-store-compliance-checklist.md`](app-store-compliance-checklist.md)
(Designed-for-Families / Apple Kids Category constraints inherited
without exception), [`scripts/validate_content.py`](../scripts/validate_content.py)
(the validator pattern the Sanctuary content schema mirrors).

---

## 1. Product promise

One sentence: **a kid's Sanctuary deepens only when they observe in
the real world.**

The Sanctuary is private to the kid, render-time independent of every
external service, contains no social surface, no purchases, no
precise location, and no kid-facing runtime LLM. It is a personal,
living reflection of a kid's own field log — nothing more.

This is the loop that answers the rainy-Tuesday question: a reason to
reopen the app indoors that does not weaken the outdoor thesis. The
only progression path stays the same — go outside, observe, submit.
The Sanctuary just makes the indoor reopen feel like coming home to
something you built, rather than rereading what you already saw.

If the Sanctuary feature ever conflicts with a Phase 1 invariant in
`AGENTS.md`, the invariant wins. Period.

---

## 2. What The Sanctuary is

### per-kid private diorama

A stylized scene — meadow, woodland, pond, sky, soil, urban,
elsewhere — that the kid sees inside the app. One Sanctuary per kid.
Not shared. Not visible to other kids.

### a reflection of real observations

Every visible element in a kid's Sanctuary is downstream of an
observation that already passed through the existing submission
pipeline (persisted to `observations`, dispatched to handlers,
awaiting async moderation and iNat submission as usual). The
Sanctuary is a *render* of state the kid already earned outdoors.

### a long-lived destination

Unlike the celebration sequence (which is a 2-second beat after
submit, per [`docs/mobile.md`](mobile.md)), the Sanctuary is the
place a kid returns to between trips: to look at what they collected,
tap a creature, read a short blurb, notice a new zone has woken up.

### a content-driven layer

Sanctuary art, copy, zone definitions, and unlock metadata are
authored as JSON under `content/sanctuary/`, validated in CI by a
Pydantic schema, and synced into Postgres by an idempotent ingest
job. Same shape as expedition content. Same rules. See section 12.

### a Postgres-persisted surface

Sanctuary state lives in four new tables — `sanctuary_zone_state`,
`sanctuary_elements`, `sanctuary_observation_contributions`, and
`sanctuary_events` — added via Alembic migrations. The `WORLD#`
phrase in `AGENTS.md`'s Phase 2 candidate list is **conceptual only**
under the post-ADR-0005 Postgres model; there is no `WORLD#`
partition key in this codebase.

---

## 3. What The Sanctuary is not

These boundaries are non-negotiable. They mirror the invariants in
`AGENTS.md` and the Designed-for-Families posture in
[`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).

- **No public social layer.** Sanctuaries are not browsable,
  searchable, or discoverable.
- **No visiting other kids' Sanctuaries.** Not for friends, not for
  classmates, not for family. (A friend-view variant is explicitly
  listed as a Phase 4 candidate in `AGENTS.md`, gated on a privacy
  model being approved. Not Phase 2.)
- **No likes, comments, DMs, public chat, or kid-to-kid free text.**
  There is no text-input surface inside the Sanctuary.
- **No ads, IAP, loot boxes, randomized paid rewards, or manipulative
  streak pressure.** No store, no currency, no spend, no FOMO timers,
  no daily-login penalty.
- **No streak rewards or consecutive-day mechanics.** The Sanctuary
  never tracks or rewards "days in a row." A kid who observes once a
  month and a kid who observes daily have the same per-observation
  Sanctuary response. Positive-reinforcement streaks are also a
  manipulative mechanic, not just penalty streaks.
- **No feeding / pet-care loop.** The Sanctuary does not require the
  kid to tap, feed, water, or tend anything to keep it alive. Indoor
  taps never advance state.
- **No idle or time-based progression.** The Sanctuary never advances
  on a wall-clock timer. Nothing grows, blooms, or evolves because
  time passed; the only way state changes is a real observation
  passing through the dispatcher.
- **No push notifications, badges, or app-icon counters for
  Sanctuary events.** The Sanctuary surface is opt-in: the kid sees
  it only when they open the tab. Push remains a separate Phase 2
  candidate (`docs/roadmap.md`) and explicitly does not ride on
  Sanctuary unlocks.
- **No precise-location display.** The Sanctuary never renders a
  kid's lat/lng, a map pin, an address, a place name finer than the
  coarse `geohash4` region already used elsewhere, or a recognizable
  backyard. Zones are stylized habitats, not maps.
- **No kid-facing runtime LLM.** No chat companion, no "ask the guide
  anything" surface, no on-device or server-side LLM call on a kid's
  request path. Author-time tools may use LLMs to *write* zone copy;
  that copy is reviewed and shipped as static JSON.
- **Not a leaderboard surface.** No ranks, no "your Sanctuary vs
  theirs," no group totals. The Dex / membership counters remain the
  only leaderboard data and live on `memberships` rows as they do
  today.
- **Not dependent on iNat, moderation, maps, or external APIs at
  render time.** Opening the Sanctuary tab works fully offline
  against state already in Postgres / on device. Render must not
  block on any third-party call.

If a future change to the Sanctuary would weaken any of the above,
it requires an ADR per `AGENTS.md` working rules and explicit
operator sign-off.

---

## 4. MVP scope

The Phase 2 MVP is intentionally narrow.

### in scope

- **Seven zones**, defined in content JSON, mapped from iNaturalist
  `iconic_taxon` values (section 5).
- **Six deepening thresholds per zone, per kid** (section 7): 1 / 3 /
  5 / 10 / 20 / 50 moderation-passed observations matched to that
  zone.
- **Coarse iconic_taxon unlocks** — every *identified* observation
  lights up something in the matching zone, even an obscure beetle.
  Sanctuary participation begins at identification (decision
  2026-07-03): an observation with no `taxon_id` contributes nothing
  until the kid picks a species, at which point the taxon-time
  re-dispatch makes the full contribution (zone, unlocks, cameos).
- **Two new dispatcher reward types**: `world_unlock` and
  `world_evolution` (section 8). No other Sanctuary-specific reward
  types.
- **One new dispatcher handler**: `WorldHandler`, running after
  `DexHandler`, `ExpeditionHandler`, and `RarityHandler`. No new
  endpoints on the submission spine.
- **One new mobile surface**: a Sanctuary screen in the Expo Router
  file-based navigation under `mobile/app/`. Tap-to-inspect; no chat;
  no map. (Whether the screen is a tab or stacked is a `mobile/app/`
  decision; this doc does not bind it.)
- **Tap-to-inspect** on each unlocked element with author-time blurb
  copy.
- **A simple journal/timeline** showing the order in which elements
  appeared, private to the kid. (Kid-facing label: **"Story"** -- the
  Field Journal tab owns the "Journal" name; the API field stays
  `journal`.)

### explicitly deferred

- Charismatic per-taxon-id unlocks beyond a small starter set (50–100
  hand-illustrated species). Content drop after MVP; data model and
  handler unchanged.
- Relationship moments (multi-species interactions). Author-time
  content; can ship piecemeal.
- Seasonal variants. Lands with the Phase 3 `SeasonHandler` work.
- Weather / time-of-day ambient layers beyond the threshold-driven
  coarse pass.
- Friend-view, shared zones, notifications, purchases, customization,
  naming, screenshots-to-camera-roll.

### net-new surfaces

One new mobile screen, one new content directory
(`content/sanctuary/`), one or more new Postgres tables, one new
dispatcher handler (`WorldHandler`), two new `RewardType` values, one
new validator entry point.

### not touched

`POST /v1/observations` shape, the dispatcher core, the existing
handlers, the `observations` / `dex_entries` / `memberships`
schemas, the moderation pipeline, the iNat submission pipeline, the
rarity refresh job. This list enforces the Phase 2 rule from
`AGENTS.md`: *"Phase 2 features must plug into existing handlers,
data patterns, or new ADR-approved prefixes. Do not rewrite the
submission spine."*

---

## 5. Seven zones

Each zone is a stylized habitat that wakes up the first time a kid
logs a moderation-passed observation whose iNaturalist
`iconic_taxon` (and, for *urban* and *elsewhere*, optional secondary
signals from content metadata) maps to it.

`iconic_taxon` values used below are the iNat-standard strings. The
authoritative routing table lives in
`content/sanctuary/routing/iconic_taxon_map.json` and is validated at
build time; the WorldHandler reads it at cold start. Edge subsets
(e.g. raptor-vs-perching, urban-vs-woodland mammal) are encoded as
`taxon_id` overrides in the same map.

### meadow

Flavor: a sunlit field of grasses, wildflowers, and the small flying
things that live among them.

- `Insecta`
- `Arachnida`
- `Plantae` (when content metadata flags open / herbaceous)

### woodland

Flavor: a quiet stand of trees with bark, leaf litter, and creatures
that prefer cover.

- `Plantae` (when content metadata flags woody / forest-associated
  taxa)
- `Aves` (perching / woodland subsets)
- `Mammalia` (terrestrial)
- `Reptilia` (terrestrial)

### pond

Flavor: still water, edges, and everything that breathes through
skin or gills.

- `Amphibia`
- `Actinopterygii`
- `Mollusca` (freshwater)
- `Insecta` (aquatic / odonate variants surface a pond cue *in
  addition to* meadow)

### sky

Flavor: the air above the kid's other zones.

- `Aves` (raptor / large-flyer subsets)
- `Insecta` (flying variants surface a sky-side cue *in addition to*
  meadow)

### soil

Flavor: the layer underfoot — fungi, decomposers, and what lives in
leaf litter.

- `Fungi`
- `Annelida`
- `Mollusca` (terrestrial gastropods)
- decomposer subsets of `Insecta` per content metadata

### urban

Flavor: built spaces — sidewalks, parks, walls, eaves.

- Any iconic taxon **plus** a content-side
  `urban_adapted: true` flag on the matching entry in
  `content/sanctuary/routing/iconic_taxon_map.json` (or a `taxon_id`
  override list at
  `content/sanctuary/routing/urban_taxa.json`).
- Urban is a *context overlay*, not an iconic_taxon-exclusive zone.
  No schema change to `species_cache` or `expedition_content` is
  required; the flag lives in Sanctuary content JSON only.

### elsewhere

Flavor: the catch-all for *identified* taxa that don't fit the six
above.

- Anything not mapped above (e.g. `Protozoa`, `Chromista`).
- Also receives identified observations whose iconic_taxon could not
  be resolved (species-cache gap): these still light *something* up
  so the kid is never punished for the taxonomy backend's gaps.
- Observations with **no taxon at all** ("mystery finds") do NOT
  contribute here or anywhere -- Sanctuary participation begins at
  identification (decision 2026-07-03). `WorldHandler` skips them
  entirely; the contribution happens when the species pick triggers
  the taxon-time re-dispatch. A mystery find that is never identified
  never contributes (the Field Journal still shows it, and
  identifying it later from the observation detail screen recovers
  the full contribution). Note: a manually *named* find (free-text
  species, no taxon) is also a non-contributor until a taxon is
  resolved for it.

A single observation may light up **one primary zone** (highest-
priority match) and at most one **overlay** (urban). The mapping
table is the source of truth; the database is a materialized view.

### when contributions happen

Sanctuary participation begins at identification (decision
2026-07-03). `WorldHandler` skips observations whose `taxon_id` is
NULL — no contribution row, no zone counts. The live mobile flow
creates observations taxonless and assigns the species via PATCH;
that taxon-time re-dispatch makes the full contribution in one shot
(zone routing, coarse/charismatic unlocks with the same dispatch's
first-find state, threshold evolutions). An observation created WITH
a taxon contributes at create time, exactly as before. A mystery
find that is never identified never contributes; identifying it later
from the observation detail screen recovers the full contribution.

---

## 6. Unlock types

Five flavors of unlock. All are **deterministic** functions of
state the dispatcher already produces. None are randomized.

### coarse unlocks by iconic_taxon

Lighting up a zone for the first time (e.g. the kid's first `Aves`
observation wakes the sky zone). The base case. Emits one
`world_unlock` reward.

### charismatic unlocks by taxon_id

Specific species — author-curated in content JSON — that get their
own art when first observed (e.g. first monarch gets a monarch on
the meadow's milkweed, not a generic butterfly). Emits one
`world_unlock` reward with a `taxon_id` payload field.

Authoring rule: copy strings for charismatic unlocks must not invoke
scarcity, rarity-as-status, or comparative framing ("only N kids
have this"). They celebrate the species, not the kid's exclusivity.
The validator enforces this at content-validation time.

### relationship moments

Authored multi-condition cues that fire when two prior unlocks
coexist (e.g. once both `bee` and `milkweed` are unlocked in meadow,
a pollination detail appears). Defined entirely in content JSON; the
handler checks the kid's unlock set. Emits a `world_evolution`
reward.

### tiny surprises

Low-weight ambient detail that appears at certain deepening
thresholds without being its own celebration — a butterfly path, a
drifting seed, a frog plop. The reward (if any) is `world_evolution`
at low weight; many tiny surprises emit no reward and are simply
rendered on next Sanctuary open. **Deterministic**, threshold-keyed.
Not randomized.

### seasonal variants

Author-time alternate art for a zone or element keyed off the
observation's submission month for already-unlocked elements.
Render-time selector reads the latest cached season tag derived from
the kid's coarse `geohash4` region's hemisphere/season window only —
never from precise location and never from an external API call on
render. Wires in when `SeasonHandler` lands in Phase 3.

---

## 7. Deepening thresholds

Per zone, per user. Thresholds count moderation-passed observations
whose zone-assignment resolves to that zone. They do **not** count
submissions that failed moderation or were quarantined — that
closes the "quarantine grinding" edge case.

| Observations in zone | Effect                                                                                  |
|----------------------|-----------------------------------------------------------------------------------------|
| 1                    | Zone wakes up: from greyscale silhouette to color. Emits `world_unlock`.                |
| 3                    | First visible detail layer (e.g. flowers fill in on the meadow). Emits `world_evolution`. |
| 5                    | Density and motion (grass sways, more individuals visible). Emits `world_evolution`.    |
| 10                   | Relationship / ambient layer (pollinators move between flowers; soundscape variant becomes available). Emits `world_evolution`. |
| 20                   | Weather / time-of-day variant becomes available for the zone. Emits `world_evolution`.  |
| 50                   | Signature detail — an author-curated, zone-defining art beat the kid earned through sustained outdoor effort. Last reward this zone emits in MVP. |

Thresholds are **per-zone, per-user, monotonic** and are not a
leaderboard. Two kids in the same group reach them independently. 50
is a cap on signature detail, not a status; a kid who never crosses
50 has a complete-feeling Sanctuary.

Charismatic unlocks layer on top of these thresholds regardless of
count. A kid who logs 200 plants but no birds has a lush, deepening
meadow and an empty woodland — the Sanctuary tells them what kind of
naturalist they're becoming.

Thresholds and the assets they unlock are author-time data in
`content/sanctuary/zones/<zone>.json`. They are not hard-coded in the
handler.

---

## 8. Reward types

The Sanctuary introduces exactly two new `RewardType` Literal values,
slotted into the existing list in `app/dispatcher/core.py` (existing
values per `docs/dispatcher.md`: `first_find`, `repeat_find`,
`expedition_step`, `expedition_complete`, `rarity_tier`, `unrecorded`,
plus Phase 2+ placeholders `territory_claimed`, `season_hit`,
`mission_progress`, `mission_complete`).

### `world_unlock`

A zone or charismatic element became visible in the kid's Sanctuary
for the first time.

- `title` — short, calm. e.g. "A new corner of your Sanctuary."
- `detail` — names the unlocked element. e.g. "Your meadow has a
  monarch now."
- `icon` — asset key resolved client-side from the Sanctuary asset
  map.
- `weight` — **60**. Same band as `rarity_tier` legendary/epic:
  meaningful and rare, but still ranked below `first_find` (80) and
  `unrecorded` (100). Raised from the earlier draft of 40 when the
  `WorldHandler` landed in the dispatcher (PR adding `WorldHandler`
  rewards) so a zone wake-up celebrates above an expedition step.
- `payload` — `{"zone": str, "element_id": str, "unlock_kind":
  "coarse"|"charismatic", "taxon_id": int | null}`. `WorldHandler`
  also stamps `tier_hint` when `RarityHandler` published a tier for
  the same observation; the field is decorative only.

### `world_evolution`

An existing unlock deepened — a threshold crossed, a relationship
moment lit up, a seasonal variant appeared.

- `title` — short. e.g. "Your meadow is fuller."
- `detail` — names what changed.
- `icon` — asset key.
- `weight` — **30**. Same band as `territory_claimed`. Ambient, not
  headline.
- `payload` — `{"zone": str, "evolution_id": str, "threshold": int | null}`.

### ordering and tie resolution

The client already sorts by `weight` desc; Sanctuary rewards
therefore appear *after* `first_find` / `unrecorded` / high
`rarity_tier` rewards in the celebration sequence. This is
intentional: the headline is what the kid did in the real world; the
Sanctuary update is the echo.

Explicit weight comparison: a same-observation `first_find`
(weight 80) outranks a same-observation `world_unlock` (60); both
fire and animate in sequence per the existing celebration model.

On ties, the dispatcher resolves by **handler registration order**
(stable). Because `WorldHandler` is registered after `DexHandler` and
`RarityHandler` (and before `ExpeditionHandler`), a same-weight
`rarity_tier` legendary (60) from `RarityHandler` appears *before*
a same-weight `world_unlock` (60) in the celebration sequence —
preserving the principle that real-world progression headlines over
Sanctuary echoes.

### sketch

```json
{
  "type": "world_unlock",
  "title": "A new corner of your Sanctuary.",
  "detail": "Your meadow has a monarch now.",
  "icon": "sanctuary.meadow.monarch.unlock",
  "weight": 60,
  "payload": {"zone": "meadow", "element_id": "meadow_charismatic_monarch", "unlock_kind": "charismatic", "taxon_id": 48662}
}
```

```json
{
  "type": "world_evolution",
  "title": "Your meadow is fuller.",
  "detail": "",
  "icon": "sanctuary.meadow.level3",
  "weight": 30,
  "payload": {"zone": "meadow", "evolution_id": "meadow.threshold.3", "threshold": 3}
}
```

Both reward `title` and `detail` strings are read verbatim from
`content/sanctuary/` JSON. Neither is generated at runtime.

---

## 9. Dispatcher integration

### handler position

`WorldHandler` is added to `HANDLERS` in `app/dispatcher/registry.py`
**after** `DexHandler` and `RarityHandler`, and **before**
`ExpeditionHandler`. The Phase 2 order becomes:

```python
HANDLERS: list[Handler] = [
    DexHandler(),          # Phase 1: owns is_first_find
    RarityHandler(),       # Phase 1: owns rarity tier
    WorldHandler(),        # Phase 2: Sanctuary writes; never blocks
    ExpeditionHandler(),   # Phase 1: may read dex state
    # TerritoryHandler(),  # Phase 2
    # SeasonHandler(),     # Phase 3
    # MissionHandler(),    # Phase 4
]
```

Order matters per `docs/dispatcher.md`: *"Ordering is a real
contract. If `ExpeditionHandler` needs to know whether a find was a
first-find to award a bonus, the Dex handler must run first."*
`WorldHandler` follows the same rule: it reads
`ctx.results["dex"].state["is_first_find"]` to gate one-shot zone
wake-ups, so it must run after `DexHandler`. It reads
`ctx.results["rarity"]` to optionally tag a charismatic unlock with
rarity context, so it must run after `RarityHandler`. Its docstring
declares `dex` and `rarity` as predecessors and a snapshot test
asserts the order.

### contract

`WorldHandler` conforms to the existing `Handler` Protocol:

```python
class WorldHandler:
    name = "world"

    async def handle(self, ctx: Context) -> HandlerResult:
        ...
```

Returning a `HandlerResult` with `rewards: list[Reward]` (zero or
more `world_unlock` / `world_evolution`) and `state: dict[str, Any]`
exposing at minimum:

```python
{
    "zones_touched": list[str],
    "unlocks_emitted": list[str],
    "evolutions_emitted": list[str],
}
```

### invariants the WorldHandler must honor

These are the same rules every Phase 1 handler obeys; they are
restated here because the Sanctuary is the first kid-visible Phase 2
surface.

- **Submission does not block on the WorldHandler.** The observation
  row is already persisted before the dispatcher runs. Per
  `docs/dispatcher.md`: *"A failed handler never fails the
  submission… the worst-case outcome is a missing celebration,
  which is recoverable (a job can replay it)."*
- **No external HTTP calls.** No iNat, no Maps, no moderation
  provider, no LLM. The handler reads Postgres and writes Postgres.
  That's it. Matches the Phase 1 handler invariant
  ("self-contained").
- **Idempotent under replay.** First-element is enforced by an
  atomic conditional write — Postgres `INSERT … ON CONFLICT DO
  NOTHING` against `sanctuary_elements` on the unique key
  `(user_id, zone_id, element_id)` — exactly mirroring the Dex
  first-find pattern (`docs/data-model.md`: *"The first-find check
  must not become read-then-write. The database unique constraint
  is the source of truth under concurrency."*). A second structural
  gate sits at `sanctuary_observation_contributions`, whose primary
  key **is** the `observation_id` itself: the dispatcher's
  per-observation contribution row can be inserted at most once, so
  replaying the same `observation_id` is a structural no-op (the
  WorldHandler catches the PK collision and skips every counter
  bump and element fire from that observation). Threshold crossings
  are computed from authoritative counts in `sanctuary_zone_state`,
  not from "was this the Nth submission this session?" Running the
  dispatcher twice for the same `observation_id` produces the same
  rewards and the same row count.
- **Exception-safe.** Any failure inside `WorldHandler` is caught by
  the dispatcher loop, logged as `dispatcher.handler_failed`, and
  the empty `HandlerResult` is recorded under `ctx.results["world"]`
  so later handlers (if any) can probe presence. The submission
  still succeeds. A replay job can recover the missed Sanctuary
  update later (replay semantics per ADR 0004).
- **Fast.** <100 ms p95, within the 300 ms full-dispatcher budget.
  Reads must be indexed lookups against the per-user Sanctuary
  state, not scans over `observations`.
- **Sequential, not parallel.** Per `docs/dispatcher.md`: *"Do not
  parallelize."*
- **Does not own `memberships` columns.** The Sanctuary is per-user;
  leaderboard rows are untouched.

### service layer

The `WorldHandler` is intentionally thin. It calls a pure-function
planner — `app.sanctuary.service.compute_sanctuary_plan(inputs,
content)` — and passes the returned `SanctuaryPlan` to a writer that
issues the actual `INSERT ... ON CONFLICT DO NOTHING` on
`sanctuary_elements`, the `UPDATE` on `sanctuary_zone_state`, the
`INSERT` rows into `sanctuary_events`, and appends the rewards to
the dispatcher's reward list. Keeping the planner pure means: no DB
session, no HTTP, no LLM, no `datetime.now()` / `random` /
`uuid.uuid4()` reads — the same `(inputs, content)` produces a
byte-identical plan, which is the property dispatcher replay relies
on.

Content is loaded once per process via
`app.sanctuary.content.get_sanctuary_content()` — a threading-locked
process-level cache that walks `content/sanctuary/*.json`, validates
each file through the Pydantic schema from PR #96, assembles a
`SanctuaryConfig` for whole-tree cross-reference validation, and
builds the lookup indexes (`coarse_by_iconic_taxon`,
`charismatic_by_taxon_id`, `zone_by_id`, etc.) the planner reads in
constant time at request time.

The planner is wired into the dispatcher in a follow-up PR; this
service layer ships first so it can be tested in isolation against
the real content files (`backend/tests/test_sanctuary_service.py`).

Sketch of the planner contract:

```python
from app.sanctuary.service import compute_sanctuary_plan
from app.sanctuary.content import get_sanctuary_content
from app.sanctuary.types import ServiceInputs, ObservationInput

plan = compute_sanctuary_plan(
    inputs=ServiceInputs(
        observation=ObservationInput(
            user_id=...,
            observation_id=...,
            taxon_id=...,
            species_name=...,
            iconic_taxon=...,       # from species_cache, may be None
            is_first_find=...,      # from DexHandler result
            current_date=...,       # reserved for future SeasonHandler
        ),
        zone_states=[...],          # read from sanctuary_zone_state
        elements=[...],             # read from sanctuary_elements
    ),
    content=get_sanctuary_content(),
)
# plan.elements_to_unlock, plan.zone_transitions, plan.events,
# plan.rewards -- all plain data, ready for the writer.
```

### persistence

Per `docs/data-model.md`, the data layer is **Postgres only** (Cloud
SQL for PostgreSQL, ADR 0005). There is no DynamoDB single-table
design anymore. The legacy phrasing in `AGENTS.md` of *"`WORLD#`
rows and `WorldHandler`"* is **conceptual only** — `WORLD#` is not a
partition key in this codebase.

The Sanctuary lands as four SQLAlchemy models + an Alembic migration
alongside the existing tables (final shape is
[`docs/data-model.md`](data-model.md) "Sanctuary State"):

- `sanctuary_zone_state` — per-user, per-zone observation counts and
  current depth tier. Unique constraint on `(user_id, zone_id)`.
- `sanctuary_elements` — per-user record of which kid-visible
  elements (zone wake-ups, charismatic species, relationship
  moments, tiny surprises, signature unlocks) have fired. Unique
  constraint on `(user_id, zone_id, element_id)` so first-fire is
  atomic via `INSERT … ON CONFLICT DO NOTHING`.
- `sanctuary_observation_contributions` — per-observation row whose
  primary key **is** the `observation_id` (FK to `observations.id`,
  ondelete=CASCADE). Records which `element_ids` an observation
  contributed to. Acts as the dispatcher's structural replay gate:
  the same `observation_id` cannot insert twice.
- `sanctuary_events` — append-only audit log of celebration-worthy
  rows (world unlocks, world evolutions, relationship moments, tiny
  surprises). Backs the per-zone journal/timeline (kid-facing label: "Story") in §10.

Content (`content/sanctuary/` JSON, validated by the Pydantic
schema in PR #96) is **not** mirrored into a Postgres table in this
PR; the API reads zone/element metadata from the in-process content
map. A `sanctuary_content` cache table can be added later if a
product need emerges.

### API: `GET /v1/sanctuary/me`

Read-only, current-user-scoped. Merges authored content from
`content/sanctuary/` (PR #96) with durable per-user state from PR
#97's four `sanctuary_*` tables. The dispatcher's `WorldHandler` (PR
adding `WorldHandler` rewards) is the only writer; the API never
mutates state and never accepts a `user_id` query parameter.

#### Response shape

```json
{
  "zones": [
    {
      "zone_id": "meadow",
      "title": "Meadow",
      "mood": "Open grass, wildflowers...",
      "description": "Open grass, wildflowers...",
      "observation_count": 5,
      "depth_tier": 5,
      "unlocked": true,
      "next_threshold": 10,
      "accent": null
    }
  ],
  "elements": [
    {
      "element_id": "meadow_coarse_plantae",
      "zone_id": "meadow",
      "element_type": "coarse",
      "title": "Plants in the meadow",
      "detail": "Grasses, wildflowers...",
      "icon": "sanctuary.meadow.plantae",
      "taxon_id": null,
      "source_observation_id": "01J0...",
      "unlocked_at": "2026-06-01T12:00:00Z",
      "payload": {}
    }
  ],
  "recent_events": [ ... ],
  "guide_message": {"speaker": "dragonfly", "text": "..."},
  "mystery_cues": [ ... ],
  "journal": [ ... ]
}
```

`zones` always returns all seven authored zones in authored order
(`meadow, woodland, pond, sky, soil, urban, elsewhere`) — even when
locked.

#### Empty state (new kid)

- `zones` returns all seven, each `unlocked=false`,
  `observation_count=0`, `depth_tier=0`, `next_threshold=1`.
- `elements`, `recent_events`, and `journal` are empty lists.
- `guide_message` is the first authored general guide line (or the
  hard-coded fallback `"Your Sanctuary is quiet. One real observation
  can wake it up."` if no general lines are authored).
- `mystery_cues` returns up to three cues from authored content, in
  authored zone order — for a fresh user this is `meadow`,
  `woodland`, `pond`.

#### `next_threshold` rules

Computed from `THRESHOLDS = (1, 3, 5, 10, 20, 50)` as the smallest
threshold strictly greater than `observation_count`. Returns `null`
when `observation_count >= 50`.

| count | next_threshold |
|------:|---------------:|
| 0     | 1              |
| 1     | 3              |
| 2     | 3              |
| 3     | 5              |
| 9     | 10             |
| 49    | 50             |
| 50    | null           |

#### Guide selection (deterministic 4-step ladder)

1. **Zero unlocked zones.** Return the first authored general guide
   line (`zone == null`), or the hard-coded starter line if none
   authored.
2. **Most-recent event has a zone.** Return the first authored guide
   line whose `zone` matches that recent zone, if any.
3. **Otherwise.** Return the first authored guide line for the kid's
   deepest unlocked zone (ties broken by authored zone order).
4. **Fallback.** Any general guide line, else the hard-coded starter.

"First matching" iterates `content.guide_lines` in content order. The
same `(state, content)` always produces the same line on every
request.

#### Mystery cue selection

Surfaces 1–3 cues from `content/sanctuary/mystery_cues.json`. Priority
within authored zone order:

1. **Locked zones** (`observation_count == 0`).
2. **Quiet unlocked zones** (`observation_count < 3`).
3. **Already-deep zones** — only included if fewer than 3 cues filled
   by the first two passes.

Cues are emitted in authored zone order (`meadow, woodland, pond,
sky, soil, urban, elsewhere`), so the surface stays stable across
requests. The mystery cue copy itself is authored under the
no-precise-location / no-streak-pressure rules from PR #96's validator.

#### Privacy guarantees

The endpoint returns NO precise-location fields. `latitude`,
`longitude`, `geohash`, `geohash4`, and `place_name` do not appear in
any DTO, payload, or nested dict. `tests/test_sanctuary_route.py::
test_response_contains_no_location_fields` walks the entire response
JSON recursively and fails CI if any forbidden key is found.

The endpoint accepts no `user_id` query parameter. Every SQL query
filters on the verified `current_user.uid` resolved by
`CurrentUserDep`; a hostile caller passing `?user_id=...` gets the
same response as if the param were never set.

#### Read-only design

This endpoint never writes. The dispatcher's `WorldHandler` is the
only writer to the four `sanctuary_*` tables; the read API is a
materialized view over what the handler already committed.

#### Delight layer (PR adding identity reflection + projections)

The response also includes three derived/projected fields built from
authored content (`content/sanctuary/`) + the per-user state already
loaded:

- `identity_reflection: IdentityReflectionDTO | null` — a single
  descriptive line about the kid's developing Sanctuary identity,
  selected via a deterministic rule ladder authored in
  `content/sanctuary/identity_reflections.json`. The route walks
  `content.identity_reflections` in content order and returns the
  FIRST entry whose rules ALL match the kid's snapshot. Returns
  `null` only when no entries are authored. Rule fields per entry:
  `dominant_zone` (zone with strict-max `observation_count`),
  `min_total_observations`, `min_element_count`, `max_zones_unlocked`
  (strict `<` test). Copy must be DESCRIPTIVE — the Pydantic
  copy-policy validator from PR #96 was extended to reject
  leaderboard / streak / "better than" / "more than other" /
  "in a row" / "do not miss" / `streak` / `rank` / `score` /
  `compete` / `winner` tokens. Identity reflection text is rendered
  verbatim on the client.
- `relationship_moments: list[RelationshipMomentDTO]` — a slim
  projection of `sanctuary_elements` rows where
  `element_type == "relationship"`. Each entry carries
  `element_id`, `zone_id`, `title`, `detail`, `icon`, `unlocked_at`.
  Authored title / detail / icon from
  `content/sanctuary/relationship_moments.json` win; snapshot
  payload + safe fallback are the same chain as the main `elements`
  list.
- `tiny_surprises: list[TinySurpriseDTO]` — same projection for
  `element_type == "surprise"`. `title` is the only client-visible
  string composed at the route layer ("A small detail in the
  {zone_title}"); `detail` is the authored
  `TinySurprise.description` verbatim; `threshold` is the authored
  per-zone threshold (3 / 5 / 10) or `null` when missing.

These three fields exist on top of the existing `elements` list —
the full `sanctuary_elements` rows are still returned via
`elements` for callers that want them; the projections are mobile
ergonomics, not a new data source.

#### Seasonal variants & sound placeholders (PR adding seasonal variants and sound placeholders)

The response also includes a date-driven seasonal info block and a list
of authored sound-placeholder entries. Neither field depends on precise
location, an external API, or any new permission.

- `season: SanctuarySeasonDTO` — the visual seasonal tint for this
  response. Selected on the server from the current UTC date via the
  helper `app.sanctuary.season.current_season(today)` using a
  Northern-Hemisphere meteorological calendar (March-May = spring,
  June-August = summer, September-November = autumn, December-February
  = winter). The block carries:
  - `season`: one of `spring` / `summer` / `autumn` / `winter`. The
    existing `Season` literal in `app/models/sanctuary.py` is the
    canonical vocabulary; the brief's "fall" is the same season as
    `autumn` (the meteorological convention this codebase uses).
  - `background_tone`: a single calm word (`fresh` / `warm` / `fading`
    / `still`) the client maps to a tint behind the season banner.
  - `zone_accents`: a `dict[ZoneId, str]` of short per-zone seasonal
    labels (e.g. `meadow: "wildflowers waking"` in spring). Structural
    rendering data, the seasonal analogue of the existing per-zone
    `ZONE_TOKENS` hex tints baked into the mobile client.
  - `variant_copy`: the only kid-facing prose field. Comes verbatim
    from `content/sanctuary/seasonal_variants.json` when an authored
    variant matches the current season (preferring one whose
    `element_ref` is one of the kid's already-unlocked elements; falls
    back to the first season-matching variant in content order so a
    fresh kid still sees a seasonal line). `null` only when no variant
    is authored for the current season.

  **Known limitation — Northern Hemisphere only.** The selector hardcodes
  a Northern-Hemisphere calendar. A Southern Hemisphere user will see
  "autumn" tint in May; this is documented in
  `backend/app/sanctuary/season.py`. The Phase 3 `SeasonHandler` will
  swap this helper for one that consults the kid's coarse `geohash4`
  hemisphere — the same coarse-only region signal the rest of the app
  already uses, never precise lat/lng.

- `soundscapes: list[SanctuarySoundscapeDTO]` — author-time placeholder
  entries describing future ambient sounds. Each carries:
  - `id`, `kind` (one of `bird_chirp`, `pond_ripple`, `meadow_buzz`,
    `wind`, `frog_croak`), `zone_id` (or `null` for a general ambient
    bed), `label` (short title), `description` (one calm sentence).
  - No asset URLs, no play tokens, no signed-GET URLs. The DTO is
    descriptive only.

- `sound_assets_available: bool` — false in this PR. When real audio
  assets ship in a later PR this flag flips to true and the mobile
  client can wire a play control to the existing DTO without a new
  field. The mobile screen NEVER autoplays sound, requests microphone
  permission, or adds analytics for sound interactions; this is
  enforced by the absence of those code paths.

---

## 10. Mobile UX

### Seasonal banner & sound placeholder panel (PR adding seasonal variants and sound placeholders)

Two more surfaces ride on top of the delight layer:

- **SeasonBanner** sits between the screen header and the Dragonfly guide
  bar. It renders the authored season label (`Spring` / `Summer` /
  `Autumn` / `Winter`), the `background_tone` word, and -- when
  present -- the authored `variant_copy` line verbatim. The tint comes
  from a tiny per-season palette in the screen itself (`SEASON_TOKENS`),
  not from the wire response; the API only ships the structural tone
  word.
- **Per-band seasonal accent** under each zone's mood line. The text
  comes from `data.season.zone_accents[zone_id]` (e.g. "wildflowers
  waking" on a spring meadow band). Hidden when the accent string is
  missing.
- **SoundscapesPanel** sits between Tiny Surprises and Quiet Corners.
  It is text-only: a "Sounds are off. Audio will arrive in a later
  update." hint, followed by one row per soundscape. Each row shows an
  `OFF` badge, the authored `label`, and the authored `description`.
  No play button, no auto-play, no microphone request, no analytics
  ping on render. When `sound_assets_available` flips to `true` in a
  later PR the hint text changes and the same row layout can host a
  play control without a new wire field.

### Delight panels (PR adding delight layer)

Three new panels were added to the Sanctuary screen on top of the MVP
diorama:

- **IdentityReflectionPanel** — sits between the Dragonfly guide bar
  and the diorama. Renders `data.identity_reflection.text` verbatim
  in a soft italic style. Hidden when the server returns `null`.
- **RelationshipMomentsPanel** — sits after the diorama. Renders one
  green-tinted chip per relationship moment; tap opens the existing
  `ElementInspectModal` via a small adapter
  (`_momentToElement`) that synthesizes a `SanctuaryElementDto`
  from the slim moment DTO.
- **TinySurprisesPanel** — sits after the relationship panel.
  Renders one row per tiny surprise with the title + detail; tap
  opens the same inspect modal via `_surpriseToElement`.

All copy comes from the API response (which in turn comes from
authored `content/sanctuary/` JSON). No client-fabricated
motivational copy beyond panel headers ("Relationships", "Tiny
surprises", "Quiet corners", "Story"). No social buttons, no
share, no location, no streak / FOMO language.

### MVP tab shipped (placeholder art)

The first iteration of the Sanctuary tab is live at
[`mobile/app/(tabs)/sanctuary.tsx`](../mobile/app/(tabs)/sanctuary.tsx)
backed by [`mobile/src/sanctuary/useSanctuary.ts`](../mobile/src/sanctuary/useSanctuary.ts)
(TanStack Query) and the typed API client at
[`mobile/src/api/sanctuary.ts`](../mobile/src/api/sanctuary.ts). The
diorama is rendered from React Native primitives (`View`, `Text`,
`Pressable`, `Modal`, `ScrollView`) tinted per habitat — no art
assets, no new heavy dependencies. The tab is registered in
[`mobile/app/(tabs)/_layout.tsx`](../mobile/app/(tabs)/_layout.tsx)
with the same `IS_WEB ? null : "/sanctuary"` hidden-on-web rule the
other kid-facing tabs use. Loading / error / empty states are
implemented; pull-to-refresh uses `RefreshControl`; tap-to-inspect
opens a modal with the element title / detail / icon-key.

Remaining work (later PRs):

- Final illustration, audio bed, and soft motion beyond opacity fades
  — rendering direction decided in
  [ADR 0012](adr/0012-sanctuary-2point5d-diorama.md): a Skia-based
  2.5D layered painterly diorama (supersedes the true-3D track in
  [ADR 0011](adr/0011-sanctuary-3d-rendering.md), whose implementation
  is frozen on `feature/sanctuary-3d`).
- Lottie / Reanimated 3 reveal beat (post-submit celebration; tracked
  separately from this MVP because the dispatcher reveal flow ships
  in the same PR cluster as the on-submit celebration sequence).
- Mystery cue silhouettes once art lands.
- Real authored guide audio.

### screen

A new Sanctuary route under the Expo Router file-based tree
(`mobile/app/`), matching the existing pattern from `docs/mobile.md`:
*"Navigation: Expo Router (file-based). Routes under `app/` map to
screens."* Whether the route is registered as a bottom-tab or a
stack screen is a `mobile/app/` decision — `docs/mobile.md` does not
enumerate a tab bar and this doc does not bind it.

The Sanctuary route is hidden until the kid has submitted their
first observation — same rule the Dex and expedition map already
use, to avoid empty-state fatigue (per `docs/onboarding.md`). Once
revealed, it stays revealed.

### living diorama, not a full map

The Sanctuary view renders the seven zones as a single stylized
scene — illustrated, hand-feeling, frame-budgeted. It is **not** a
MapLibre / OSM surface, **not** a coordinate grid, and **not** a
geographic map of any kind. Zones are content-authored compositional
regions, not procedurally placed pins. There are no place names, no
pins, no overhead view.

The renderer behind this scene is decided in
[ADR 0012](adr/0012-sanctuary-2point5d-diorama.md): a 2.5D layered
painterly diorama — seven floating islands in one vista, with
tap-to-dive into each island. The invariants in this section bind any
renderer; the RN-primitives 2D screen remains the permanent fallback.

### tap-to-inspect

Tapping an element (a creature, a plant, a piece of scenery) opens
a small inspector card with:

- the element's name
- a one-line authored blurb from `content/sanctuary/`
- a back-link to the kid's matching Dex entry, if any

No comment field. No share button. No external link reachable by the
kid (per the app-store compliance checklist: *"No external links
reachable by kids in Phase 1"*).

### Dragonfly guide

A small in-scene character — Dragonfly — surfaces short,
*pre-authored* lines pointing the kid at the next outdoor thing to
look for. The lines are static content shipped in JSON. There is no
chat. There is no on-demand generation. Per `AGENTS.md`:
*"Kid-facing runtime LLM calls are forbidden."*

### mystery cues

Not-yet-unlocked content is rendered as faint silhouettes or empty
patches with a one-line Dragonfly-guide nudge ("look for bugs in
the grass" / "check under leaves"). The kid sees "there is more out
there" without being told what species count as which thing —
preserving the surprise of real discovery. Never reveals the answer.
Never reveals a precise location.

### journal / timeline (kid-facing label: "Story")

A scrollable per-zone timeline of the kid's own unlocks and
threshold crossings, sourced from `sanctuary_elements` and
`sanctuary_events` joined to the
kid's own `observations`. Uses `FlashList` per the existing
`docs/mobile.md` performance rule.

**Private and per-kid.** There is no group, classroom, or family
view of another kid's journal. The endpoint that backs this view
returns only the requesting kid's own rows.

### new-arrival reveal

After a successful observation submit, if the dispatcher returned at
least one `world_unlock` or `world_evolution`, the existing
celebration sequence (per `docs/mobile.md`, max 2-second Reanimated
3 + Lottie beat, sorted by reward `weight` desc) plays the Sanctuary
reward *after* the higher-weight rewards. The 2-second budget is not
extended; if more than three rewards fire on the same submission,
the celebration sequence may compress per-reward animation time
rather than overflow the budget.

The kid can then optionally tap through to the Sanctuary screen to
see the change in context. No auto-navigation, no hijacking the
post-submit flow.

**MVP shipped.** A minimal reveal modal is wired at
[`mobile/src/sanctuary/SanctuaryRevealModal.tsx`](../mobile/src/sanctuary/SanctuaryRevealModal.tsx),
mounted by [`mobile/app/observe-submit.tsx`](../mobile/app/observe-submit.tsx).
When ``createObservation()`` returns at least one ``world_unlock`` or
``world_evolution``, the kid sees a card with header *"Something
changed in your Sanctuary"* + the reward `title` / `detail` from the
dispatcher payload + two buttons: **See Sanctuary** (navigates to the
Sanctuary tab) and **Done** (returns via `router.back()`). Both
buttons fire-and-forget invalidate the `["sanctuary", "me"]` query so
the tab fetches fresh state on next visit. The modal:

- Uses only ``Modal``/``Pressable``/``Text``/``View`` -- no new heavy
  dependencies, no animation beyond a fade.
- Has no auto-dismiss timer; the kid sits on it as long as they want.
- Dismisses via either button OR a backdrop tap.
- Renders no precise location, no social buttons, no streak / FOMO
  copy. Only `title` / `detail` / `icon` (asset key) from the
  dispatcher reward, plus the modal chrome strings above.
- Does not fire when the dispatcher returns no Sanctuary rewards --
  the existing submit-success path runs unchanged.

Higher-weight rewards (e.g. `first_find` at 80) still rank above
`world_unlock` (60) per `docs/dispatcher.md`'s weight convention.
This MVP renders only the first Sanctuary reward verbatim and shows
"+ N more change(s)" if multiple Sanctuary rewards fired on the same
submission; the full Reanimated 3 + Lottie weighted-sort celebration
sequence is a later PR.

### render-time independence

Opening the Sanctuary screen must work with no network connection.
All assets are bundled or cached; all state is read from
Postgres-derived data already on device. The architecture invariant
that *"the kid experience must not depend on iNaturalist,
Google/Maps, moderation, or rarity refresh being available at the
moment of submission"* extends here to *rendering* as well.

### no fabricated rewards

Consistent with `docs/mobile.md`: the client never fabricates a
Sanctuary unlock. The reveal beat only fires from dispatcher-returned
rewards.

---

## 11. Safety / privacy

The Sanctuary is the first Phase 2 surface that lives *inside the
app between trips*, so it gets explicit scrutiny against the
kid-safety posture in `AGENTS.md` and the store-policy posture in
[`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).

### no social surface

There is no way, from inside the Sanctuary, to:

- view another kid's Sanctuary
- send a message
- write free text
- broadcast a screenshot
- discover, search, or browse other users

This is enforced by the absence of those endpoints, not by a hidden
toggle.

### no precise location display

The Sanctuary never renders a kid's lat/lng, a map of where they
were, a recognizable home/backyard rendering, or a place name finer
than the coarse `geohash4` cell already used for region-level
rarity (~30 km). Per Risk 0007: *"only a coarse 4-character geohash
(~30 km cell) is shared outside the kid's group per the privacy
DRAFT."* The Sanctuary, being a stylized habitat scene, simply does
not need precise coordinates — and explicitly does not consume them.

**Whichever of Risk 0007's options (A / B / C / D) the operator
selects for the precise-location question, the Sanctuary feature is
invariant under that decision**: it never depends on
`ACCESS_FINE_LOCATION`. Option B (coarse location only, the
recommended technical path in Risk 0007) is sufficient for the
Sanctuary's region-aware behavior (e.g. seasonal variants tied to
hemisphere/season).

### no external links from the Sanctuary surface

Consistent with the app-store compliance checklist (*"External
links: None reachable by kids in Phase 1"*). The future iNat
species page link (Phase 2+) sits behind a parent gate and is
reached from the Dex, not from the Sanctuary.

### no third-party SDKs added

Sentry remains the only third-party SDK; the Sanctuary does not add
analytics, ads, attribution, or any new SDK.

### no IAP, no loot, no streaks

The Sanctuary has no store entitlements, no randomized rewards, and
no time-pressure mechanics. Apple Kids Category and Google Play
Designed-for-Families constraints from the app-store checklist hold
without exception.

### no push notifications for Sanctuary events

Push remains a separate Phase 2 candidate (`docs/roadmap.md`) and
explicitly does not ride on Sanctuary unlocks. No badge counts, no
"come back to see your monarch" nudges, no app-icon dots.

### no kid-facing runtime LLM

Author-time LLM tooling may generate draft Sanctuary copy for adult
review (same as expedition draft tooling in
`scripts/draft_expedition.py`); reviewed copy ships as static JSON.
There is no LLM call on a kid's request path. There is no chat UI
inside the Sanctuary.

### personal and per-user

Sanctuary tables are keyed by `user_id`. There is no group-level
Sanctuary, no shared Sanctuary, and no read path that returns
another user's Sanctuary state in Phase 2.

### pilot-window posture

The pilot is adult-supervised, Internal-testing-only, and runs under
signed parental consent (Option C language in Risk 0007). The
Sanctuary surface inherits this posture and adds no new collection.
**The Sanctuary must not be promoted past Internal testing while
Risk 0007 is Open.**

### account deletion

Sanctuary rows are deleted on account-deletion alongside
observations, dex_entries, and memberships. The in-app deletion path
that Play's Data Safety form requires applies to Sanctuary state as
well.

### definition-of-done check

Per `AGENTS.md`: *"No kid-facing privacy, safety, or LLM invariant
is weakened."* The Sanctuary PR series must explicitly attest to
this in each PR description, and any future Sanctuary change that
touches these boundaries requires an ADR.

---

## 12. Phase plan

Seven steps, in order. Each is one PR-sized unit of work; each
leaves the app shippable. Nothing rewrites the submission spine.

1. **Content schema.** Define `app/models/sanctuary.py` (Pydantic
   `Sanctuary` model — zones, unlocks, charismatic taxa, relationship
   moments, evolutions, routing map) modeled after
   `app/models/expedition.py`. Add `scripts/validate_sanctuary.py`
   mirroring `scripts/validate_content.py`: walk
   `content/sanctuary/` with `rglob("*.json")`, validate each file
   with `Sanctuary.model_validate(...)`, enforce `path.stem ==
   sanctuary.id`, exit 0 / 1 with per-file report. Wire into CI on
   `content/**` changes and a `make validate-sanctuary` target.
   Author the seven zone files and the iconic_taxon → zone routing
   map.
2. **DB model.** Add Alembic migration introducing
   `sanctuary_zone_state`, `sanctuary_elements`,
   `sanctuary_observation_contributions`, and `sanctuary_events`
   tables. SQLAlchemy models in `backend/app/db/models.py`. Unique
   constraint on `(user_id, zone_id, element_id)` for
   `sanctuary_elements` so first-element is atomic via
   `INSERT … ON CONFLICT DO NOTHING`.
   `sanctuary_observation_contributions` uses `observation_id` (FK
   to `observations.id`) as its primary key, giving the dispatcher
   structural per-observation replay safety. Content stays as JSON
   under `content/sanctuary/` validated by the Pydantic schema from
   PR #96 — no Postgres-side content cache is added in this PR. No
   DynamoDB-style `WORLD#` / `ZONE#` keys.
3. **WorldHandler.** Implement `WorldHandler` at
   `app/dispatcher/handlers/world.py` conforming to the existing
   `Handler` Protocol. Add `world_unlock` and `world_evolution` to
   the `RewardType` Literal and the weight table in
   `docs/dispatcher.md`. Append `WorldHandler()` to `HANDLERS` after
   `RarityHandler()`. Snapshot tests including replay no-op
   (dispatcher snapshot #11) and predecessor-order assertion.
4. **API.** Add a single `GET /v1/sanctuary` read endpoint returning
   the kid's full Sanctuary state in one query — zone tiers,
   unlocked elements, recent-arrivals journal. Read-only.
   **Additive only — `POST /v1/observations` shape is not modified.**
5. **Mobile screen.** New route under `mobile/app/` for the
   Sanctuary screen. Renders from the API response plus the cached
   content map. Hidden until first observation lands. Tap-to-inspect,
   mystery cues, journal/timeline. No reveal beat yet; this PR
   ships the screen as a quiet surface.
6. **Reveal flow.** Wire `world_unlock` and `world_evolution`
   rewards into the existing post-submit celebration sequence.
   Respect the 2-second budget and the existing weight-desc sort. No
   new client-side reward fabrication; everything driven by the
   dispatcher payload.
7. **Delight / content expansion.** Charismatic unlocks (50–100
   species), relationship moments, tiny surprises, seasonal
   variants gated on Phase 3 `SeasonHandler`. Content-only PRs from
   this point; no further schema or handler changes required.

### content layout

```
content/
  sanctuary/
    zones.json
    coarse_unlocks.json
    charismatic_unlocks.json
    relationship_moments.json
    guide_lines.json
    mystery_cues.json
    tiny_surprises.json
    seasonal_variants.json
  schema/
    sanctuary.schema.json
```

Single file per content kind keeps validation and authoring simple;
per-element-file sharding can come later if files get unwieldy
(`scripts/validate_content.py` already walks with `rglob` so a
nested layout drops in without a tooling change). Each file is a JSON
object with one top-level key matching the content kind
(`{"zones": [...]}`, `{"coarse_unlocks": [...]}`, ...); element `id`
fields inside the JSON are lowercase snake_case (enforced by
[`backend/app/models/sanctuary.py`](../backend/app/models/sanctuary.py)).
The `content/sanctuary/` tree is created in step 1 and populated
through steps 1 and 7; no other steps need to author content.

`content/schema/sanctuary.schema.json` is generated, not authored —
[`scripts/regenerate_schema.py`](../scripts/regenerate_schema.py)
emits it from `SanctuaryConfig.model_json_schema()`, and CI fails if
the committed file drifts from the model.

### authoring future Sanctuary content

When you want to add or edit Sanctuary content:

1. Edit or add JSON to `content/sanctuary/<kind>.json` (one of the
   eight files in the flat layout above).
2. Run `python scripts/validate_content.py` locally — it validates
   both expedition and sanctuary content. The per-file pass checks
   shapes; the whole-tree pass resolves cross-references (relationship
   moment refs, seasonal variant element_refs, zone refs).
3. Run `python scripts/regenerate_schema.py` if you added new fields
   to `backend/app/models/sanctuary.py`. This re-emits
   `content/schema/sanctuary.schema.json`; CI catches drift via
   `git diff --exit-code`.
4. CI runs the same validator on every push that touches
   `content/sanctuary/**`, `backend/app/models/sanctuary.py`,
   `scripts/validate_content.py`, or `scripts/regenerate_schema.py`
   (see [`.github/workflows/content-validate.yml`](../.github/workflows/content-validate.yml)).

Charismatic unlock authoring rule: every entry ships with
`taxon_id_verified: false` until an author has manually opened
`inaturalist.org/taxa/<id>` and confirmed the numeric id matches the
intended species. The flag is advisory in this PR; a follow-up will
gate production builds on `taxon_id_verified=true`.

Every step preserves every Phase 1 invariant. The submission spine
is untouched. The dispatcher contract is unchanged. The kid still
earns the Sanctuary by going outside.
