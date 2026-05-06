# Phase 1 Roadmap

10–12 weeks, solo. The goal at the end of Phase 1 is a closed beta: kids in a small number of invited groups can log observations, see their Dex fill, complete starter expeditions, and feel first-find celebrations that hit. The app does not need territory, seasons, missions, or teacher dashboards at this point — those are Phase 2–4.

Every week has an **exit criterion** that defines "done." Weeks slip; criteria don't.

---

## Week 0 — Foundations (this week)

Starter repo scaffolded, docs written, three ADRs landed. Not building features yet; getting the constraints right before the first line of product code.

**Exit criterion.** `make install` succeeds from a clean clone. `README.md` accurately describes what exists.

---

## Week 1–2 — Phase 0 round-trip

Get a real deployed API responding to a real Expo client. Nothing fancy; this is the "does the pipe work" milestone.

- Cloud Run service `dragonfly-api` and Cloud SQL Postgres instance provisioned in `dev` (project `dragonflyapp-495423`).
- FastAPI on Cloud Run returns 200 on `GET /health`.
- Expo project (new) fetches `/health` and renders the JSON.
- GitHub Actions: build via Cloud Build on PR, `gcloud run deploy` on merge to main for `dev`.

**Exit criterion.** Expo app on a physical device (iOS or Android) displays the `/health` response from the deployed Cloud Run service in `dev`. Phase 0 is done. Do not proceed to Week 3 until this is green.

---

## Week 3 — Auth end-to-end

Firebase Auth works. A parent can create an account. A parent can create a kid account and a group, and the kid can sign in with a username.

- `POST /v1/auth/parent-signup` — email + password, creates Firebase user with custom claim `role=parent`.
- `POST /v1/groups` — creates a group, returns a 6-char join code.
- `POST /v1/groups/{id}/kids` — admin-create-user flow (Firebase Admin SDK) for a kid account under that group.
- JWT dependency in `core/auth.py` verifies Firebase ID tokens against the JWKS and extracts `role` and `group_id` from custom claims.

**Exit criterion.** A Postman collection (checked in at `docs/postman/`) can sign up a parent, create a group, create a kid, sign in as the kid, and call a `/v1/me` endpoint that returns the kid's profile.

---

## Week 4 — Observation submission, happy path

The core write path, minus async workers. Synchronous everything; no iNat push yet; no moderation yet.

- `POST /v1/photos/presign` — returns a V4 signed PUT URL into the `pending/` prefix on the photos bucket.
- `POST /v1/observations` — validates, runs the submission transaction (insert observation row + bump membership counter + first-find `INSERT ... ON CONFLICT DO NOTHING` on the dex table), invokes a stub dispatcher that returns an empty reward list.
- SQLAlchemy models for users, groups, memberships, observations, dex.
- Postgres access via the Cloud SQL Python connector with `asyncpg`, in `app/db/session.py`.

**Exit criterion.** Kid account on mobile can pick a photo from device, upload it, and see the observation appear in a "my observations" list backed by `SELECT ... FROM observations WHERE user_id = $1 ORDER BY created_at DESC`. The list shows the photo from its GCS URL.

---

## Week 5 — iNat CV + geocoding integration (decision week)

Two third-party integrations on the hot path, both with fallbacks. ADR 0002 is on the line this week — if iNat CV is unusable for kid photos, we re-open the "LLM-assisted ID fallback" deferred alternative.

- iNat `/v1/computervision/score_image` called on observation submit; taxon suggestions returned to the client.
- Google Geocoding called on observation submit; place name cached on the observation row.
- Google Places (Nearby) called in the "Start where you are" onboarding; results cached in a `geo_cache` table keyed by `(rounded_lat, rounded_lng)` for 7 days.
- `geo_cache` table added to `data-model.md` (ADR 0003 follow-up).

