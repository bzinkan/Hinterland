# The Dispatcher

ADR 0004 defines atomic first-find ownership. ADR 0015 makes Observation
rewards durable, replayable, and rebuildable without weakening immediate kid
success.

## One-Sentence Contract

The API saves one authoritative observation first, then runs deterministic
handlers under one outer transaction with a savepoint per handler and persists
every handler's state, status, version, attempts, and rewards.

## Core Types

`Reward` is immutable presentation data: type, title, detail, weight, and
JSON-serializable payload. `Context` is built once per observation and carries
the canonical user/group/observation identity plus handler results restored
from the ledger. `HandlerResult` contains a handler's durable state and reward
list.

Handlers may read/write PostgreSQL through the provided session. They must not:

- commit or roll back the outer transaction;
- catch and convert SQL errors into fabricated success;
- call external services; or
- assume an in-memory predecessor result exists when its ledger row failed.

## Registration And Dependencies

The stable registry order is:

1. `dex`
2. `rarity`
3. `world`
4. `expedition`

Names and versions are durable identifiers. Renaming a handler or changing the
meaning of persisted result state requires a version/migration plan.

Hard dependencies are explicit. World and Expedition consume successful Dex
state. If Dex fails, those rows become `blocked`; they do not run against an
invented `is_first_find=false` result. A handler without a hard dependency may
continue after another handler fails.

## Durable Execution

The durable dispatcher acquires the per-user PostgreSQL advisory lock and:

1. loads all ledger rows for the observation;
2. restores state/rewards already succeeded at the same handler version;
3. checks hard dependencies;
4. marks an eligible row `running` and increments its attempt count;
5. executes the handler inside `begin_nested()` and flushes its writes;
6. records `succeeded` plus JSON state/rewards, or records `failed` after the
   savepoint rolls back;
7. marks dependent rows `blocked` when a predecessor has not succeeded;
8. combines only persisted successful rewards and sorts by weight descending
   with stable registry-order tie breaking; and
9. stores that aggregate on the observation.

`dispatched_at` is set only when every required row succeeds. Otherwise the
observation remains `pending` or `partial`. The create request still returns
the saved observation; mobile says rewards are catching up.

## Handler Ownership

### Dex

`DexHandler` owns the atomic `(user_id, taxon_id)` insert and membership
`dex_count`. Catalog taxon IDs can produce first/repeat rewards. Manual text and
Unknown never create a Dex entry.

### Rarity

`RarityHandler` reads only cached regional tiers. With no location it skips
location-dependent rewards. It owns `memberships.rarest_tier` and must update it
with a single rank-aware SQL expression.

### World/Sanctuary

`WorldHandler` consumes successful Dex state and writes one idempotent
Sanctuary contribution per observation. Handler errors propagate to the
dispatcher; they are not converted into an in-handler error flag.

### Expedition

`ExpeditionHandler` consumes successful Dex state, checked-in Expedition
content, ecology tags, and optional coarse location. An
`expedition_observation_contributions` gate makes replay idempotent. Only
observations after Expedition enrollment count. Radius rules decline to match
when precise-enough location does not exist; W1 content must not require it.

## Replay

The replay job claims observations with pending, failed, or blocked handler
rows using `FOR UPDATE SKIP LOCKED`, then runs the same durable dispatcher under
the same user lock. Successful same-version rows restore their persisted state
and are not executed again.

Replay never calls the submission endpoint, increments the base observation
counter, recreates the observation, or replays a celebration. A version
mismatch is explicit work, not silent reuse.

## Rejection And Correction Rebuild

Incremental undo is unsafe: rejecting a first find can change which later
observation owns the Dex entry and can alter Expedition and Sanctuary history.
Rejection or revision-checked identification correction therefore queues a
`derived_state_rebuilds` row in the same transaction as the authoritative
change.

The rebuild:

1. acquires the per-user PostgreSQL advisory lock;
2. loads accepted observations ordered by `(observed_at, id)`;
3. clears observation-derived Dex, Expedition contribution, Sanctuary, handler
   ledger, counter, and reward projections;
4. preserves Expedition enrollment/start times;
5. redispatches accepted observations under one outer transaction; and
6. publishes the replacement state only when every required handler succeeds.

Triggers coalesce per user and retry five times. Rebuild never emits mobile
celebrations or notifications.

## Testing Contract

Unit and disposable PostgreSQL tests cover:

- handler ordering and stable reward sort;
- savepoint rollback after handler SQL failure;
- predecessor failure and dependent `blocked` state;
- persisted reward ordering and lost-response reconciliation;
- replay without duplicate Dex, Expedition, Sanctuary, or counters;
- one-observation Expedition contribution gates;
- concurrent dispatcher/rebuild serialization; and
- rejection replacing first-find and all derived state.

The W1 release gate repeats failure/replay against real PostgreSQL and requires
dispatcher p95 below 300 ms.

## Adding A Handler

1. Define one stable name, version, and result-state schema.
2. Document hard predecessors and handler-owned tables/counters.
3. Make writes idempotent at the database level.
4. Add the handler to the registry in dependency order.
5. Add unit, real-PostgreSQL failure/replay, and snapshot coverage.
6. Extend rebuild cleanup/replay when the handler creates derived state.
7. Update this document and add an ADR if data access or privacy changes.
