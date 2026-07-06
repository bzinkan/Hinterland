# Postgres Data Model

ADR 0005 supersedes the original DynamoDB single-table design. Hinterland now
uses Cloud SQL for PostgreSQL as the operational store.

The logical product invariants are unchanged:

- first-find detection is atomic
- leaderboard counters live on membership rows
- expedition JSON in git is source of truth
- slow integrations do not block kid-facing submission success
- dispatcher handlers do not call external APIs

## Core Tables

| Table | Purpose |
|---|---|
| `users` | Firebase-backed parent, teacher, and kid identities |
| `groups` | Invite-only class/family groups with join codes |
| `memberships` | User membership plus leaderboard counters |
| `photos` | GCS object lifecycle state |
| `observations` | Kid observations and denormalized display fields |
| `dex_entries` | First species finds per user |
| `expedition_content` | Materialized view of repo-authored expedition JSON |
| `expedition_progress` | Per-user progress through active expeditions |
| `review_queue` | Teacher/adult review for quarantined photos |
| `ingest_runs` | Replayable ingest audit and cursor state |
| `job_state` | Durable cursors for scheduled/background jobs |
| `species_cache` | Cached iNaturalist taxa metadata |
| `geo_cache` | Cached reverse geocode and nearby-place data |
| `rarity_cache` | Regional rarity tiers consumed by `RarityHandler` |
| `sanctuary_zone_state` | Per-user per-zone observation counts and depth tier |
| `sanctuary_elements` | Per-user record of which named Sanctuary unlocks have fired |
| `sanctuary_observation_contributions` | Idempotency gate keyed on `observation_id` for `WorldHandler` replay |
| `sanctuary_events` | Append-only audit log of Sanctuary state changes shown to the kid |

## Access Patterns

| Access pattern | SQL shape |
|---|---|
| Load current user | `select * from users where firebase_uid = $1` |
| Load group members | `select * from memberships where group_id = $1` |
| Group leaderboard | `select * from memberships where group_id = $1 order by dex_count desc` |
| User observations | `select * from observations where user_id = $1 order by created_at desc` |
| Group observations | `select * from observations where group_id = $1 order by created_at desc` |
| User Dex | `select * from dex_entries where user_id = $1 order by first_seen_at desc` |
| First find | `insert into dex_entries (...) on conflict (user_id, taxon_id) do nothing` |
| Expedition progress | `select * from expedition_progress where user_id = $1` |
| Review queue | `select * from review_queue where group_id = $1 and status = 'pending'` |
| Rarity lookup | `select * from rarity_cache where region_geohash = $1 and taxon_id = $2` |
| Ingest replay | `select * from ingest_runs where source = $1 and status = 'failed'` |
| Sanctuary zone state | `select * from sanctuary_zone_state where user_id = $1 and zone_id = $2` |
| Sanctuary first-fire | `insert into sanctuary_elements (...) on conflict (user_id, zone_id, element_id) do nothing` |
| Sanctuary replay gate | `insert into sanctuary_observation_contributions (observation_id, ...) values ($1, ...)` |
| Sanctuary timeline | `select * from sanctuary_events where user_id = $1 order by created_at desc` |

## Atomic Submission Transaction

`POST /v1/observations` must commit the core product state in one transaction:

1. Insert the observation row.
2. Update the membership observation counter.
3. Attempt `dex_entries` insert with `ON CONFLICT DO NOTHING`.
4. If the insert wins, update the membership Dex counter.
5. Run deterministic dispatcher handlers against already-persisted state.
6. Store reward output on the observation row.
7. Enqueue async work for iNaturalist submission and other slow tasks.

The first-find check must not become read-then-write. The database unique
constraint is the source of truth under concurrency.

## Ingest And Cursors

`ingest_runs` is the operational record for replayable data movement. Content
sync, taxa refresh, rarity snapshots, moderation events, and telemetry-derived
jobs all write run state before mutating durable app data.

Failed ingest runs are replayed by source and cursor. Replays must be
idempotent and must not duplicate observations, Dex rows, expedition rows, or
review queue items.

## Sanctuary State

Sanctuary is per-user derived state -- a materialized view of observation
outcomes, not an authoritative source. Authoritative state lives on
`observations`, `dex_entries`, and `memberships` rows. Sanctuary state is
derived from observation history via `WorldHandler` (Phase 2, not implemented
yet) and the four tables below.

### `sanctuary_zone_state`

Per-user, per-zone observation count and current depth tier. One row per
`(user_id, zone_id)`.

- ULID `id` PK; `user_id` FK to `users.id` (ondelete CASCADE)
- `zone_id` `String(40)` NOT NULL; `observation_count` and `depth_tier` Integer
  NOT NULL (server default 0)
