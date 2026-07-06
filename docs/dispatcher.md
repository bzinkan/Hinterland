# The Dispatcher

The dispatcher is the spine of Hinterland. Every observation submission runs through it; every user-visible reward comes out of it. Get this contract right in Phase 1 and the rest of the roadmap is purely additive.

## The one-sentence version

Given a `Context`, the dispatcher runs every registered `Handler` in order and returns the combined list of `Reward`s, which the client renders as the celebration sequence.

## Why a dispatcher

Without it, the observation submission endpoint becomes an ever-growing block of conditionals: "if first find, celebrate; if expedition step matched, advance; if rare species, show tier badge; if territory newly claimed, show the map; if mission complete, fire a push…". Every new feature edits the same hot function, every regression touches every feature, and every test has to set up the universe.

With it, the endpoint has one responsibility: persist the observation and call `dispatch(context)`. Features are strangers to each other. Ordering is explicit. Testing is per-handler. The entire Phase 2–4 roadmap is "add a handler."

## Contracts

### `Reward`

The unit of user-visible feedback. Immutable. Serialized to the client verbatim.

```python
from dataclasses import dataclass, field
from typing import Literal

RewardType = Literal[
    "first_find",        # Dex: first time this user logged this species
    "repeat_find",       # Dex: already had it
    "expedition_step",   # Expedition: a step was matched
    "expedition_complete",
    "rarity_tier",       # Rarity: species falls into a tier for this region
    "unrecorded",        # Rarity: nobody has logged this species here before
    # Phase 2+
    "territory_claimed",
    "season_hit",
    "mission_progress",
    "mission_complete",
]

@dataclass(frozen=True)
class Reward:
    type: RewardType
    title: str            # "New species!" — shown large
    detail: str           # "You're the first in your group to find a Mourning Cloak"
    icon: str             # asset key the client maps to an image/animation
    weight: int = 0       # celebration order, higher = shown first
    payload: dict = field(default_factory=dict)  # type-specific extras
```

**Weight convention** (set once, respect forever):

| Weight | Meaning                                           | Examples                              |
|--------|---------------------------------------------------|---------------------------------------|
| 100    | "This has never happened before in the world"     | `unrecorded` in region                |
| 80     | "This has never happened before for you"          | `first_find`                          |
| 60     | Rare or high-tier contextual                      | `rarity_tier` (legendary/epic), `world_unlock`, `expedition_complete` |
| 40     | Progress on a goal                                | `expedition_step`, `mission_progress` |
| 30     | Goal completion                                   | `territory_claimed`, `world_evolution` |
| 10     | Ambient acknowledgment                            | `repeat_find`                         |

The client sorts by `weight` desc and shows rewards one at a time as a sequence. Equal weights resolve by handler registration order (stable). `expedition_complete` sits at 60 — above its own `expedition_step` at 40 — so completing an expedition celebrates before the step that finished it; its tie with `world_unlock` resolves by registration order (World before Expedition).

### `Context`

The universe the handlers see. Built once by the dispatcher, passed by reference, enriched as handlers run.

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Context:
    # Core — populated at dispatch time, never mutated
    db: AsyncSession                # SQLAlchemy session bound to Postgres
    user: User
    group: Group | None             # kids always have one; parents may not
    observation: Observation        # already persisted to Postgres
    location: Location              # lat, lng, geohash4, geohash3

    # Per-handler outputs, keyed by handler name. Populated as dispatch runs.
    # Lets later handlers read earlier decisions without coupling to the
    # handler class itself. Example: ExpeditionHandler checks
    # ctx.results["dex"].is_first_find to gate a "rare discovery" bonus.
    results: dict[str, "HandlerResult"] = field(default_factory=dict)

@dataclass
class HandlerResult:
    rewards: list[Reward]
    state: dict[str, Any] = field(default_factory=dict)  # handler-defined
```

**Rules for what goes on `Context`:**

- Anything every handler might need (db, user, group, location) is a top-level field.
- Anything one handler produces that others might care about goes in `results[handler_name].state` as typed fields in a handler-owned dataclass.
- Never mutate core fields. Never reach into `results` for a handler that hasn't run yet — ordering is explicit, use it.

### `Handler`

A protocol, not a base class. Duck-typed so tests don't need to subclass anything.

```python
from typing import Protocol

