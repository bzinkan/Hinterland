# Architecture

## System at a glance

Dragonfly is a managed-services app on GCP, built around one synchronous request path (observation submission) and two asynchronous workers (photo moderation, rarity cache refresh). Everything else — Dex, leaderboard, expeditions — is derived data computed at submission time and cached.

Platform choices are documented in [ADR 0005](adr/0005-gcp-target-architecture.md). ADR 0001 (single-table DynamoDB) is superseded by Cloud SQL for PostgreSQL.

```
┌─────────────────┐
│  Expo client    │  iOS / Android / web
│  (React Native) │
└────────┬────────┘
         │ HTTPS (Firebase ID token)
         ▼
┌──────────────────┐     ┌──────────────────┐
│  Cloud Run       │────▶│ Firebase Auth    │  (JWKS verify)
│  FastAPI         │     └──────────────────┘
│  (uvicorn)       │
└────────┬─────────┘
         │
         ├──────────────▶  Cloud SQL (Postgres: dragonfly)
         ├──────────────▶  Cloud Storage (photos, V4 signed URLs)
         ├──────────────▶  iNaturalist API (CV + project submit)
         ├──────────────▶  Cloud Tasks (iNat submit queue, with DLQ)
         └──────────────▶  Eventarc (GCS object-finalized → moderation)
                              │
                              ▼
                 ┌────────────────────────────────┐
                 │  Cloud Run workers             │
                 │  ─ moderation (Eventarc-triggered)
                 │  ─ inat_submit (Cloud Tasks consumer)
                 │  ─ rarity_refresh (Cloud Run job, Cloud Scheduler cron)
                 └────────────────────────────────┘
```

## The one important request path

An observation submission is the hot path. Everything that matters — first-find celebration, expedition progress, leaderboard updates, rarity tier — is computed here, in this order:

1. Client uploads photo to the `pending/` prefix in the photos bucket via a V4 signed URL.
2. Client calls `POST /observations` with `{ photo_key, lat, lng, taxon_id, ... }`.
3. Cloud Run validates and writes the observation row to Postgres in a single transaction (observation insert + membership-counter update + Dex `INSERT ... ON CONFLICT DO NOTHING` for the first-find detection).
4. **Dispatcher runs.** The `Context` object (db, user, group, observation, location) is passed through every handler in `HANDLERS`. Each returns zero or more `Reward`s.
5. Rewards are returned to the client, sorted by weight desc. The client renders the celebration sequence from that list.
6. A Cloud Tasks task is enqueued for `inat_submit` to push to iNaturalist out of band.
7. Moderation runs independently via Eventarc on GCS object-finalized — if a photo is flagged post-submission, the observation is moved to quarantine and the teacher review queue picks it up.

The client never knows or cares which handler produced which reward. Adding Territory in Phase 2, Seasons in Phase 3, Missions in Phase 4 is purely additive on the server.

## Component responsibilities

**API service (Cloud Run, FastAPI on uvicorn).** All synchronous HTTP. Owns the dispatcher. Aim for p95 < 500ms. No blocking iNat calls on the hot path; that's what Cloud Tasks is for. Mangum handler remains in `app.main` per AGENTS.md compatibility rule until the AWS path is intentionally removed.

**Moderation worker (Cloud Run service, Eventarc-triggered).** Triggered by GCS `google.cloud.storage.object.v1.finalized` on the `pending/` prefix. Calls the moderation provider (see follow-up ADR). Clean photos are copied to `observations/`; flagged photos go to `quarantine/` and write a `REVIEW#` row. See `moderation.md`.

**iNat submit worker (Cloud Run service, Cloud Tasks consumer).** Handles retries (Cloud Tasks-managed exponential backoff), dedup via idempotency key (observation id), and writes the returned iNat observation id back onto the observation row. On terminal failure (e.g. iNat down for > 24h), the task is routed to the DLQ queue and an alert fires.

**Rarity refresh (Cloud Run job, Cloud Scheduler cron).** Cron at 03:00 UTC posts to a Cloud Run job. Self-continues via a `JOB#rarity` cursor row if it runs out of time. Never parallelizes — iNat rate limits matter more than throughput. See `rarity-pipeline.md`.

## External dependencies and failure modes

| Dependency | Used for | Failure mode |
|---|---|---|
| iNaturalist CV | Species ID on upload | Fallback: let kid free-text select; log `cv_unavailable` flag on the observation |
| iNaturalist submit | Scientific contribution | Queue retries; kid sees success regardless (we own the project account) |
| Moderation provider (TBD per follow-up ADR; likely Cloud Vision SafeSearch) | Photo moderation | If API errors, hold in `pending/` and retry with exponential backoff; do not default-allow |
| USA-NPN (Phase 3) | Phenology windows | Weekly sync to local cache; offline-first |

The app must degrade gracefully on all of these. The kid experience cannot depend on iNat or NPN being reachable at the moment of submission.

## Auth model

Firebase Authentication with custom claims `role` (`parent` | `teacher` | `kid`) and `group_id` set on join. The Firebase ID token is verified on every API request via the `core/auth.py` dependency, which validates against the Firebase JWKS and reads the custom claims. Kids under 13 have no email — they authenticate via a group join code exchanged for a Firebase user provisioned by the parent/teacher at join time.

One iNaturalist account is owned by the app (the "project account"). All observations are submitted as that account, tagged with our project. Kids do not have iNat accounts until they turn 13 and opt into the claim flow (Phase 3).

## Deployment

Cloud Run services per environment in GCP project `dragonflyapp-495423`. Environments: `dev` (scale-to-zero), `staging` (one shared), `prod` (`min_instances=1` to keep the Postgres pool warm). GitHub Actions deploys on merge to `main` for `dev`; `staging` and `prod` are manual-approve workflows. Build via Cloud Build; image artifacts in Artifact Registry.

Secrets live in **Secret Manager**, loaded at Cloud Run cold start via `pydantic-settings`. The Cloud Run service account is granted `roles/secretmanager.secretAccessor` on specific secrets only, never at project scope. `DRAGONFLY_*` env vars on the service hold *secret names*, not secret values. Never commit secrets.

## Observability

Structured JSON logs from every Cloud Run service, ingested by **Cloud Logging** as native JSON payloads (no parsing config required). One log line per observation submission that includes: `observation_id`, `user_id`, `group_id`, `handler_rewards` (list), `dispatcher_duration_ms`, `taxon_id`. That single line is enough to debug 80% of "why didn't I get a celebration" complaints.

**Error Reporting** auto-aggregates exceptions from the structured JSON. **Cloud Monitoring** holds the alerting policies: API 5xx rate > 1% (Cloud Run request-count metric), moderation DLQ depth > 0 (Cloud Tasks queue-depth metric), rarity job duration > 12 min (Cloud Run job execution-duration metric), iNat submit DLQ depth > 0 (Cloud Tasks queue-depth metric). Everything else is a dashboard, not an alert.

## Key invariants (things to preserve through all four phases)

1. **The submission endpoint never changes shape.** Handlers are added; the endpoint is not modified.
2. **Expedition JSON is the source of truth.** DynamoDB is a materialized view of `content/expeditions/`. A deploy is the only write path.
3. **Conditional `PutItem` is how first-find is detected.** Don't add a read-then-write pattern; it introduces a race.
4. **Moderation happens in S3, not in the API Lambda.** The API path must not block on Rekognition.
5. **Denormalized counters on membership rows are the leaderboard.** Don't aggregate at read time.
