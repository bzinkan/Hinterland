# Ingest Pipelines

Ingest is a production subsystem for moving external or repo-authored data into
Hinterland safely.

## Sources

| Source | Owner | Source of truth | Runtime effect |
|---|---|---|---|
| `observation` | API | Postgres transaction | Kid sees submitted observation |
| `photo` | Observation API | Azure Blob canonical object + PostgreSQL outbox | Verified photo becomes eligible for moderation |
| `expedition_content` | Repo | `content/expeditions/` | Postgres materialized view for app reads |
| `taxonomy_catalog` | Reviewed ingest job | Pinned iNaturalist snapshot + reviewed project additions | Project-owned canonical search/Dex authority |
| `rarity_snapshot` | iNaturalist | iNat species counts | Rarity cache for dispatcher |
| `geocoding_cache` | Configured geocoder | Coarse geohash lookup | Cached coarse place labels |
| `moderation_event` | Azure AI Content Safety | Provider response | Review queue or clean photo transition |
| `telemetry` | Azure Monitor / Log Analytics | Service logs | Product and ops dashboards |

BlobCreated is not an Observation ingest source. Direct storage events may be
used for orphan telemetry only; they cannot enqueue moderation.

Observation/photo finalization is a transactional application write, not a
replayed external feed. The idempotency and moderation-outbox ledgers are its
audit/recovery mechanism. Only the committed outbox relay may publish
canonical-photo work to Service Bus.

## Contract

Each ingest run records:

- `source`
- `source_run_id`
- `status`
- `cursor`
- `checksum`
- `retry_count`
- `last_error`
- timestamps

An ingest run is safe to retry when the same `source` and `source_run_id`
arrive more than once. Duplicate events should be skipped or merged, not
double-applied.

## Replay

Replay commands should accept source, source run ID, or cursor range. They must
write structured logs and update `ingest_runs` so the operator can tell whether
the replay repaired the failure or produced a new one.

## Taxonomy Catalog

The PostgreSQL taxonomy catalog is the only runtime authority for kid-facing
identification. Its ingest is explicit, replayable, and recorded in
`ingest_runs`. The loader upserts canonical name, rank, ancestor IDs, aliases,
active status, catalog version, source timestamp, and indexed search fields
from a pinned source snapshot.

Child requests never fall through to a live iNaturalist taxon lookup. An ingest
failure leaves the prior catalog active; bundled catalog/manual/Unknown remains
available. Checked-in manifests pin each downloadable pack's version, checksum,
byte size, and taxon count. Mobile verifies a pack before atomically replacing
its SQLite copy. Pack selection uses adult choice or coarse region only.

Removing or renaming a taxon is not an in-place destructive edit. Mark it
inactive or add an alias, publish a new catalog version, and queue affected
derived-state rebuilds when canonical ID meaning changes.

## Coarse Geocoding Cache

New cache keys use `geohash4`; raw child coordinates do not appear in URLs,
cache keys, logs, or persisted Observation rows. A successful cache fill and
its cache state commit together. Provider failure is a cache miss and cannot
block Observation saving.

## Moderation Is Outbox-Driven

The provider response is recorded against the exact committed
observation/photo work item. A malformed Azure success response is a failed
attempt, not a clean decision. Duplicate Service Bus delivery is idempotent;
the worker verifies canonical object and terminal database state before
provider or copy work.

Replaying taxonomy, rarity, or content updates source material for future
submissions. It must not silently rewrite historical derived state; that is an
explicit per-user rebuild with a recorded reason/version.

## Hot Path Rule

Observation submission can enqueue or record ingest work, but the kid-facing
success response must not wait on moderation, iNaturalist submission, rarity
refresh, content sync, or AI tooling.

