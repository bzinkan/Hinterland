# Postgres Data Model

ADR 0010 supersedes the original DynamoDB and GCP-era designs. Hinterland now
uses Azure Database for PostgreSQL Flexible Server as the operational store.

The logical product invariants are unchanged:

- first-find detection is atomic
- leaderboard counters live on membership rows
- expedition JSON in git is source of truth
- slow integrations do not block kid-facing submission success
- dispatcher handlers do not call external APIs

## Core Tables

| Table | Purpose |
|---|---|
| `users` | Entra-backed adults and Hinterland-signed kid identities |
| `groups` | Invite-only class/family groups with join codes |
| `memberships` | User membership plus leaderboard counters |
| `photos` | Azure Blob attachment state plus verified canonical metadata |
| `observations` | Authoritative kid observations, moderation, identification, dispatch status, and persisted rewards |
| `dex_entries` | First species finds per user |
| `expedition_content` | Materialized view of repo-authored expedition JSON |
| `expedition_progress` | Per-user progress through active expeditions |
| `review_queue` | Teacher/adult review for quarantined photos |
| `ingest_runs` | Replayable ingest audit and cursor state |
| `job_state` | Durable cursors for scheduled/background jobs |
| `species_cache` | Versioned project-owned runtime taxonomy catalog |
| `taxonomy_packs` | Published immutable pack metadata and Blob location |
| `cv_suggestion_cache` | Post-clean suggestions keyed by canonical photo SHA-256 and CV model version |
| `geo_cache` | Coarse-geohash reverse-geocode cache |
| `rarity_cache` | Regional rarity tiers consumed by `RarityHandler` |
| `observation_idempotency` | Operation-scoped request hashes for presign/create replay |
| `moderation_outbox` | Committed canonical-photo work awaiting Service Bus relay |
| `observation_handler_runs` | Versioned per-handler status, state, attempts, and rewards |
| `derived_state_rebuilds` | Adult-visible rejection/correction compensation jobs |
| `expedition_observation_contributions` | One-observation-per-expedition replay gate |
| `sanctuary_zone_state` | Per-user per-zone observation counts and depth tier |
| `sanctuary_elements` | Per-user record of which named Sanctuary unlocks have fired |
| `sanctuary_observation_contributions` | Idempotency gate keyed on `observation_id` for `WorldHandler` replay |
| `sanctuary_events` | Append-only audit log of Sanctuary state changes shown to the kid |

## Access Patterns

| Access pattern | SQL shape |
|---|---|
| Load current user | `select * from users where id = $1` after token verification / cache resolution |
| Resolve adult identity | `select * from users where entra_oid = $1` |
| Load group members | `select * from memberships where group_id = $1` |
| Group leaderboard | `select * from memberships where group_id = $1 order by dex_count desc` |
| User observations | `select * from observations where user_id = $1 order by created_at desc` |
| Submission replay | Read `observation_idempotency` by `(user_id, idempotency_ulid, operation)` and compare normalized request hash |
| Group observations | `select * from observations where group_id = $1 order by created_at desc` |
| User Dex | `select * from dex_entries where user_id = $1 order by first_seen_at desc` |
| First find | `insert into dex_entries (...) on conflict (user_id, taxon_id) do nothing` |
| Expedition progress | `select * from expedition_progress where user_id = $1` |
| Active expedition focus | `select * from expedition_progress where user_id = $1 and focused_at is not null` |
| Review queue | `select * from review_queue where group_id = $1 and status = 'pending'` |
| Handler replay claim | Lock pending/failed/blocked `observation_handler_runs` with `FOR UPDATE SKIP LOCKED` |
| Derived rebuild claim | Coalesce/lock `derived_state_rebuilds` by `user_id` |
| Rarity lookup | `select * from rarity_cache where region_geohash = $1 and taxon_id = $2` |
| Taxonomy search | Search active `species_cache` normalized/indexed name and aliases; no live fallback |
| Taxonomy pack | Read active `taxonomy_packs` by pack ID/version and sign the private Blob |
| CV suggestion replay | Read `cv_suggestion_cache` by canonical photo SHA-256 and model version |
| Ingest replay | `select * from ingest_runs where source = $1 and status = 'failed'` |
| Sanctuary zone state | `select * from sanctuary_zone_state where user_id = $1 and zone_id = $2` |
| Sanctuary first-fire | `insert into sanctuary_elements (...) on conflict (user_id, zone_id, element_id) do nothing` |
| Sanctuary replay gate | `insert into sanctuary_observation_contributions (observation_id, ...) values ($1, ...)` |
| Sanctuary timeline | `select * from sanctuary_events where user_id = $1 order by created_at desc` |

## Atomic Submission Transaction

`POST /v1/observations` must commit the core product state in one transaction:

