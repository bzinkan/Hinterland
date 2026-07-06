# Architecture

## System At A Glance

Hinterland is an Azure-hosted field app with one kid-facing hot path:
observation submission. Everything slow or failure-prone, including photo
moderation, iNaturalist submission, rarity refresh, ingest, and admin replay,
must run outside that hot path.

Platform choices are documented in
[ADR 0010](adr/0010-azure-target-architecture.md). ADR 0010 supersedes the
earlier GCP ADRs 0005, 0008, and 0009.

```text
Expo mobile / parents web
        |
        | HTTPS
        v
Azure Container Apps: FastAPI / uvicorn
        |
        +--> Entra External Identities (adult access tokens)
        +--> Hinterland RS256 kid JWTs (handoff/session)
        +--> Azure Database for PostgreSQL Flexible Server
        +--> Azure Blob Storage (photos, SAS URLs)
        +--> iNaturalist API (CV + eventual project submit)
        +--> Azure AI Content Safety (async moderation provider)
        +--> Azure Monitor / Log Analytics
```

Current W1 Internal Testing posture: iNat public submission is off, moderation
provider defaults to noop unless explicitly configured, and parent setup is
web-first through the parents app. Kids sign in by scanning the QR handoff that
the adult generates from Classroom.

## Observation Hot Path

1. Kid captures or chooses a photo.
2. Client calls `POST /v1/photos/presign` and uploads JPEG bytes to
   `pending/<photo_id>.jpg` in the private photos container.
3. Client calls `POST /v1/observations` with the stable observation shape.
4. API validates ownership, inserts the observation, and atomically bumps the
   membership observation counter.
5. Dispatcher runs after the observation is committed. It invokes Dex,
   Rarity, Expedition, and World/Sanctuary handlers with exception isolation.
6. Rewards return to the client for the celebration sequence. Failure in any
   handler does not fail submission; replay can recover missing rewards.
7. Async moderation/iNat work may run later. The kid already saw success.

The submission endpoint shape is intentionally stable. Add handlers and workers;
do not keep expanding the endpoint with feature-specific branches.

## Auth Model

Adults authenticate with Microsoft Entra External Identities. Verified Entra
tokens are resolved to local `users` rows by `users.entra_oid`.

Kids never enter email/password. A parent/teacher provisions a kid, the backend
mints a 15-minute single-use Hinterland handoff JWT, and the kid app exchanges
that at `POST /v1/auth/kid-exchange` for a 30-day Hinterland session JWT.
Hinterland kid JWTs are RS256 and are verified against
`/.well-known/dragonfly-kid-jwks.json` (the path keeps the legacy name; it is a
deployed client contract — see ADR 0013).

The backend augments request identity from Postgres on every real token path:
`CurrentUser.uid` is the canonical local `users.id`. Route code should resolve
the row through `resolve_current_user_row(...)`, not by directly querying
`firebase_uid`.

## Component Responsibilities

**API service.** FastAPI on Azure Container Apps. Owns synchronous HTTP,
dispatcher execution, signed URL issuance, Entra/Hinterland auth verification,
and source-of-truth writes to Postgres.

**Photo storage.** Azure Blob Storage private container. `pending/`,
`observations/`, and `quarantine/` prefixes remain the lifecycle vocabulary.
Signed PUT/GET URLs are SAS URLs behind the existing storage protocol.

**Moderation worker.** Async worker code classifies pending photos through the
`Moderator` protocol. Azure AI Content Safety is the production provider; noop
is allowed only for local/dev/W1 Internal Testing. Provider outage must retry or
hold pending; never default-allow on safety outage.

**iNaturalist submit worker.** Async, retryable, and idempotent by Hinterland
observation id. It is disabled until the iNat OAuth/project account risk is
closed and moderation clean-path wiring exists.

**Rarity refresh and admin jobs.** Container Apps jobs or equivalent scheduled
Azure jobs run rarity refresh, stale-review sweep, and dispatcher replay. They
must be idempotent and auditable.

**Content sync.** Expedition and Sanctuary JSON in `content/` is the source of
truth. Postgres tables are materialized views synced by scripts/CI.

## External Dependencies

| Dependency | Used For | Failure Mode |
|---|---|---|
| iNaturalist CV | Species suggestions | Return `cv_unavailable=true`; kid can choose manually |
| iNaturalist submit | Science contribution | Queue/retry later; kid submission already succeeded |
| Azure AI Content Safety | Photo moderation | Hold/retry pending; do not default-allow |
| Reverse geocoding | Place names | No-op/cache miss is acceptable |
| USA-NPN (future) | Phenology | Weekly cache, never hot-path required |

## Deployment And Observability

Active API deploys use `.github/workflows/deploy-azure-api-dev.yml`: build in
ACR, update Azure Container Apps, run Alembic, smoke public probes, and
optionally run `scripts/smoke_azure_parent_kid.py` with an operator-provided
Entra bearer token.

The legacy `.github/workflows/deploy-cloud-run-dev.yml` is intentionally
manual/no-op. It must not deploy or recreate Cloud Run after ADR 0010.

Structured logs go to Azure Log Analytics. Key operational events should
include `observation_id`, `user_id`, `group_id`, reward types, and
`duration_ms` for dispatcher completion. Azure Monitor alerts should cover API
5xx/latency, Postgres pressure, async queue/DLQ depth, iNat failures,
moderation failures, dispatcher replay backlog, and budget anomalies.

## Invariants

1. Observation submission shape stays stable.
2. First-find detection uses atomic conditional writes.
3. Kid-facing runtime LLM calls are forbidden.
4. Moderation and iNat submission are asynchronous.
5. Expedition/Sanctuary JSON is source of truth.
6. Leaderboard counters live on membership rows.
7. Ingest/admin jobs are idempotent, replayable, and auditable.
8. No ads, marketing pushes, public chat, DMs, or kid-to-kid free text in Phase 1.
