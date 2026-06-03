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
- **Coarse iconic_taxon unlocks** — every observation lights up
  something in the matching zone, even an obscure beetle.
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
  appeared, private to the kid.

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

Flavor: the catch-all for taxa that don't fit the six above, and for
observations whose iconic_taxon is missing or unrecognized.

- Anything not mapped above (e.g. `Protozoa`, `Chromista`, unknown).
- Also receives observations whose iconic_taxon could not be
  resolved at submission time; these still light *something* up so
  the kid is never punished for the taxonomy backend's gaps.

A single observation may light up **one primary zone** (highest-
priority match) and at most one **overlay** (urban). The mapping
table is the source of truth; the database is a materialized view.

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
- `weight` — **40**. Same band as `expedition_step` /
  `mission_progress`: meaningful, but ranked below `first_find` (80)
  and `unrecorded` (100).
- `payload` — `{"zone": str, "unlock_id": str, "taxon_id": int | null}`.

### `world_evolution`

An existing unlock deepened — a threshold crossed, a relationship
moment lit up, a seasonal variant appeared.

- `title` — short. e.g. "Your meadow is fuller."
- `detail` — names what changed.
- `icon` — asset key.
- `weight` — **30**. Same band as `expedition_complete` /
  `territory_claimed`. Ambient, not headline.
- `payload` — `{"zone": str, "evolution_id": str, "threshold": int | null}`.

### ordering and tie resolution

The client already sorts by `weight` desc; Sanctuary rewards
therefore appear *after* `first_find` / `unrecorded` / high
`rarity_tier` rewards in the celebration sequence. This is
intentional: the headline is what the kid did in the real world; the
Sanctuary update is the echo.

Explicit weight comparison: a same-observation `first_find`
(weight 80) outranks a same-observation `world_unlock` (40); both
fire and animate in sequence per the existing celebration model.

On ties, the dispatcher resolves by **handler registration order**
(stable). Because `WorldHandler` is registered after `DexHandler`,
`ExpeditionHandler`, and `RarityHandler`, a same-weight
`expedition_step` (40) from `ExpeditionHandler` appears *before* a
same-weight `world_unlock` (40) in the celebration sequence —
preserving the principle that real-world progression headlines
over Sanctuary echoes.

### sketch

```json
{
  "type": "world_unlock",
  "title": "A new corner of your Sanctuary.",
  "detail": "Your meadow has a monarch now.",
  "icon": "sanctuary.meadow.monarch.unlock",
  "weight": 40,
  "payload": {"zone": "meadow", "unlock_id": "meadow.monarch", "taxon_id": 48662}
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
**after** `DexHandler`, `ExpeditionHandler`, and `RarityHandler`. The
Phase 2 order becomes:

```python
HANDLERS: list[Handler] = [
    DexHandler(),
    ExpeditionHandler(),
    RarityHandler(),
    WorldHandler(),        # Phase 2: reads dex first-find state; never blocks
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
  surprises). Backs the per-zone journal/timeline in §10.

Content (`content/sanctuary/` JSON, validated by the Pydantic
schema in PR #96) is **not** mirrored into a Postgres table in this
PR; the API reads zone/element metadata from the in-process content
map. A `sanctuary_content` cache table can be added later if a
product need emerges.

---

## 10. Mobile UX

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

### journal / timeline

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
