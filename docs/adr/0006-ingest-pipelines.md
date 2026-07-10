# ADR 0006: Ingest pipelines are explicit and replayable

- **Status:** Accepted
- **Date:** 2026-05-06
- **Deciders:** Solo author
- **Related:** ADR 0005, ADR 0004

## Context

Hinterland has more ingest than the hot observation endpoint: photo upload
events, expedition content sync, iNaturalist taxa data, rarity snapshots,
geocoding cache fills, moderation results, and operational telemetry. If each
one becomes an ad hoc script or worker, retries and partial failures will be
hard to reason about before closed beta.

## Decision

Treat ingest as a first-class subsystem.

Every ingest path must have:

- a stable `source`
- a stable `source_run_id`
- an idempotency key or content hash
- a cursor when work spans more than one batch
- structured logs
- retry-safe behavior
- an audit row in Postgres when it changes durable state

The Postgres `ingest_runs` table records source, source run ID, status, cursor,
checksum, retry count, last error, start time, and completion time.

Repo-authored content remains source of truth. Postgres stores the materialized
view, never the canonical expedition copy.

## Consequences

- Failed content, species, rarity, moderation, and telemetry jobs can be
  replayed without bespoke one-off recovery work.
- External APIs can be rate-limited and cached behind one consistent pattern.
- Ingest code has more ceremony up front, but closed-beta operations become
  calmer and more inspectable.

## Guardrails

- Do not put third-party API calls in dispatcher handlers.
- Do not make kid-facing submission success depend on ingest completion.
- Do not mutate expedition content directly in the database.
- Any ingest job that writes child-proximal data must log enough context for
  audit and deletion workflows without logging secrets or raw child data.