**Exit criterion.** 50 test observations from kid-style photography (low-light, obscure angles, kids' backyards) submitted; iNat CV's top-3 suggestions include the correct species in ≥70% of cases. If below that threshold, file a Phase 1 risk and schedule a Week 6 variance discussion.

---

## Week 6 — Moderation + photo lifecycle

Moderation provider integration (per follow-up ADR). Quarantine flow. Teacher review queue schema (UI comes Week 11).

- Cloud Run service triggered by Eventarc on `google.cloud.storage.object.v1.finalized` for the `pending/` prefix.
- Clean photos copied to `observations/`; flagged photos moved to `quarantine/` with a row written to the `review_queue` table for the group.
- GCS lifecycle rules on `pending/` (24h delete) and `quarantine/` (90d after resolution) — verify they're actually running.
- `docs/moderation.md` written.

**Exit criterion.** A deliberately inappropriate test image submitted by a test kid account lands in `quarantine/`, writes a `review_queue` row, and is not visible in the kid's observation list. Moderation thresholds tuned against a 20-image sample set checked into `content/moderation_test_set/` (all synthetic, nothing real).

---

## Week 7 — iNat submit worker + rarity cache scaffold

The other async worker, plus the data structure `RarityHandler` will read.

- `inat_submit` Cloud Run service as a Cloud Tasks consumer, with managed retries, idempotency by observation ID, and a DLQ queue for terminal failures.
- On success, writes `inat_observation_id` onto the observation row.
- Seed script populates `rarity_cache` rows keyed by `(region_geohash4, species_taxon_id)` with tier data from a one-time iNat export for the target beta regions.
- `docs/rarity-pipeline.md` written (describes the Week 9+ nightly job, but rows exist now).

**Exit criterion.** A submitted observation appears on iNaturalist (as the project account) within 60 seconds of submission. DLQ has zero tasks after a week of dogfood use.

---

## Week 8 — Dispatcher test harness + DexHandler

The first real dispatcher code. Test harness first, then `DexHandler`, so that "snapshot scenario #1 through #11" drives the implementation rather than the other way around.

- `app/dispatcher/core.py` (the 30-line dispatcher itself).
- `app/dispatcher/registry.py` (`HANDLERS` list, Dex only to start).
- `tests/fixtures/dispatcher.py` — Postgres-backed harness using `testcontainers` or a dedicated test schema.
- `DexHandler` per ADR 0004: owns the dex insert, owns the `dex_count` counter bump on the membership row.
- Snapshot tests #1, #2, #9, #10 land.

**Exit criterion.** `pytest tests/dispatcher/` runs green. Coverage for `app/dispatcher/*` is ≥90%.

---

## Week 9 — ExpeditionHandler + RarityHandler + rarity nightly job

Two more handlers, plus the Cloud Scheduler cron that keeps rarity data fresh.

- `ExpeditionHandler` queries active expedition progress, matches steps, advances.
- Matcher registry with all Phase 1 match kinds (`iconic_taxon`, `taxon_id`, `any_organism`, `not_in_dex`, `not_within_radius_of_existing`, `all_of`, `any_of`).
- `RarityHandler` per ADR 0004: owns the `rarest_tier` counter update on the membership row.
- `rarity_refresh` Cloud Run job invoked by Cloud Scheduler at 03:00 UTC, self-continuation via `JOB#rarity` cursor row.
- Snapshot tests #3 through #8 land.

**Exit criterion.** All 11 dispatcher snapshot scenarios green. Dispatcher p95 < 300ms measured against the test harness.

---

## Week 10 — Expedition authoring + starter content

Content treadmill starts here. The tooling built this week is what makes 2-expeditions-per-week sustainable post-launch.

- `scripts/draft_expedition.py` — author-time LLM tool (ADR 0002 compliant). Input: theme prompt. Output: expedition JSON draft.
- `scripts/validate_content.py` — validates `content/expeditions/**/*.json` against the Pydantic model.
- `scripts/sync_expeditions.py` — syncs validated JSON to Postgres on merge to main.
- `.github/workflows/content-validate.yml` — CI gate.
- The five starter expeditions authored and checked in: `backyard_starter`, `park_starter`, `street_starter`, `school_starter`, `anywhere_starter`.

**Exit criterion.** All five starters visible in the Expo app's "pick an expedition" screen, filtered correctly by environment. Completing one triggers the `expedition_complete` celebration.

---

## Week 11 — Tier-2 expeditions + teacher review UI

Minimum viable teacher surface, plus a richer expedition catalog so the beta has runway.

- Five tier-2 expeditions authored (one unlocked by each tier-1 completion).
- Teacher review screen in the Expo app: list of pending `REVIEW#` rows for the teacher's group, approve/reject actions.
- Approve/reject wired through to the `review_queue` row plus the cleanup described in `docs/runbook.md`.

**Exit criterion.** A test teacher account can see a quarantined observation, approve it, and verify the observation appears on the kid's Dex.

---

## Week 12 — Beta polish + observability

Last week before closed beta. Everything flagged "Phase 1 Week 12" in follow-ups lands here.

- `scripts/replay_missed_dispatch.py` (ADR 0004 follow-up).
- Cloud Monitoring alert: observations without matching dex rows older than 1 hour (ADR 0004 follow-up).
- Cloud Monitoring alert: per-request LLM calls from the API service (ADR 0002 follow-up).
- GCP billing budget alerts on Google Maps Platform usage (ADR 0003 follow-up).
- `scripts/sweep_stale_reviews.py` (runbook follow-up).
- Dogfood dashboard: a single Cloud Monitoring dashboard with the seven metrics that actually matter (API 5xx, dispatcher p95, moderation DLQ depth, iNat DLQ depth, rarity job duration, observations/day, Cloud Run instance-startup count).

**Exit criterion.** First closed-beta group invited. At least one real kid submits at least one real observation outside the house. That's the bar. Anything beyond that is bonus.

---

## Not in Phase 1

Tracking here so they don't creep in.

- **Territory map** — Phase 2.
- **Seasons and phenology (USA-NPN sync)** — Phase 3.
- **Missions** — Phase 4.
- **Kid iNat account claim flow (13+)** — Phase 3.
- **Offline tile bundles** — Phase 2.
- **Teacher dashboard beyond review queue** — Phase 2.
- **Custom MapLibre styling** — Phase 2 (use Stadia/OSM default for starters).
- **Push notifications** — Phase 2.

If one of these starts feeling essential mid-Phase 1, write it down and keep moving. The shape of Phase 1 was chosen specifically so none of these are needed for the closed beta to succeed.

---

## Slip policy

Weeks 1–2, 8–9, and 12 are the ones that cannot slip without re-planning the phase: Phase 0 gate, the dispatcher spine, and beta launch prep. The rest can slip a week each with the phase still hitting a 12-week end. Cumulative slip beyond 3 weeks triggers a phase re-scope — drop a tier-2 expedition goal, drop the teacher review UI to a separate post-beta release, or delay the beta by two weeks. Pick one explicitly; don't compress the last week.
