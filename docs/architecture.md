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
        +--> Project-owned PostgreSQL taxonomy catalog
        +--> iNaturalist API (optional post-clean CV; public submit disabled)
        +--> Azure AI Content Safety (async moderation provider)
        +--> Azure Monitor / Log Analytics
```

Current W1 Internal Testing posture: iNat public submission is off, moderation
provider defaults to noop unless explicitly configured, and parent setup is
web-first through the parents app. Kids sign in by scanning the QR handoff that
the adult generates from Classroom.

## Observation Hot Path

1. The kid captures or chooses a photo. Mobile normalizes it, writes it to the
   document directory, and records an owner-scoped SQLite queue row with a
   client-generated ULID submission key.
2. The client calls `POST /v1/photos/presign` with that key and uploads to
   `pending/uploads/<photo_id>.jpg` using the headers returned by the API.
3. Identification during capture uses the bundled/project-owned catalog.
   Manual display text and Unknown remain valid but do not create a Dex entry.
4. `POST /v1/observations`, using the same idempotency key, validates and
   canonicalizes the JPEG, strips metadata, and stores immutable bytes under
   `pending/finalized/`.
5. One PostgreSQL transaction attaches the photo, inserts exactly one
   observation, atomically increments the membership counter, and records the
   moderation outbox plus pending dispatcher-handler rows.
6. The dispatcher runs handlers under per-handler savepoints. It persists each
   handler's version, status, state, and rewards. A failed handler leaves the
   observation saved with `dispatch_status=partial`; replay resumes only
   incomplete work.
7. Only the committed moderation outbox can produce Service Bus work. Direct
   BlobCreated moderation is forbidden. W1 NoOp results are `pilot_private`,
   not clean safety approvals.
8. iNaturalist image CV is available only as an optional post-clean action.
   Public iNaturalist submission stays disabled pending a separate consent and
   geoprivacy project.

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

**Photo storage.** Azure Blob Storage private container. Raw reservations use
`pending/uploads/`; server-verified canonical JPEGs use `pending/finalized/`;
NoOp results move to private `pilot-private/` for seven-day lifecycle;
resolved provider results use `observations/` or `quarantine/`. Signed PUT/GET URLs are
SAS URLs behind the existing storage protocol. Pending, quarantined,
pilot-private, rejected, and deleted photos are never readable by a child.
Clean photos are readable by their owner and an authorized managing adult;
quarantine is adult-reviewer-only. Same-group peer children never receive a
photo URL.

**Taxonomy catalog.** Replayable audited ingest promotes pinned, reviewed
taxonomy content into PostgreSQL. The bundled core pack and authenticated
catalog search are the only immediate kid-facing authority. A child request
never falls through to a live iNaturalist taxa lookup.

**Moderation worker.** Async worker code claims committed outbox work through a
lease and classifies canonical photos through the `Moderator` protocol. Azure
AI Content Safety is the closed-beta provider; noop is allowed only for
local/dev/W1 and records `pilot_private`. Provider outage or malformed success
payloads retry/hold pending and never default-allow.

**iNaturalist submit worker.** Async, retryable, and idempotent by Hinterland
observation id. It is disabled until the iNat OAuth/project account risk is
closed and moderation clean-path wiring exists.

**Rarity refresh and admin jobs.** Container Apps jobs run rarity refresh,
stale-review sweep, legacy-photo cutover reconciliation, moderation-outbox
relay, dispatcher replay, retention, health probes, and derived-state rebuild.
They must be idempotent and auditable. The temporary legacy reconciler is the
only path from old flat `pending/` bytes to verified canonical outbox work.

**Content sync.** Expedition and Sanctuary JSON in `content/` is the source of
truth. Postgres tables are materialized views synced by scripts/CI.

## External Dependencies

| Dependency | Used For | Failure Mode |
|---|---|---|
| Project taxon catalog | Immediate canonical identification | Bundled core/region cache and PostgreSQL search; manual/Unknown remains available |
| iNaturalist CV | Optional post-clean suggestions | Requires enable, disclosure-approved, and benchmark-approved gates; otherwise catalog/manual/Unknown remains available |
| iNaturalist submit | Science contribution | Queue/retry later; kid submission already succeeded |
| Azure AI Content Safety | Photo moderation | Hold/retry pending; do not default-allow |
| Reverse geocoding | Place names | No-op/cache miss is acceptable |
| USA-NPN (future) | Phenology | Weekly cache, never hot-path required |

## Deployment And Observability

Active API deploys use `.github/workflows/deploy-azure-api-dev.yml`: contain the
old revision by removing its iNaturalist token aliases and discovering storage
system topics, build one immutable ACR digest from the repo root, run the
read-only Observation preflight and additive Alembic migrations, sync taxonomy
and Expedition content, reconcile legacy pending bytes, rebuild derived state,
pin every worker/job, cut API traffic, reconcile/rebuild the old-revision race,
then run public, auth, and Observation canaries.

The legacy `.github/workflows/deploy-cloud-run-dev.yml` is intentionally
manual/no-op. It must not deploy or recreate Cloud Run after ADR 0010.

Structured logs go to Azure Log Analytics. Key operational events should
include `observation_id`, `user_id`, `group_id`, reward types, and
`duration_ms` for dispatcher completion, but never raw coordinates, SAS URLs,
photo bytes, child-entered manual species text, or provider credentials. Azure
Monitor alerts cover API 5xx/latency, Postgres pressure, moderation/pending
photo age, queue/DLQ depth, rebuild backlog/failure, idempotency conflicts,
state mismatches, dispatcher replay backlog, and budget anomalies.

New clients persist only optional `geohash4`; no location is valid. Legacy
latitude/longitude is accepted for one compatibility release, converted to
`geohash4` in memory, and never persisted or logged. Reverse geocoding accepts
a coarse geohash in a request body rather than raw coordinates in a URL.

Rejection and revision-checked identification correction queue the same
per-user deterministic rebuild. The rebuild shares the submission advisory
lock and atomically regenerates counters, Dex, Expedition, Sanctuary, handler
ledgers, and persisted rewards. It never replays celebrations.

## Invariants

1. Observation submission shape stays stable.
2. First-find detection uses atomic conditional writes.
3. Kid-facing runtime LLM calls are forbidden.
4. Moderation and iNat submission are asynchronous.
5. Expedition/Sanctuary JSON is source of truth.
6. Leaderboard counters live on membership rows.
7. Ingest/admin jobs are idempotent, replayable, and auditable.
8. No ads, marketing pushes, public chat, DMs, or kid-to-kid free text in Phase 1.
9. Direct BlobCreated moderation and pre-clean image egress are forbidden.
10. New observation location is coarse or absent; raw coordinates are not durable data.
11. Deployments run additive migrations first, then API/workers/jobs from one immutable digest.
