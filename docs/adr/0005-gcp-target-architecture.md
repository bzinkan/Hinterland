# ADR 0005: GCP target architecture

- **Status:** Accepted
- **Date:** 2026-05-05
- **Deciders:** Solo author
- **Supersedes:** ADR 0001 (Single-table DynamoDB)
- **Related:** ADR 0002 (LLMs are author-time, not runtime), ADR 0004 (First-find ownership)

## Context

The original architecture (ADR 0001 and `docs/architecture.md`) assumed an all-AWS serverless stack: API Gateway, Lambda + Mangum, Cognito, DynamoDB, S3, SQS, EventBridge, Rekognition, CloudWatch. AGENTS.md §"Current Direction" pivots the runtime to GCP / Cloud Run.

That pivot leaves six platform questions open. AGENTS.md §2 lists them as a gate before any feature porting:

1. Auth provider
2. Operational datastore
3. Object storage
4. Async work (queues, schedulers, event triggers)
5. Secret storage
6. Observability

This ADR answers all six in one place so subsequent feature work has a single source of truth, and supersedes ADR 0001's datastore choice specifically (Postgres replaces DynamoDB).

The non-negotiable invariants in AGENTS.md §"Non-Negotiable Invariants" are cloud-agnostic and unchanged. This ADR is about *how* they are implemented on GCP, not whether they apply.

## Decision

Use the following GCP services for the Phase 1 target architecture, in GCP project `dragonflyapp-495423`:

### 1. Auth: Firebase Authentication

Firebase Auth handles parent, teacher, and kid accounts. Custom claims carry `role` (`parent` | `teacher` | `kid`) and `group_id`. Kids under 13 authenticate via a group join code that the parent/teacher exchanges for a Firebase user at join time — no email, no password recovery surface for kids.

The FastAPI dependency in `app/core/auth.py` verifies Firebase ID tokens against the Firebase JWKS and extracts custom claims.

Rejected: Identity Platform (same custom-claims surface; only adds SAML/MFA/multi-tenancy that we do not need in Phase 1), Auth0 (extra vendor outside GCP).

### 2. Datastore: Cloud SQL for PostgreSQL

A single Cloud SQL Postgres instance (`db-g1-small` in dev, sized up for prod) holds users, groups, memberships, observations, Dex entries, expedition progress, the rarity cache, the review queue, and job state.

The previous single-table DynamoDB design (ADR 0001) is **superseded**. The reasoning that drove ADR 0001 — solo ops, cold-start friendliness, scale-to-zero pricing — is partially preserved on GCP via Cloud SQL's managed posture (automated backups, patching windows, point-in-time recovery) and Cloud Run's connector pooling, but the access-pattern argument inverts:

- Cloud Run instances are warm enough that connection lifecycle is cheap with the Cloud SQL Auth Proxy / connector.
- Postgres gives us joins, ad-hoc queries for the eventual teacher dashboard, and a familiar mental model that reduces the per-feature cost of every future ADR.
- The first-find invariant (atomic conditional write) maps cleanly onto a unique-constraint `INSERT ... ON CONFLICT DO NOTHING` — preserving the AGENTS.md guarantee without DynamoDB's `ConditionExpression` surface.
- Membership-counter updates remain atomic via a single `UPDATE` in the same transaction as the observation insert.

`docs/data-model.md` will be rewritten under the Postgres schema in a follow-up. Until then, treat the existing DynamoDB key descriptions as a logical model: each `PK#SK` partition becomes a table with the natural foreign keys.

Rejected: Firestore (single-table prefix patterns from ADR 0001 do not translate; document model mismatched with the relational shape that surfaces once we add a teacher dashboard), AlloyDB (overpowered and overpriced for pre-PMF), keep DynamoDB during transition (two operational data stores is the worst of both worlds for a solo dev).

### 3. Object storage: Cloud Storage

One bucket, three prefixes, mirroring the existing S3 design:

- `gs://dragonfly-photos-<env>/pending/` — fresh uploads, awaiting moderation
- `gs://dragonfly-photos-<env>/observations/` — clean photos, served to clients
- `gs://dragonfly-photos-<env>/quarantine/` — moderation-flagged photos, accessible only to teacher-review pathways

Settings:

- **Uniform bucket-level access** enabled — IAM only, no per-object ACLs.
- **Google-managed encryption** (default). Re-evaluate CMEK at scale.
- **Lifecycle rule** on `pending/`: delete objects older than 24h that never advanced.
- **Lifecycle rule** on `quarantine/`: delete objects older than 90d after `REVIEW#` resolution.

Photo upload uses **V4 signed URLs** issued by the API. This preserves the AWS presigned-PUT pattern in `docs/architecture.md` §"The one important request path" step 1.

### 4. Async work

