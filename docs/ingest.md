# Ingest Pipelines

Ingest is a production subsystem for moving external or repo-authored data into
Hinterland safely.

## Sources

| Source | Owner | Source of truth | Runtime effect |
|---|---|---|---|
| `observation` | API | Postgres transaction | Kid sees submitted observation |
| `photo` | GCS/Eventarc | Cloud Storage object | Photo lifecycle moves pending to clean or quarantine |
| `expedition_content` | Repo | `content/expeditions/` | Postgres materialized view for app reads |
| `species_taxa` | iNaturalist | iNat taxa API | Cached species metadata |
| `rarity_snapshot` | iNaturalist | iNat species counts | Rarity cache for dispatcher |
| `geocoding_cache` | Google Maps | Google Geocoding/Places | Cached place labels and nearby onboarding data |
| `moderation_event` | Moderation provider | Provider response | Review queue or clean photo transition |
| `telemetry` | Cloud Logging/Monitoring | Service logs | Product and ops dashboards |

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

## Hot Path Rule

Observation submission can enqueue or record ingest work, but the kid-facing
success response must not wait on moderation, iNaturalist submission, rarity
refresh, content sync, or AI tooling.

