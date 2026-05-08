# AGENTS.md

Dragonfly is a citizen-science field app for kids ages 9-12. Kids log real observations, fill a personal Dex, complete expeditions, and contribute to iNaturalist through an app-owned project account.

This file is for coding agents working in this repository. It is both a project plan and a set of guardrails. Read it before making changes.

## Read First

Start with these files, in this order:

1. `README.md` for repo intent and current phase.
2. `docs/architecture.md` for system shape and invariants.
3. `docs/roadmap.md` for the Phase 1 execution plan and exit criteria.
4. `docs/data-model.md` for Postgres tables and access patterns.
5. `docs/ingest.md` for replayable data-pipeline rules.
6. `docs/dispatcher.md` for reward handling.
7. `docs/mobile.md` for Expo/mobile constraints.
8. `docs/adr/` for decisions that must not be casually reversed. Most active:
   - [ADR 0005](docs/adr/0005-gcp-target-architecture.md) — GCP target architecture (Firebase Auth, Cloud SQL Postgres, Cloud Storage, Eventarc + Cloud Tasks + Cloud Scheduler, Secret Manager, Cloud Logging). Supersedes ADR 0001.
   - [ADR 0006](docs/adr/0006-ingest-pipelines.md) — Ingest pipelines are explicit, replayable, and audited via `ingest_runs`.
   - [ADR 0007](docs/adr/0007-internal-ai-agent-tooling.md) — Multi-agent AI is internal/adult-only; never on a kid-facing request path.
   - [ADR 0008](docs/adr/0008-public-cloud-run-with-firebase-enforcement.md) — Override `iam.allowedPolicyMemberDomains` on dev project so `allUsers` can invoke Cloud Run; Firebase ID token verification becomes the only auth boundary.

If code and docs disagree, stop and reconcile them in the same PR. Do not let architecture drift silently.

## Current Direction

The original docs describe an AWS serverless stack: API Gateway, Lambda, Cognito, DynamoDB, S3, SQS, and CDK.

The active migration direction is GCP/Cloud Run for the API runtime. The GCP target architecture is documented in [ADR 0005](docs/adr/0005-gcp-target-architecture.md) — Firebase Auth, Cloud SQL Postgres (supersedes ADR 0001), Cloud Storage, Eventarc + Cloud Tasks + Cloud Scheduler for async work, Secret Manager, Cloud Logging/Error Reporting/Monitoring. Keep product and data invariants from the AWS-era docs, but avoid adding new AWS-only implementation unless the task explicitly requires it.

When migrating platform pieces, prefer compatibility layers and small reversible changes:

- Keep `app.main:app` runnable by uvicorn.
- Keep the `Mangum` handler until the AWS path is intentionally removed.
- Keep environment-driven settings under the `DRAGONFLY_` prefix unless a migration ADR says otherwise.
- Do not rewrite product logic just to move infrastructure.

## Non-Negotiable Invariants

Preserve these through every phase:

- The observation submission endpoint shape should stay stable. Add handlers; do not keep expanding the endpoint with feature-specific branches.
- First-find detection must use an atomic conditional write. Do not implement read-then-write first-find checks.
- Kid-facing runtime LLM calls are forbidden. LLMs may be used for author-time tools or adult-facing reviewed summaries only.
- The kid experience must not depend on iNaturalist, Google/Maps, moderation, or rarity refresh being available at the moment of submission.
- Moderation is asynchronous. Do not block the hot path on image moderation.
- iNaturalist submission is asynchronous. A kid can see success before iNat receives the observation.
- Expedition JSON/content is source of truth. The database is a materialized view.
- Leaderboard counters live on membership rows. Do not aggregate observations at read time for normal leaderboard reads.
- No ads, marketing pushes, public chat, DMs, or kid-to-kid free text in Phase 1.
- Ingest jobs must be idempotent, replayable, and auditable.
- CrewAI or multi-agent tooling is internal/adult-only. Do not import it from `backend/app` or put it on a kid-facing request path.

## Working Rules

- Use feature branches. Keep `main` in a deployable or at least known-good state.
- Keep PRs small and vertical: one milestone, one migration slice, or one handler at a time.
- Do not modify unrelated files.
- Add or update tests when changing behavior.
- Update docs in the same PR as behavior or architecture changes.
- Add an ADR for changes to data access patterns, platform direction, privacy/safety policy, auth model, or kid-facing runtime AI.
- Never commit secrets, local `.env` files, service-account keys, or real child data.
- Prefer structured parsers and typed models over ad hoc string handling.