class Handler(Protocol):
    name: str  # unique, stable, used as key in Context.results

    async def handle(self, ctx: Context) -> HandlerResult:
        """Run side effects (DB writes, cache updates) and return rewards.

        Must be:
        - Idempotent (same observation twice = same result, no double writes)
        - Self-contained (no calls to iNat, the moderation provider, or other slow services)
        - Fast (<100ms p95; the full dispatcher budget is 300ms)
        - Exception-safe (failures logged, other handlers still run)
        """
        ...
```

## Registration and ordering

One file, one list. The only place that changes when you add a handler.

```python
# app/dispatcher/registry.py
from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.expedition import ExpeditionHandler
from app.dispatcher.handlers.rarity import RarityHandler

HANDLERS: list[Handler] = [
    DexHandler(),          # Phase 1: must run first — owns DEX# write, dex_count bump, sets is_first_find
    RarityHandler(),       # Phase 1: owns rarest_tier update on MEMBER#; publishes `tier`
    WorldHandler(),        # Phase 2: Sanctuary writes; reads is_first_find + tier; emits world_unlock / world_evolution
    ExpeditionHandler(),   # Phase 1: may read dex state
    # TerritoryHandler(),  # Phase 2
    # SeasonHandler(),     # Phase 3
    # MissionHandler(),    # Phase 4: reads almost everything, runs last
]
```

**Ordering is a real contract.** If `ExpeditionHandler` needs to know whether a find was a first-find to award a bonus, the Dex handler must run first. Document dependencies in the handler's docstring; assert expected predecessors in tests.

**Do not parallelize.** The performance win is negligible (handlers are I/O-bound against Postgres and a single connection pipelines roundtrips well) and the debugging cost of non-deterministic ordering is huge. Sequential, deterministic, easy to reason about.

## The dispatcher itself

The entire thing is small enough to fit on a screen. That's the point.

```python
# app/dispatcher/core.py
import structlog

log = structlog.get_logger()

async def dispatch(ctx: Context, handlers: list[Handler]) -> list[Reward]:
    all_rewards: list[Reward] = []
    for handler in handlers:
        try:
            result = await handler.handle(ctx)
        except Exception:  # noqa: BLE001 — we intentionally catch all
            log.exception(
                "dispatcher.handler_failed",
                handler=handler.name,
                observation_id=ctx.observation.id,
                user_id=ctx.user.id,
            )
            # Record an empty result so later handlers can check presence
            # without worrying about KeyError.
            ctx.results[handler.name] = HandlerResult(rewards=[])
            continue
        ctx.results[handler.name] = result
        all_rewards.extend(result.rewards)

    all_rewards.sort(key=lambda r: r.weight, reverse=True)
    log.info(
        "dispatcher.complete",
        observation_id=ctx.observation.id,
        reward_count=len(all_rewards),
        reward_types=[r.type for r in all_rewards],
    )
    return all_rewards