1. Acquire the per-user PostgreSQL advisory lock.
2. Recheck the operation-scoped idempotency key/request hash and lock the photo
   reservation.
3. Attach the verified canonical photo and insert exactly one observation.
4. Increment the membership observation counter exactly once.
5. Insert one pending handler ledger row per registered handler.
6. Insert the moderation outbox row.
7. Persist the successful create idempotency result.

After that transaction commits, the dispatcher runs under the same per-user
serialization contract and persists handler state/rewards. An infrastructure
or handler failure does not roll back the authoritative observation; it leaves
`dispatch_status=pending|partial` for durable replay.

The first-find check must not become read-then-write. The database unique
constraint is the source of truth under concurrency.

`observations.ecology_tags` stores closed-choice kid selections used by
Expedition steps, such as `{ "life_stage": "flower" }`. Tags are optional,
validated against the approved key/value catalog at `POST /v1/observations`,
and are never free text or runtime-LLM output.

## Observation And Photo Field Contract

`photos.attachment_status` is separate from moderation:

- `reserved`: SAS issued, not attached to an observation;
- `attached`: canonical JPEG verified and linked;
- `deleted`: bytes must not be served.

Verified photo metadata includes canonical object name, byte count, decoded
dimensions, SHA-256, and verification time. One photo may back only one
observation. The photo and observation share a submission ULID unique per user.

`observations.moderation_status` is one of `pending`, `processing`, `clean`,
`quarantine`, `pilot_private`, `rejected`, or `failed`, with a separate
`moderation_source` (`none|noop|azure|adult`) and policy version. Attachment or
Blob arrival never implies a moderation decision.

Observations record `observed_at`, optional `geohash4`, and
`location_source=device_coarse|manual_coarse|none|legacy_coarsened`. New raw
latitude/longitude is not durable data. Compatibility latitude/longitude input
is converted in memory, never stored or logged.

Identification records a project-catalog taxon ID or manual/Unknown choice,
`identification_source`, and `identification_revision`. When a taxon ID is
present, the server ignores client-supplied names and uses the catalog's
canonical display name. Manual and Unknown observations are Dex-ineligible.

## Handler And Work Ledgers

`observation_handler_runs` is keyed by observation, stable handler name, and
handler version. Rows record dependency-aware status, attempt count, JSON state,
and persisted rewards. `dispatched_at` is set only when every required handler
succeeds; `dispatch_status=partial` means durable recovery remains.

`moderation_outbox` is unique per observation. Only its committed rows may
produce Service Bus messages. Direct BlobCreated/Event Grid moderation is
forbidden.

`derived_state_rebuilds` coalesces per user. Rejection and revision-checked
identification correction enqueue it transactionally. Expedition contribution
gates prevent one observation from advancing the same step twice under replay.

## Additive Migration Procedure

The Observation repair uses new Alembic revisions; applied migrations are not
edited. Deployment must:

1. report duplicate photo, submission, review, and negative-counter keys;
2. keep the earliest observation for a duplicated photo, tombstone later rows,
   and queue affected users for rebuild;
3. backfill compatible legacy submission keys while leaving the columns
   nullable for the old-API migration-first window; the new API always writes
   them and the scheduled cutover reconciler fills any race rows;
4. map observed photos to attached/pending and leave unattached rows reserved;
5. set legacy `observed_at=created_at`;
6. retain only an existing `geohash4`, null precise coordinates, and mark the
   source `legacy_coarsened`;
7. register every attached pending legacy observation in `moderation_outbox`;
8. canonicalize old `pending/<photo_id>.jpg` bytes before the relay can publish
   them, rejecting invalid/missing bytes and queuing a rebuild;
9. mark historical derived state `unverified` and queue rebuilds;
10. validate unique/check constraints only after reconciliation; and
11. retain compatibility columns/status mappings for one mobile release.

## Rejection And Identification Correction

Rejection tombstones the authoritative observation, immediately denies photo
access, and queues a rebuild instead of piecemeal counter decrements. A
revision-checked identification correction uses the same rebuild path.

The rebuild acquires the same user advisory lock and atomically regenerates
membership counters, Dex, Expedition contribution, rarity, Sanctuary, handler
ledger, and stored rewards from accepted observations ordered by
`(observed_at, id)`. Expedition enrollment/start times remain intact. Normal
reads never aggregate observations at request time.

## Expedition Progress

Expedition progress is personal kid game state. `expedition_progress.user_id`
is the dispatch and read scope; `group_id` remains on the row as creation
context for audit/history, not as the active progression boundary. The mobile
quest loop highlights one focused incomplete expedition at a time through
nullable `focused_at`, with a partial unique index enforcing at most one focused
row per user.

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
derived from observation history via `WorldHandler` and the four tables below.

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