## Repository Reality Check

Some directories in `README.md` are planned and may not exist yet. Do not assume `mobile/`, `lambdas/`, `content/`, or `scripts/` exist until you see them locally.

Current backend baseline:

- FastAPI app in `backend/app/main.py`.
- `/health` endpoint is the first deployment smoke test.
- `Mangum` handler remains for AWS compatibility.
- Cloud Run runtime should use uvicorn against `app.main:app`.
- Postgres foundation lives under `backend/app/db/` with Alembic migrations.
- Ingest contracts live under `backend/app/ingest/`.

Migration scaffolding present:

- `infra/` — legacy AWS CDK stacks. To be removed after Cloud Run is in prod (per ADR 0005).
- `infra-gcp/` — Terraform root for Cloud Run, Cloud SQL, GCS, Artifact Registry, IAM, Workload Identity Federation, monitoring, and DNS.
- `infra-gcp/dns/main.tf` — Terraform stub for the Cloud DNS zone `dragonfly-app-zone`. Import path documented in the file.

## End-to-End Plan

### 0. Repo Integrity

Goal: a clean clone can install, test, and run the documented smoke path.

Deliverables:

- Make `README.md` match what exists today versus what is planned.
- Ensure backend dependency install works from a clean clone.
- Add missing infra or remove stale install/deploy references.
- Ensure CI runs lint, typecheck, tests, and any content validation only when supporting files exist.

Exit criteria:

- `make install` succeeds or the README gives a correct replacement command.
- `make test` or the documented test command passes.
- `/health` runs locally.

**Status:** ✅ Met 2026-05-05. README split into exists-vs-planned sections; backend builds and `/health` runs locally and on Cloud Run.

### 1. GCP Platform Foundation

Goal: deploy the existing FastAPI health endpoint to Cloud Run with minimal product change.

Deliverables:

- `backend/Dockerfile` and `backend/.dockerignore`.
- Cloud Run service `dragonfly-api` in project `dragonflyapp-495423`.
- Artifact Registry and Cloud Build enabled.
- `DRAGONFLY_ENV=dev` configured on the service.
- Document manual deploy commands first; automate later.

Exit criteria:

- Cloud Run URL returns HTTP 200 from `/health`.
- Local Docker container returns the same health response.
- `main` remains recoverable if the migration branch is abandoned.

**Status:** ✅ Met 2026-05-05. Cloud Run service `dragonfly-api` deployed in `us-central1`; `/health` returns 200 with an identity token. The `--allow-unauthenticated` flag is silently rejected by the `dragonfly-app.net` Workspace org policy `iam.allowedPolicyMemberDomains` — every endpoint requires a Google identity until Firebase Auth lands in Phase 4. Custom domain `api.dragonfly-app.net` mapped via Cloud DNS; cert provisioning in flight at last check. See `docs/runbook.md` for the smoke-test command and DNS zone reference.

### 2. GCP Architecture Decision

Goal: decide and document the GCP equivalents before porting feature code.

Decisions needed:

- Auth: Firebase Auth, Identity Platform, Auth0, or another provider.
- Data store: Firestore, Cloud SQL, AlloyDB, or keep DynamoDB during transition.
- Object storage: Cloud Storage bucket layout for `pending/`, `observations/`, and `quarantine/`.
- Queues/workers: Cloud Tasks, Pub/Sub, Cloud Run jobs, or Eventarc.
- Secrets: Secret Manager.
- Observability: Cloud Logging, Error Reporting, Monitoring alerts.

Exit criteria:

- New ADR documents the GCP target architecture and migration order.
- Each AWS-era invariant has an equivalent GCP implementation path.

**Status:** ✅ Met 2026-05-05. [ADR 0005](docs/adr/0005-gcp-target-architecture.md) selects Firebase Auth, Cloud SQL Postgres, Cloud Storage, Eventarc + Cloud Tasks + Cloud Scheduler, Secret Manager, and Cloud Logging/Error Reporting/Monitoring. ADR 0001 (single-table DynamoDB) superseded. Open follow-ups: moderation provider ADR (gates Phase 8), Postgres rewrite of `docs/data-model.md`, infra-gcp/ Terraform-vs-scripts decision.