```

**A failed handler never fails the submission.** The observation is already persisted by the time the dispatcher runs; the worst-case outcome is a missing celebration, which is recoverable (a job can replay it). Anything that *must* succeed for a submission to be valid (moderation, iNat-queueing, the Postgres write itself) happens outside the dispatcher, before or after.

## Concrete Phase 1 handlers

### `DexHandler`

Owns the Dex row write AND the `dex_count` counter increment on the user's membership row. See ADR 0004 for why this is a handler responsibility rather than part of the submission transaction.

```python
class DexHandler:
    name = "dex"

    async def handle(self, ctx: Context) -> HandlerResult:
        try:
            await ctx.db.put_item(
                Item={
                    "PK": f"USER#{ctx.user.id}",
                    "SK": f"DEX#{ctx.observation.taxon_id}",
                    "first_observed_at": ctx.observation.created_at,
                    "first_obs_id": ctx.observation.id,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            is_first_find = True
        except ConditionalCheckFailedError:
            is_first_find = False

        if is_first_find:
            # First-find bumps dex_count on the membership row. Separate write
            # from the DEX# put — acceptable because ADD is commutative and the
            # two-write window is sub-millisecond.
            if ctx.group is not None:
                await ctx.db.update_item(
                    Key={
                        "PK": f"GROUP#{ctx.group.id}",
                        "SK": f"MEMBER#{ctx.user.id}",
                    },
                    UpdateExpression="ADD dex_count :one",
                    ExpressionAttributeValues={":one": 1},
                )
            reward = Reward(
                type="first_find",
                title="New species!",
                detail=f"First {ctx.observation.species_name} in your Dex",
                icon="dex.first_find",
                weight=80,
            )
        else:
            reward = Reward(
                type="repeat_find",
                title="Logged",
                detail=f"Another {ctx.observation.species_name}",
                icon="dex.repeat",
                weight=10,
            )

        return HandlerResult(
            rewards=[reward],
            state={"is_first_find": is_first_find},
        )
```

Downstream handlers read `ctx.results["dex"].state["is_first_find"]`.

### `ExpeditionHandler`

Queries the user's active expedition progress rows, runs each incomplete step's `match` spec against the observation via the matcher registry, advances matched steps, emits `expedition_step` rewards, and — on the final step — an `expedition_complete` reward. Each completed step is recorded in `expedition_progress.completed_steps` as `{"completed_at": <iso string>, "observation_id": <ulid>}` (legacy rows hold a plain iso string); the recorded `observation_id` is a per-observation gate — an expedition whose `completed_steps` already credits this observation is skipped on re-dispatch, so a replay cannot chain one observation through multiple steps. After a restart (`POST /v1/expeditions/{id}/restart`) the gate map is empty, so a re-dispatched old observation may credit the fresh run once — the invariant is one step per expedition per *run*; if that ever needs hardening, a `restarted_at` column is the lever.

Key correctness property: a single observation can match at most one step per expedition (the first unmatched step), but can progress multiple expeditions at once. Document this in the handler's docstring and snapshot-test it. `PATCH /v1/observations/{id}` re-dispatches the observation when its first `taxon_id` lands (the live mobile flow picks the species after create), which is what makes taxon-based steps reachable; corrections (A -> B) and observations that already minted a dex entry do not re-dispatch (one photo can never farm first-find credit), and a failed re-dispatch resets `dispatched_at` to NULL so the nightly replay recovers it.

### `RarityHandler`

Two lookups, both against `REGION#<geohash4>`:

1. Is there a `SPECIES#<taxonId>` row? If yes, emit a `rarity_tier` reward (weight 60 for `legendary`/`epic`, 40 for `rare`, 10 for `common`, suppressed for `abundant`).
2. If the region exists (`META` row present) and the species row does *not*, emit an `unrecorded` reward (weight 100).
3. If the region has `low_data: true`, look at the parent geohash-3 row instead. Never emit tier rewards from a `low_data` region — the signal isn't strong enough.

The rarity data comes from the nightly Cloud Run job (`rarity-pipeline.md`). This handler never calls iNat directly.

This handler also owns the `rarest_tier` counter on the user's membership row. When the observation's tier outranks the previously-recorded `rarest_tier`, issue a conditional `UPDATE` to set it (using a tier-rank comparison in the WHERE clause). Ownership lives here because this is the handler that knows the tier; see ADR 0004.

## Testing

Build the test harness in Week 8, same week as the first handler. The shape is:

```python
# tests/fixtures/dispatcher.py
async def run_dispatch(
    *,
    handlers: list[Handler],
    observation: Observation,
    preload: Callable[[AsyncSession], Awaitable[None]] | None = None,
) -> list[Reward]:
    """Spin up a Postgres test schema, preload state, build Context, dispatch."""
```

Snapshot-test these scenarios, all of them, before declaring Phase 1 done:

| # | Scenario                                                              |
|---|-----------------------------------------------------------------------|
| 1 | First observation ever, common species, common region                 |
| 2 | Repeat find, same species as #1                                       |
| 3 | First find, rare species, common region                               |
| 4 | First find, rare species, region with low_data (uses parent geohash) |
| 5 | Unrecorded species (never seen in region before)                      |
| 6 | Observation completes an expedition step                              |
| 7 | Observation completes the final step of an expedition                 |
| 8 | One observation advances steps in two expeditions simultaneously      |
| 9 | One handler raises; others still produce rewards                      |
| 10 | Same observation submitted twice (idempotency check — no duplicates) |
| 11 | API service crashes after submission transaction but before dispatch; replay recovers (see ADR 0004) |

When Phase 2 ships `TerritoryHandler`, these same 10 snapshots catch any regression where the new handler interferes with a Phase 1 reward.

## Adding a handler — the recipe

1. Create `app/dispatcher/handlers/<name>.py` implementing the `Handler` protocol.
2. If the handler produces state other handlers consume, define a typed dataclass for its `state` dict in the same file, export it, document the contract in the module docstring.
3. Add one line to `HANDLERS` in `registry.py`. Put it in the position that reflects its dependencies.
4. Add a snapshot test for each new scenario it introduces, plus a regression test for every existing scenario to confirm the new handler is a no-op when its preconditions aren't met.
5. Update this doc's handler list and the weight table if you introduce a new `RewardType`.

No step 6. No touching the submission endpoint. That's the whole deal.