- Bookkeeping pointers `first_unlocked_observation_id` and
  `last_evolved_observation_id` are FK to `observations.id` with
  ondelete=SET NULL so zone state survives observation deletion
- `last_observed_at` DateTime(tz) nullable
- `created_at` + `updated_at` via the standard timestamp mixin
- `UniqueConstraint(user_id, zone_id)` enforces the one-row-per-zone shape
- `Index(user_id, depth_tier)` supports per-user tier sweeps

### `sanctuary_elements`

Per-user record of which named Sanctuary unlocks (zone wake-ups, charismatic
species, relationship moments, surprises, signature finds) have fired.

- ULID `id` PK; `user_id` FK to `users.id` (ondelete CASCADE)
- `zone_id` `String(40)` NOT NULL; `element_id` `String(80)` NOT NULL;
  `element_type` `String(40)` NOT NULL
- `element_type` `CheckConstraint` in `('coarse', 'charismatic',
  'relationship', 'surprise', 'signature')`
- `source_observation_id` FK to `observations.id` ondelete=SET NULL,
  `taxon_id` Integer nullable
- `payload` JSONB NOT NULL default `{}` carries the reward-shape snapshot for
  the celebration sequence
- `unlocked_at` DateTime(tz) NOT NULL; `created_at` + `updated_at` via mixin
- `UniqueConstraint(user_id, zone_id, element_id)` is the atomic-first-fire
  gate. `INSERT ... ON CONFLICT DO NOTHING` wins exactly one row; this mirrors
  the Dex first-find pattern and satisfies the AGENTS.md invariant that
  first-find detection must be an atomic conditional write
- `Index(user_id, zone_id)` supports per-zone unlock reads

### `sanctuary_observation_contributions`

The structural idempotency gate for `WorldHandler` replay. One row per
observation that has already contributed to Sanctuary state.

- PK is `observation_id` (FK to `observations.id`, ondelete=CASCADE). The
  observation IS the gate, so there is no separate ULID `id` column
- `user_id` FK to `users.id` (ondelete CASCADE)
- `zone_id` `String(40)` NOT NULL; `taxon_id` Integer nullable;
  `iconic_taxon` `String(80)` nullable
- `element_ids` JSONB NOT NULL default `[]` lists the element rows fired by
  this observation
- `created_at` only (no `updated_at`); rows are write-once
- If the dispatcher replays the same observation twice, the second `INSERT`
  raises a primary-key collision on `observation_id`. The WorldHandler treats
  that collision as the signal to skip every counter bump and element fire
  from this observation. This is how Sanctuary survives Phase 8's
  dispatcher-replay scenario without double-counting

### `sanctuary_events`

Append-only audit log of Sanctuary state changes the kid saw. Drives the
on-submit celebration sequence and the journal / timeline screen.

- ULID `id` PK; `user_id` FK to `users.id` (ondelete CASCADE)
- `observation_id` FK to `observations.id` (ondelete=SET NULL so events
  survive observation deletion as audit), nullable
- `event_type` `String(40)` NOT NULL with `CheckConstraint` in
  `('world_unlock', 'world_evolution', 'relationship', 'surprise')`
- `zone_id` and `element_id` nullable; `title` `String(100)` NOT NULL;
  `detail` `String(240)` nullable
- `payload` JSONB NOT NULL default `{}`
- `created_at` only (no `updated_at`); rows are immutable once written
- `Index(user_id, created_at)` supports the timeline read

### Sanctuary preserves leaderboard and privacy invariants

Sanctuary state is **not** on `memberships` rows. Leaderboard counters stay
on `memberships` (per the AGENTS.md invariant: "Leaderboard counters live on
membership rows. Do not aggregate observations at read time for normal
leaderboard reads.") and the Sanctuary tables are completely independent of
the leaderboard path. The `sanctuary_zone_state.observation_count` field is a
per-zone counter scoped by the `(user_id, zone_id)` unique key; it never
participates in any leaderboard read and never replaces the membership-level
counter.

**No precise location is stored in any Sanctuary-specific row.** Zone routing
happens in `WorldHandler` (Phase 2) from the observation's `iconic_taxon` and
the content map; precise lat/lng never lands in `sanctuary_zone_state`,
`sanctuary_elements`, `sanctuary_observation_contributions`, or
`sanctuary_events`. `tests/test_sanctuary_schema.py` enforces this at the
schema level: any column whose name contains `lat`, `lng`, `geo`, `addr`,
`place`, or `coord` on any Sanctuary table fails CI.

## Local Development

Local development uses `backend/compose.yaml` Postgres and Alembic:

```bash
make dev-db
make db-migrate
make dev
curl localhost:8080/ready
```
