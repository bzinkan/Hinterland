# AGENTS.md

Hinterland (formerly Dragonfly; full title "The Hinterland Guide", short form "Hinterland" everywhere user-facing) is a citizen-science field app for curious explorers of all ages. People log real observations, fill a personal Dex, complete expeditions, and may eventually contribute approved observations to iNaturalist through a reviewed contribution flow. Kid accounts remain adult-managed. Layer-2 identifiers (bundle ids, `DRAGONFLY_*` env vars, deep-link scheme, Azure resource names, the `dragonfly-app.net` domain) deliberately keep the old name — see `docs/adr/0013-hinterland-rename.md` before renaming anything.

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
   - [ADR 0010](docs/adr/0010-azure-target-architecture.md) — Azure target architecture (Entra External Identities, Hinterland-signed kid JWTs, Azure Postgres, Blob Storage, Container Apps, Key Vault, Azure AI Content Safety, Azure Monitor). Supersedes ADR 0005, 0008, and 0009.
   - [ADR 0006](docs/adr/0006-ingest-pipelines.md) — Ingest pipelines are explicit, replayable, and audited via `ingest_runs`.
   - [ADR 0007](docs/adr/0007-internal-ai-agent-tooling.md) — Multi-agent AI is internal/adult-only; never on a kid-facing request path.
   - [ADR 0002](docs/adr/0002-no-runtime-llm.md) — Kid-facing runtime LLM calls remain forbidden.

If code and docs disagree, stop and reconcile them in the same PR. Do not let architecture drift silently.

## Current Direction

The original docs describe an AWS serverless stack; the next wave of docs described a GCP/Cloud Run runtime. Both are now historical for new implementation work.

The active direction is Azure, documented in [ADR 0010](docs/adr/0010-azure-target-architecture.md): Microsoft Entra External Identities for adults, Hinterland-signed RS256 JWTs for kids, Azure Database for PostgreSQL Flexible Server, Azure Blob Storage, Azure Container Apps, Azure Key Vault, Azure AI Content Safety, and Azure Monitor/Application Insights/Log Analytics. Keep product and data invariants from the AWS/GCP-era docs, but do not add new AWS-only or GCP-only implementation unless a new ADR explicitly reopens the platform decision.

When migrating platform pieces, prefer compatibility layers and small reversible changes:

- Keep `app.main:app` runnable by uvicorn.
- Keep the `Mangum` handler until the AWS path is intentionally removed.
- Keep environment-driven settings under the `DRAGONFLY_` prefix unless a migration ADR says otherwise.
- Do not rewrite product logic just to move infrastructure.
- Production API auth should not accept Firebase ID tokens. Firebase remains only where ADR 0010 explicitly says residual hosting/auth rollback is retained.

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
- No ads, marketing pushes, public chat, DMs, or kid-to-kid free text in Phase 1. The Field Journal (the observation list tab) is read-only for kids: adding kid-authored journal notes requires an ADR first.
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

`mobile/`, `content/`, and `scripts/` exist. Old notes that list them as planned are stale and should be corrected when touched.

Kid-facing naming (2026-07-04): the observation list tab is the **Field
Journal** (tab label "Journal", route `mobile/app/(tabs)/index.tsx`;
logic in `mobile/src/observation/journalLogic.ts`). The Sanctuary's
event-timeline panel is labeled **"Story"** in the UI — its API field
stays `journal[]` (wire contract; do not rename). Historical phase logs
below still say "Home tab" / "gallery"; leave them as history.

Current backend baseline:

- FastAPI app in `backend/app/main.py`.
- `/health` endpoint is the first deployment smoke test.
- `Mangum` handler remains for AWS compatibility.
- Azure Container Apps runtime should use uvicorn against `app.main:app`.
- Postgres foundation lives under `backend/app/db/` with Alembic migrations.
- Ingest contracts live under `backend/app/ingest/`.

Migration scaffolding present:

- `infra-azure/` — Azure setup/decommission scripts and the current manifest.
- `infra-gcp/` — legacy GCP Terraform and residual DNS/Firebase reference only.
- `infra/` — legacy AWS CDK stacks, reference only.

## Immediate Risk Closure Priority

As of 2026-06-04, code is closed-beta-complete but pilot/beta risk closure is active:

1. Keep Azure as the source of truth; disable stale Cloud Run deploy paths.
2. Keep native Android parent setup web-first through Entra/MSAL and use the kid QR handoff flow for kids.
3. Use coarse-only location for the `play-internal` Android build.
4. Keep iNat submission and real moderation off for W1 Internal Testing; wire Azure Content Safety + async queues before closed beta.
5. Close legal/human blockers before any public or closed-store release: reviewed privacy/terms, support/privacy email, account deletion follow-up, iNat OAuth/project account, and first-family onboarding.

## End-to-End Plan

The dated phase notes below are a historical execution log. Use the current
direction, repository reality check, and immediate risk closure priority above
for active implementation choices.

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

**Status:** ✅ Met 2026-05-05. [ADR 0005](docs/adr/0005-gcp-target-architecture.md) selects Firebase Auth, Cloud SQL Postgres, Cloud Storage, Eventarc + Cloud Tasks + Cloud Scheduler, Secret Manager, and Cloud Logging/Error Reporting/Monitoring. ADR 0001 (single-table DynamoDB) superseded. Moderation provider settled in [ADR 0009](docs/adr/0009-moderation-provider-cloud-vision-safesearch.md) (Cloud Vision SafeSearch). Open follow-ups: Postgres rewrite of `docs/data-model.md`, infra-gcp/ Terraform-vs-scripts decision.

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

**Status:** ✅ Met 2026-05-09. Foundation: PR #8 landed Firebase Admin SDK + ID-token verification + `GET /v1/me`; Firebase project configured on `dragonflyapp-495423` with Email/Password sign-in and a Web app registered; [ADR 0008](docs/adr/0008-public-cloud-run-with-firebase-enforcement.md) decided dev Cloud Run is publicly callable with Firebase ID-token verification as the only auth boundary. All four Phase 4 endpoints implemented:

- `POST /v1/auth/parent-signup` (PR #13) — idempotent `users` upsert keyed by `firebase_uid`, sets the Firebase custom claim `role=parent`.
- `POST /v1/groups` (PR #14) — group create with a Crockford-base32 6-char join code (no I/L/O/U), CSPRNG-generated, collision-checked with retry. Atomic Group + owner Membership insert.
- `POST /v1/groups/{group_id}/kids` (PR #15) — admin-create a kid via Firebase Admin SDK (no email), set custom claims `{role, group_id, parent_id}`, insert User + Membership, mint a Firebase custom token. Best-effort Firebase cleanup on partial-create failure.
- `POST /v1/groups/join` (PR #16) — idempotent join-code redemption; lowercase normalized at the boundary.

Authorization on all four routes gates on canonical `users.role` from Postgres rather than the Firebase ID-token claim, so a parent who just signed up doesn't have to refresh their token before creating a group. The custom claim is a convenience cache of the same fact.

Tests: every endpoint covers 401/4xx/5xx/happy-path with `AsyncMock(spec=AsyncSession)` for the DB and module-level monkeypatches for the Firebase Admin wrappers (PR #15 also has a partial-create-cleanup test).

End-to-end smoke (`scripts/smoke_phase4.py`, PR #18) runs all 7 round-trip steps against the deployed dev service: Firebase signUp → parent-signup → token refresh → groups create → kids create → custom-token sign-in → kid `/v1/me`. **All 7 green as of 2026-05-09 against revision `dragonfly-api-00014-*` backed by Cloud SQL `dragonfly-postgres-dev` (db-g1-small, ENTERPRISE edition).**

Out-of-band IAM grants needed (codify in Terraform as a follow-up):

- `roles/firebaseauth.admin` on the runtime SA `dragonfly-api-dev@…` at project scope — Admin SDK `set_custom_user_claims` and `create_user` need it.
- `roles/iam.serviceAccountTokenCreator` on the runtime SA *to itself* — `create_custom_token` calls `iam:signBlob` against the same SA.
- `DRAGONFLY_FIREBASE_CHECK_REVOKED=false` env var on Cloud Run — temporary; flip back to `true` once the runtime SA also gets `firebaseauth.users.get` (ironically already in `firebaseauth.admin`, so this can be removed in the same follow-up).

### 5. Mobile Phase 0

Goal: prove the real mobile client can talk to the deployed API.

Deliverables:

- Expo app scaffold.
- Environment-switched API base URL.
- First screen fetches `/health`.
- Basic navigation structure for auth, observation, Dex, expeditions, and settings.

Exit criteria:

- Physical iOS or Android device displays the Cloud Run `/health` response.

**Status:** ✅ Met 2026-05-09. New top-level `mobile/` Expo app (SDK 54, Expo Router 6, React 19, RN 0.81, TypeScript strict) landed in PR #28. `app.config.ts` switches API base URL, bundle id (`com.dragonfly.app[.dev|.staging]`), and Expo Update channel by `APP_ENV`. Five-tab nav: Home (live `/health` fetch with status pill), Observe / Dex / Expeditions (placeholders), Settings (shows active env + API URL). `eas.json` carries the three build profiles per `docs/mobile.md`. Verified end-to-end: physical Android device (SM-G998U via Expo Go on `192.168.1.x` LAN) shows `● ok` with `env: dev · version: 0.1.0` from `https://api.dragonfly-app.net`. Intentionally bare — Nativewind, Zustand, TanStack Query, Sentry, expo-camera/-location/-image-manipulator/-sqlite, the offline queue, the celebration sequence, and EAS Update wiring each land with the phase that needs them (Phases 6–11).

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

**Status:** ✅ Met 2026-05-09. Backend trio shipped end-to-end: `POST /v1/photos/presign` (PR #30) issues V4 signed PUT URLs via IAM signBlob (no private key needed); `POST /v1/observations` (PR #31) inserts the row and atomically bumps `memberships.observation_count` via `UPDATE … RETURNING id` — preserves the no-read-then-write invariant; `GET /v1/observations/me` (PR #32) is cursor-paginated newest-first, joining `photos` for status. Mobile: API client + bearer-token store + TanStack Query (PR #33), camera capture + image-manipulator resize to 1600px/0.8 JPEG + Zustand draft store (PR #34), submit screen running presign → PUT → create with per-step status (PR #35), Home tab listing my observations with pull-to-refresh and cursor-paginated "Load more" (PR #36). GCS `pending/` prefix uses the existing `dragonfly-photos-dev-…` bucket and its 24h lifecycle rule. `observations/` and `quarantine/` prefixes are reserved for the moderation worker (Phase 8). Photo bytes themselves still need a signed-GET endpoint to render in the list — Phase 7 follow-up. Real Firebase sign-in replaces the dev "paste an ID token" Settings shortcut also in Phase 7+.

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

**Status:** ✅ Met 2026-05-10. Backend: iNat httpx wrapper + species_cache read-through (PR #38), `POST /v1/observations/{id}/identify` calling iNat CV server-side with `cv_unavailable=true` graceful fallback (PR #39), `PATCH /v1/observations/{id}` letting the kid pick a suggestion or type manual + auto-fill species_name from cache (PR #40), reverse-geocoding `Geocoder` provider abstraction (NoOp default, Nominatim available) + `geo_cache` read-through + `GET /v1/geocode/reverse` (PR #41). Mobile: submit screen extended into a state machine with picker UI -- presign → put → create → identify → picking → patch → done -- with parallel reverseGeocode call folded into the eventual PATCH (PR #42). **Outage paths covered**: every external call (iNat CV / iNat taxa / Nominatim) has unit tests using `respx` for 5xx, 401/403, transport error, and the empty/4xx degradation path. **The "50 kid-style observations" correctness target is filed as [risk 0001](docs/risks/0001-inat-cv-correctness-target-unverified.md)** -- needs a real iNat OAuth token (manual project signup) and a labeled benchmark dataset before it can be measured. Nearby-places-for-onboarding cache deferred to onboarding work (Phase 11). Production unblock checklist lives in the risk doc.

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

**Status:** ✅ Met 2026-05-10 (code complete; production wiring captured as a risk). Moderation: `Moderator` provider abstraction with `NoOpModerator` (dev) + `CloudVisionSafeSearchModerator` per ADR 0009 thresholds, `process_pending_photo()` doing the GCS move + DB updates + review_queue insert, `POST /internal/moderation/process` (PR #44). Review queue: `GET /v1/review-queue` + `POST .../approve` (move quarantine → observations + flip photo to clean) + `POST .../reject` (mark photo deleted + atomic counter decrement) (PR #45). iNat submit: `submit_observation_to_inat()` doing iNat's two-call dance with the Dragonfly observation id as the iNat uuid for Cloud Tasks idempotency, `POST /internal/inat/submit` (PR #46). Rarity refresh: tier-by-share computation, `geohash_bbox()`, `discover_active_regions()`, `refresh_region()` with `low_data` short-circuit, `run_refresh()` with per-region failure isolation, `admin/rarity_refresh.py` Cloud Run Job entry point (PR #47). All four exit paths (worker failure → kid still submits, third-party 5xx, transport error, 4xx other) covered by `respx`-mocked unit tests; the AGENTS.md "worker failures do not break observation submission" criterion is structurally guaranteed -- workers run after the kid's response is already returned. **Production wiring** (Eventarc trigger, Cloud Tasks queue + DLQ, Cloud Scheduler cron, OIDC verification on `/internal/*`, Vision API enablement, observations.moderation_status migration) is filed as [risk 0002](docs/risks/0002-async-workers-production-unwired.md) with an ordered unblock checklist. The "submitted observation appears in iNaturalist within target window" exit criterion is gated on the iNat OAuth token blocker shared with [risk 0001](docs/risks/0001-inat-cv-correctness-target-unverified.md).

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

**Status:** ✅ Met 2026-05-10 (code complete; full snapshot coverage + p95 measurement filed as a risk). Types: `Reward` (frozen), `Context` (mutable, populated once), `HandlerResult`, `Handler` Protocol -- exactly the shapes spec'd by `docs/dispatcher.md` (PR #49). Core: `dispatch()` with per-handler exception isolation + sort-by-weight desc with stable registration-order tie-break. Failed handlers still get an empty `HandlerResult` recorded so downstream presence checks don't `KeyError` (PR #49). Handlers: `DexHandler` (atomic `INSERT ... ON CONFLICT RETURNING` for first-find detection + `dex_count` counter bump + first_find/repeat_find rewards, PR #49), `RarityHandler` (reads `rarity_cache` from PR #47, emits `rarity_tier` weighted by tier + `unrecorded` for region-known/species-missing, owns `rarest_tier` counter via SQL CASE-rank comparison, PR #50), `ExpeditionHandler` stub (full impl is Phase 10, PR #51). Wiring: `dispatch()` runs in `POST /v1/observations` after the row is committed; failure caught + logged + observation still returned with empty rewards (`docs/dispatcher.md` "a failed handler never fails the submission" extended to dispatcher infra, PR #51). Snapshot scenarios 1, 2, 3, 5, 9 from the docs table validated in `tests/test_dispatcher_snapshots.py` (PR #52). Scenarios 4 (geohash-3 low_data fallback), 6/7/8 (require ExpeditionHandler full impl from Phase 10), 10/11 (require real-Postgres test harness from Phase 11) and the p95 budget measurement are filed as [risk 0003](docs/risks/0003-dispatcher-snapshots-and-perf-not-fully-validated.md) with an unblock checklist.

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

**Status:** ✅ Met 2026-05-10. Pydantic schema for Expedition + Step + MatchSpec + Prerequisite (PR #54), with snake_case + duplicate-step-id + tier-range validators and a discriminated-union resolution for every match kind. Matcher registry + per-kind functions for iconic_taxon / taxon_id / any_organism / not_in_dex / not_within_radius (equirectangular distance approximation) + all_of / any_of combinators (PR #54). `scripts/validate_content.py` walks `content/expeditions/`, validates each file against the Pydantic model, enforces filename-stem-equals-id; CI workflow `.github/workflows/content-validate.yml` runs it on every PR (PR #54). Five starter expeditions under `content/expeditions/starters/` (PR #55) covering every match kind: backyard_starter (canonical), park_starter (any_of + not_in_dex), street_starter (iconic_taxon × 3), school_starter (not_within_radius), anywhere_starter (not_in_dex × 3). `scripts/sync_expeditions.py` validates → SHA-256 hash → `INSERT ... ON CONFLICT DO UPDATE` only on hash drift, never deletes (PR #55). `scripts/regenerate_schema.py` emits `content/schema/expedition.schema.json` from the Pydantic model; CI's `git diff --exit-code` catches any drift between code and committed schema (PR #54). ExpeditionHandler full impl replaces the Phase 9 stub: walks active progress rows, builds MatcherInputs once (TaxonInfo from species_cache, dex set EXCLUDING the row DexHandler just inserted, prior obs lat/lng), advances first-incomplete-step on match, emits expedition_step + expedition_complete rewards (PR #56). `GET /v1/expeditions/available` (prerequisites met + not yet started) + `GET /v1/expeditions/me` (in-progress + completed with step counts) + `POST /v1/expeditions/{id}/start` (creates empty progress row, 409 on prereq fail or already started); mobile Expeditions tab consumes them with a TanStack picker UI (PR #57). The exit criterion is met from the code's perspective: starter expeditions are listable, startable, and progressable end-to-end. **Deferred:** TaxonInfo.ancestor_ids is empty (taxon_id matches with `include_descendants=True` need an iNat taxa-tree fetch in species_cache fill); `scripts/draft_expedition.py` is a stub (filed as [risk 0004](docs/risks/0004-expedition-authoring-tooling.md) -- LLM-assisted authoring is a Phase 11 nice-to-have, the manual JSON path works fine for the next ~20 expeditions).

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

**Status:** ✅ Met 2026-05-10 from the code's perspective. PRs #59-62 shipped: signed-GET photo URL + mobile review queue UI consuming the Phase 8 review_queue endpoints (PR #59), `admin/sweep_stale_reviews.py` auto-rejecting reviews open >30 days per `docs/moderation.md` (PR #60), `observations.dispatched_at` column + Alembic migration + `admin/dispatcher_replay.py` re-dispatching crashed observations per `docs/dispatcher.md` snapshot 11 (PR #61), three Cloud Monitoring alarms (api p95 latency / Cloud SQL CPU / Cloud Run instance count) + a 2x2 dogfood dashboard for the closed-beta period (PR #62), privacy policy DRAFT + app-store compliance checklist + risk doc capturing the human-action items that gate the actual beta launch. **The Phase 11 exit criteria ("first closed-beta group is invited" + "at least one real kid submits at least one real outdoor observation") are NOT met by code alone** -- they need: lawyer review of the privacy policy, resolution of risks 0001-0004, Terraform apply of the new monitoring resources, Cloud Scheduler wiring of the three new admin tasks, app-store submissions, beta-tester onboarding sessions. All captured with an ordered checklist in [risk 0005](docs/risks/0005-beta-launch-human-action-items.md).

### 12. Phase 2

Goal: deepen engagement without disrupting Phase 1 loops.

Candidates:

- Territory map using MapLibre/OSM rendering.
- Sanctuary / open-world layer with `WorldHandler` — full Phase 2 product + architecture contract in [`docs/sanctuary.md`](docs/sanctuary.md). The `WORLD#` phrasing kept here is conceptual only; the real persistence is Postgres tables (indicative: `sanctuary_zone_state`, `sanctuary_unlocks`, `sanctuary_content`) per ADR 0005.
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