Three different shapes of async work, three GCP services. Each is chosen for its strongest fit, not for stack uniformity:

| Workload | GCP service | Why |
|---|---|---|
| Photo moderation (GCS object create → moderation) | **Eventarc** | Native GCS object-finalized trigger, fires a Cloud Run service. Mirrors the existing S3-event-driven design 1:1. |
| iNaturalist submission (durable work queue with retries + DLQ) | **Cloud Tasks** | Per-task retry config, exponential backoff, dead-letter target on a separate queue. Handler is a Cloud Run service. |
| Rarity refresh (nightly batch with self-continuation) | **Cloud Scheduler → Cloud Run job** | Cron at 03:00 UTC posts to a Cloud Run job; the job reads/writes a `JOB#rarity` cursor row in Postgres for self-continuation. |

Pub/Sub is not used in Phase 1. It is the right tool for fan-out and streaming; we have neither pattern.

The moderation worker calls a moderation provider (decided in a follow-up ADR — likely **Cloud Vision SafeSearch** as the GCP analogue of Rekognition's `DetectModerationLabels`, but worth a separate evaluation against the moderation thresholds in `docs/moderation.md`).

### 5. Secrets: Secret Manager

All secrets — Firebase service account JSON, iNaturalist project-account credentials, moderation provider API key, third-party API keys (Google Geocoding, etc.) — live in Secret Manager.

Access:

- The Cloud Run service account is granted `roles/secretmanager.secretAccessor` **on specific secrets only**, not at project scope.
- Secrets are loaded at Cloud Run cold start by `pydantic-settings` via the Secret Manager API, never injected as plaintext environment variables.
- `DRAGONFLY_*` env vars on the Cloud Run service hold *secret names*, not secret values.

Rotation is per-secret, manual in Phase 1, scheduled in Phase 2.

### 6. Observability: Cloud Logging + Error Reporting + Cloud Monitoring

- **Cloud Logging** ingests Cloud Run stdout. The existing `structlog` JSON renderer in `backend/app/main.py` requires no code change — Cloud Logging recognizes JSON payloads natively and indexes their fields.
- **Error Reporting** auto-aggregates exceptions from the structured JSON.
- **Cloud Monitoring** holds the alerting policies. The four alarms from `docs/architecture.md` §"Observability" port directly:

| Original (CloudWatch) | GCP equivalent |
|---|---|
| API 5xx rate > 1% | Cloud Run request-count metric filtered by `response_code_class="5xx"` over a 5-min window |
| Moderation DLQ depth > 0 | Cloud Tasks queue-depth metric on the moderation DLQ |
| Rarity job duration > 12 min | Cloud Run job execution-duration metric |
| iNat submit DLQ depth > 0 | Cloud Tasks queue-depth metric on the iNat DLQ |

Dashboards (not alarms) carry the rest of the metrics from the AWS-era observability section.

## Consequences

### Positive

- **One platform.** Firebase Auth, Cloud SQL, GCS, Cloud Run, Cloud Tasks, Eventarc, Cloud Scheduler, Secret Manager, Cloud Logging — all in one project, one billing account, one IAM model. Reduces the cognitive tax that drove the original "single AWS account" preference.
- **Cloud Run is well-suited to the workload.** Stateless FastAPI, scale-to-zero in dev, scale-to-N in prod, HTTPS termination built in, no API Gateway middleman.
- **Postgres unblocks the teacher dashboard.** ADR 0001 explicitly noted "would become the right choice if the app grew ad-hoc query needs (e.g. a teacher dashboard with arbitrary filters)" — that is now in Phase 1's Week 11. We're choosing Postgres before we have to retrofit it.
- **The structlog pipeline carries over verbatim.** Zero code change for the observability path.
- **Familiar mental model.** SQL plus a managed instance is a smaller per-feature cost than single-table DynamoDB design for everything past the simplest access patterns.
- **The compatibility window is small.** Mangum stays in `main.py` per AGENTS.md until the AWS path is intentionally removed; once Cloud Run is hosting prod traffic, that removal is a single-PR cleanup.

### Negative

- **Postgres is not scale-to-zero.** A `db-g1-small` runs ~$25/month even idle in dev. Accepted: it buys joins, transactions, and familiarity. ADR 0001's cost argument was strongest at $0; at $25/month it's a non-issue against the time saved on every subsequent feature.
- **Connection management matters again.** Cloud Run instances open Postgres connections; a flapping autoscale event can exhaust the pool. Mitigation: use the Cloud SQL Python connector with `pg8000` or `asyncpg`, set `min_instances=1` on the Cloud Run service in prod, configure connection limits to leave headroom.
- **Three async services instead of one (SQS).** Eventarc + Cloud Tasks + Cloud Scheduler is more surface area than "everything is a queue." Justified by each service being the strongest fit; revisit if operational complexity bites.
- **Moderation provider is a separate decision.** This ADR does not pick Cloud Vision SafeSearch over a third-party provider — that needs its own evaluation against `docs/moderation.md` thresholds.
- **No DynamoDB Local equivalent fits as cleanly.** Postgres for local dev is fine (Docker container), but the "API parity = laptop parity" property of DynamoDB Local doesn't have a perfect GCP analogue. Cloud SQL Auth Proxy works for connecting to a real dev instance; the local Docker Postgres is the offline option.
- **Existing CDK in `infra/` becomes dead weight.** `infra/stacks/` stays in the repo per AGENTS.md compatibility rule until Cloud Run is in prod, then removed in a single PR. CI workflow `deploy-dev.yml` will need a GCP sibling and eventual replacement.

### Neutral

- **Backups.** Cloud SQL automated backups + 7-day point-in-time recovery in dev, 35-day in prod. Equivalent to DynamoDB PITR; different control surface.
- **IAM.** GCP IAM is per-resource at the binding level; AWS IAM is policy-based. Different conceptual model, equivalent expressive power for our needs.

## Migration order

The phases below replace the AWS-flavored Week 1–12 sequencing in `docs/roadmap.md` Phase 0 onward. The week numbers stay the same; the service names change.

1. **Cloud Run + Cloud SQL skeleton** — `/health` deploys to Cloud Run, `app.main:app` connects to a dev Cloud SQL instance via the connector, returns 200. (Week 1–2)
2. **Firebase Auth wiring** — JWT verification dependency in `app/core/auth.py`, parent/group/kid signup endpoints. (Week 3)
3. **GCS upload + observation insert** — V4 signed URL endpoint, `POST /v1/observations` writes to Postgres. (Week 4)
4. **iNat CV + geocoding** — unchanged from the existing roadmap; third-party integrations are platform-neutral. (Week 5)
5. **Eventarc moderation worker** — Cloud Run service triggered on GCS object-finalized for `pending/`. (Week 6)
6. **Cloud Tasks iNat submit worker + rarity cache scaffold** — Cloud Run service as queue handler, DLQ on the queue. (Week 7)
7. **Dispatcher + handlers** — unchanged; dispatcher is platform-agnostic. (Weeks 8–9)
8. **Rarity refresh as Cloud Run job + Cloud Scheduler** — replaces the EventBridge cron design. (Week 9)
9. **Content authoring + sync to Postgres** — `scripts/sync_expeditions.py` writes to Postgres instead of DynamoDB. (Week 10)
10. **Teacher review UI + closed beta polish** — unchanged scope, GCP services throughout. (Weeks 11–12)

`main` stays recoverable on the AWS path until Week 6 at the earliest; remove the CDK stacks and Mangum handler in a single dedicated PR after Cloud Run is serving prod traffic.

## Alternatives considered

### Stay on AWS

Rejected. The pivot is already in flight (AGENTS.md §"Current Direction"). Re-litigating the platform choice every ADR cycle is itself the cost we are avoiding by writing this one.

### Lift-and-shift onto GCP equivalents per service

Considered. Cloud Functions could replace Lambda, Firestore could replace DynamoDB, etc. Rejected because the lift-and-shift preserves AWS's serverless-everywhere bias on a platform where Cloud Run + Cloud SQL is the more idiomatic and operationally simpler shape.

### Cloud Run + Firestore (preserve single-table-style design on GCP)

Considered. Firestore's document model could host the `PK/SK` shape from ADR 0001, and it is genuinely scale-to-zero. Rejected because (a) the access-pattern argument that justified single-table on DynamoDB does not justify it on Firestore — Firestore's strengths (real-time listeners, mobile SDKs) are not used by an observation-write API, (b) the teacher dashboard surfaces ad-hoc query needs that Firestore handles poorly, (c) one operational data store choice, not two.

## Follow-ups

- Rewrite `docs/data-model.md` under the Postgres schema. Map every `PK#SK` partition to a table with explicit foreign keys. Preserve the access patterns; restate them as SQL queries.
- Write `docs/runbook.md` entries for the GCP services: Cloud Run rollback, Cloud SQL failover, Cloud Tasks DLQ replay, Eventarc trigger reset.
- Decide on the moderation provider in a separate ADR (Cloud Vision SafeSearch vs. third-party). Block Week 6 work until that lands.
- Add a `infra-gcp/` sibling to `infra/` (Terraform or `gcloud` scripts — to be decided). Remove `infra/` after Cloud Run is in prod.
- Set Cloud SQL maintenance windows to off-hours for the target beta region.
- Configure billing budgets and budget alerts on the GCP project before any worker that calls a paid third-party API ships.
- Revisit this ADR at 5k DAU or on any sustained month where total GCP spend exceeds $300 — either triggers a real cost/benefit recomputation.