### 3. API Foundation

Goal: build the backend spine before product features.

Deliverables:

- Settings module with typed environment config.
- Structured logging for request and observation flows.
- API version prefix under `/v1`.
- Error response conventions.
- Health and readiness endpoints.
- Local dev setup for chosen datastore and storage emulator if available.

Exit criteria:

- Backend starts locally and in Cloud Run.
- Tests cover settings, health, and error response shape.

**Status:** ✅ Met 2026-05-07. Backend deployed to Cloud Run (revision `dragonfly-api-00007-b8c`); `/health`, `/ready`, and `/v1/meta` all return 200 against the live service. Typed settings, structured request logging, error envelope, Postgres model + initial Alembic migration, ingest contracts under `backend/app/ingest/`, and the `infra-gcp/` Terraform foundation all landed via PRs #1–8. Custom domain `api.dragonfly-app.net` live with a managed cert. Cloud Run-dev auto-deploy workflow is wired up via Workload Identity Federation.

### 4. Auth, Groups, and Roles

Goal: parent/teacher/kid accounts can be created and joined to invite-only groups.

Deliverables:

- Parent/teacher signup or invite flow.
- Group create endpoint with 6-character join code.
- Kid account provisioning flow.
- JWT/session validation dependency.
- `/v1/me` endpoint.
- Postman or scripted smoke collection.

Exit criteria:

- A parent can create a group, create a kid, sign in as the kid, and call `/v1/me`.

**Status:** Endpoint surface complete 2026-05-08. Foundation: PR #8 landed Firebase Admin SDK + ID-token verification + `GET /v1/me`; Firebase project configured on `dragonflyapp-495423` with Email/Password sign-in and a Web app registered; [ADR 0008](docs/adr/0008-public-cloud-run-with-firebase-enforcement.md) decided dev Cloud Run is publicly callable with Firebase ID-token verification as the only auth boundary. All four Phase 4 endpoints implemented:

- `POST /v1/auth/parent-signup` (PR #13) — idempotent `users` upsert keyed by `firebase_uid`, sets the Firebase custom claim `role=parent`.
- `POST /v1/groups` (PR #14) — group create with a Crockford-base32 6-char join code (no I/L/O/U), CSPRNG-generated, collision-checked with retry. Atomic Group + owner Membership insert.
- `POST /v1/groups/{group_id}/kids` (PR #15) — admin-create a kid via Firebase Admin SDK (no email), set custom claims `{role: 'kid', group_id, parent_user_id}`, insert User + Membership, mint a Firebase custom token. Best-effort Firebase cleanup on partial-create failure.
- `POST /v1/groups/join` (PR #16) — idempotent join-code redemption; lowercase normalized at the boundary.

Authorization on all four routes gates on canonical `users.role` from Postgres rather than the Firebase ID-token claim, so a parent who just signed up doesn't have to refresh their token before creating a group. The custom claim is a convenience cache of the same fact.

Tests: every endpoint covers 401/4xx/5xx/happy-path with `AsyncMock(spec=AsyncSession)` for the DB and module-level monkeypatches for the Firebase Admin wrappers.

Remaining for Phase 4 ✅ Met:

1. End-to-end smoke test against deployed dev: parent signs up via Firebase Web SDK → creates group → admin-creates kid → kid signs in via the custom token → kid calls `/v1/me`. Single happy-path run from a real device or curl is enough.
2. `docs/postman/` (or `scripts/smoke_phase4.py`) checked in as the reproducible smoke collection.

### 5. Mobile Phase 0

Goal: prove the real mobile client can talk to the deployed API.

Deliverables:

- Expo app scaffold.
- Environment-switched API base URL.
- First screen fetches `/health`.
- Basic navigation structure for auth, observation, Dex, expeditions, and settings.

Exit criteria:

- Physical iOS or Android device displays the Cloud Run `/health` response.

### 6. Observation Happy Path

Goal: a kid can submit an observation and see it in their list.

Deliverables:

- Presigned or signed upload URL endpoint for photo upload.
- Object storage prefixes: `pending/`, `observations/`, `quarantine/`.
- Observation create endpoint.
- Observation list endpoint, newest first.
- Membership counter update with the observation write.
- Mobile camera/photo picker, upload, submit, and list UI.

Exit criteria:

- Kid account on mobile can upload a photo and see the observation in "my observations."

### 7. External Integrations

Goal: enrich observations while preserving graceful degradation.

Deliverables:

- iNaturalist CV suggestions with fallback to manual/free-text selection.
- Reverse geocoding cache.
- Nearby places cache for onboarding.
- Species cache from iNaturalist taxa.
- Integration tests with mocked third-party APIs.

Exit criteria:

- 50 kid-style test observations achieve top-3 iNat CV correctness target or a risk is filed.
- Third-party outage paths are tested.

### 8. Async Workers

Goal: move slow or failure-prone work off the hot path.

Deliverables:

- Moderation worker: `pending/` to `observations/` or `quarantine/`.
- Review queue rows for flagged photos.
- iNat submit worker with retry and DLQ/dead-letter handling.
- Rarity refresh job with cursor/state row.
- Runbook updates for failure recovery.

Exit criteria:

- Submitted observation appears in iNaturalist within target window.
- Flagged test photo is quarantined and reviewable.
- Worker failures do not break observation submission.

### 9. Dispatcher and Rewards

Goal: make the reward system additive and testable.

Deliverables:

- `Reward`, `Context`, `HandlerResult`, and handler protocol/types.
- Dispatcher core with exception isolation.
- Handler registry.
- `DexHandler`, then `ExpeditionHandler`, then `RarityHandler`.
- Moto/emulator-backed test harness or a provider-equivalent test strategy.
- Snapshot scenarios from `docs/dispatcher.md`.

Exit criteria:

- All dispatcher snapshot scenarios pass.
- Dispatcher p95 meets the documented budget.

### 10. Content and Expeditions

Goal: make expedition authoring sustainable.

Deliverables:

- Expedition Pydantic model and JSON schema.
- `content/expeditions/` source tree.
- `scripts/validate_content.py`.
- `scripts/sync_expeditions.py`.
- Author-time `draft_expedition.py` tool, with no kid-facing runtime LLM path.
- Five starter expeditions.

Exit criteria:

- Starter expeditions are visible in the app and can complete through the dispatcher.

### 11. Teacher Review and Beta Polish

Goal: closed beta can operate safely with real groups.

Deliverables:

- Teacher review list and approve/reject actions.
- Cleanup behavior for rejected or stale quarantined observations.
- Replay script for missed dispatcher runs.
- Alarms for API errors, worker DLQs, missed Dex rows, and budget anomalies.
- Dogfood dashboard.
- Privacy policy and app-store compliance checklist.

Exit criteria:

- First closed-beta group is invited.
- At least one real kid submits at least one real outdoor observation.

### 12. Phase 2

Goal: deepen engagement without disrupting Phase 1 loops.

Candidates:

- Territory map using MapLibre/OSM rendering.
- Sanctuary/world layer with `WORLD#` rows and `WorldHandler`.
- Offline tile bundles.
- Push notifications, transactional only.
- Teacher dashboard beyond review queue.

Rule:

- Phase 2 features must plug into existing handlers, data patterns, or new ADR-approved prefixes. Do not rewrite the submission spine.

### 13. Phase 3

Goal: add seasonality and older-kid account ownership.

Candidates:

- USA-NPN phenology sync.
- Season rewards via `SeasonHandler`.
- Kid iNaturalist account claim flow for users 13+.
- Richer species blurbs from reviewed author-time generation.

### 14. Phase 4

Goal: add long-term goals and social depth safely.

Candidates:

- Missions via `MissionHandler`.
- Friend/group challenges without free text.
- Optional friend Sanctuary viewing if privacy model is approved.
- Offline-first refinements based on beta telemetry.

## Definition of Done

For any feature:

- Tests pass.
- New behavior has focused tests.
- Docs are updated.
- Logs expose enough context to debug failures.
- Failure mode is known and graceful.
- No kid-facing privacy, safety, or LLM invariant is weakened.

For any platform migration:

- Old and new runtime paths are clearly documented.
- Rollback path exists.
- Secrets are managed outside git.
- Smoke test is documented and run.
- Cost and alerting impact is understood.
