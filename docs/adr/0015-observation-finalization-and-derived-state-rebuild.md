# ADR 0015: Idempotent observation finalization and derived-state rebuilds

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** Product owner and implementation agent
- **Related:** ADR 0004, ADR 0006, ADR 0010, ADR 0013, ADR 0014
- **Supersedes:** ADR 0010's synchronous/direct-Event-Grid moderation
  sequencing. Azure remains the target platform, but committed PostgreSQL
  outbox rows are the sole moderation producer.

## Context

The original observation flow treated a Blob-created event as the moderation
trigger and generated an observation only after upload. That creates three
failure classes:

1. moderation can move a photo while a child is still identifying it;
2. a lost create response can duplicate the observation, counters, and rewards;
3. rejection or a later identification correction can leave Dex, Expedition,
   rarity, and Sanctuary state inconsistent with accepted observations.

The W1 build must also work without iNaturalist or real moderation and must not
persist precise child location.

## Decision

### Client submission identity

The mobile client creates one ULID submission key and sends it as
`Idempotency-Key` to both photo presign and observation create. PostgreSQL
stores operation-scoped request hashes. Replaying the same operation and
payload returns the original resource; reusing the key with different input is
a 409. One photo can back exactly one observation.

### Photo finalization

Blob arrival is not a moderation trigger. Presign creates a reserved photo and
uploads into `pending/uploads/`. Observation create validates and canonicalizes
the JPEG, attaches it, inserts the observation and counter update, and writes
moderation plus dispatcher work records transactionally. Only committed outbox
work may reach moderation.

NoOp moderation records an explicitly private pilot result; it never records a
clean safety decision and never enables iNaturalist egress.

### Identification and location

Kid-facing identification uses the project-owned taxon catalog. Manual display
text and Unknown may be saved but cannot create a Dex entry. Image CV is an
optional post-clean action only.

New clients send only an optional four-character geohash and provenance. Raw
coordinates are discarded before persistence. No-location observations remain
valid and location-dependent handlers skip them.

### Rewards and compensation

Observation success remains immediate. Handler runs persist their version,
status, state, and rewards. Handler failures leave durable pending/failed work
for replay and do not mark the full dispatch complete.

Rejection and identification correction immediately tombstone or revise the
authoritative observation and enqueue a per-user rebuild. The rebuild takes a
per-user PostgreSQL advisory lock and atomically regenerates membership
counters, Dex, Expedition, rarity, Sanctuary, handler results, and stored
rewards from non-rejected observations. Rebuilds use current checked-in content
and cache snapshots and never replay celebrations.

## Consequences

- Offline and lost-response retries are safe without changing the core
  observation route.
- Moderation and third-party availability remain outside kid-facing success.
- Rejection is more expensive than an incremental decrement, but it is rare
  and deterministic.
- Historical precise coordinates are intentionally removed during migration;
  only an already-derived `geohash4` may remain.
- Direct Blob-created moderation and pre-clean CV must not be restored as a
  rollback mechanism.
- Migration-first cutover keeps submission keys nullable for the previous API
  revision. Attached legacy pending rows are inserted into the moderation
  outbox, but the relay requires verified canonical metadata. A temporary
  scheduled reconciler safely adopts old flat `pending/` bytes before and after
  API cutover; invalid bytes are rejected and rebuilt rather than stranded.
