# Rarity Pipeline

The rarity cache is how Hinterland tells a kid that their observation matters beyond "you logged a thing." `RarityHandler` (see `dispatcher.md`) reads a per-region, per-species tier row and emits a reward sized to match: `legendary` for something almost no one has logged in this geohash cell, `abundant` suppressed entirely. The data powering those lookups is refreshed by a nightly batch job against iNaturalist — not at observation time, because iNat rate limits matter more than freshness for this feature.

Related reading: `architecture.md` (how the nightly Cloud Run job fits into the worker topology), `data-model.md` (the `rarity_cache` table keyed by `(region_geohash4, taxon_id)`), `dispatcher.md` (how `RarityHandler` interprets the tier values), `adr/0005-gcp-target-architecture.md` (Cloud SQL Postgres replaces ADR 0001's DynamoDB).

## Why a nightly cache instead of a live lookup

Three reasons, each sufficient on its own:

1. **iNat rate limits.** iNaturalist's API allows roughly 60 requests/minute per authenticated account and 10k/day. At meaningful DAU we would exhaust that in hot-path rarity lookups alone, before any of the project-submit traffic we also need.
2. **Dispatcher budget.** `RarityHandler` runs inside the 300ms dispatcher p95 budget. An iNat round-trip (p95 ~800ms at the edge) does not fit.
3. **Determinism.** Two kids logging the same species in the same neighborhood an hour apart should see the same tier. A live iNat lookup is subject to second-order variance from whoever else submitted in that window. A nightly snapshot is stable for the day.

Cost: rarity data lags iNat reality by up to 24 hours. For a feature about "how rare is this in your region," that staleness is invisible — region rarity doesn't meaningfully shift hour to hour. Acceptable.

## Trigger and shape

- **Schedule.** Cloud Scheduler cron, `0 3 * * *` — 03:00 UTC. Picked to be well off US peak and off European peak so our iNat usage doesn't compete with human traffic.
- **Resource.** One Cloud Run job, 1 vCPU, 2GB memory. Cloud Run job task timeout is configurable up to 24h, so the wall-clock ceiling is generous; we still aim for a single sub-hour run.
- **Continuation.** The job writes its cursor to a `job_state` row keyed `name='rarity'` on every region batch. If it runs out of time or hits a transient failure, a second cron at 04:00 UTC re-invokes and the job reads the cursor and resumes. Target: whole-world refresh completes in one nightly run at our beta scale; multi-run continuation is the safety net.
- **Parallelism.** Strictly none. A single-threaded walk that respects iNat's rate limit is the whole design. Parallelizing would either throttle us or get us 429'd.

## The algorithm

For each geohash-4 cell where Hinterland has at least one observation in the last 90 days (tracked in `REGION#<gh>/META.last_seen_at`):

1. Query iNat `/v1/observations/species_counts` for the cell's bounding box, verifiable observations, research-grade + needs-id, last 5 years. Returns `{taxon_id, count}` pairs.
2. Compute the total observation count for the cell, `N`. Tier each species by its share of `N`:

   | Tier       | Share of cell observations         |
   |------------|------------------------------------|
   | abundant   | ≥ 20%                              |
   | common     | 5–20%                              |
   | rare       | 1–5%                               |
   | epic       | 0.1–1%                             |
   | legendary  | < 0.1% (but appears at least once) |

   These thresholds are a starting point. Tune once we have at least 30 days of live kid observations and can see how the distribution looks for our actual users' regions. Thresholds live in SSM at `/dragonfly/{env}/rarity/tier_thresholds`.

3. If `N < 50`, the cell is data-sparse. Write `REGION#<gh>/META.low_data = true` and *do not* compute a per-species tier at the geohash-4 level. `RarityHandler` will fall back to the geohash-3 parent cell for tier decisions in this region.

4. Upsert each `(region, species)` pair as `PK=REGION#<gh>, SK=SPECIES#<taxonId>, tier=<tier>, count=<n>, refreshed_at=<iso>`. Use `BatchWriteItem` in chunks of 25.

5. Delete rows whose species no longer appears in the current iNat snapshot (e.g. taxonomic reclassifications folded one taxon into another). These are rare enough that a single-item `DeleteItem` per missing row is fine.

6. Update `REGION#<gh>/META`: `SET refreshed_at = :now, observation_count = :N, low_data = :low_data`.

7. Write cursor to `JOB#rarity/STATE`: `{last_region: "<gh>", regions_remaining: [...], started_at, model_version: "v1"}`.

At the end of the run, clear the cursor (`STATE.regions_remaining = []`) and record a run summary row for observability.

## `low_data` and the geohash-3 fallback

Our tiering is statistical — below a sample size threshold it's noise. Rather than lie to a kid with a meaningless tier, we mark sparse cells as `low_data` and let `RarityHandler` fall back to the larger geohash-3 parent cell.

Geohash-4 is roughly 20km × 20km; geohash-3 is roughly 156km × 156km. Geohash-3 is almost always well-sampled even for rural US regions. The fallback sacrifices locality for signal — a kid in a rural town gets a "regional rarity" signal based on their state-ish-sized area instead of their county, which is the right trade for both signal and scientific meaning.

Geohash-3 tier rows are written by the same nightly job, but only for cells whose geohash-4 children collectively have less than 200 observations — we don't need a geohash-3 refresh for well-sampled areas.

## Unrecorded species

A special case: the observation's species has never been logged in this region at all. `RarityHandler` detects this as "region's `META` row exists (someone has logged *something* here) AND no `SPECIES#<taxonId>` row under it." This gets weight-100 `unrecorded` reward — the rarest possible. It's also the case most interesting scientifically, because a kid's observation is actually changing what the regional species list looks like.

Note: "unrecorded" here means unrecorded in *our* nightly snapshot, which is derived from iNat's verifiable observations. A species that's been logged in iNat today but hadn't been at last night's snapshot would still trigger `unrecorded`. That's fine — the kid's observation will show up in the next night's snapshot, and subsequent kids won't get the same reward. The reward is for the moment of discovery, not a permanent record.

## Capacity and cost at our scale

A full refresh for Phase 1 beta regions (say, 200 geohash-4 cells averaging 300 species each) is 60k `INSERT ... ON CONFLICT DO UPDATE` calls into `rarity_cache`, concentrated in a 15-minute window. Average ~67 writes/s. Cloud SQL Postgres on a `db-g1-small` absorbs this trivially; user-driven writes never share the rarity hot path because the job batches in chunks and runs at 03:00 UTC.

iNat API cost is the dominant constraint. 200 regions × 1 species-counts call per region = 200 calls per run. Well inside iNat's 10k/day budget and well inside the 60 req/min pacing. Room to grow 10–20x before rate limits become the binding constraint.

## Observability

The job emits one structured log line per region (`rarity.region_complete` with `{region, species_count, low_data, duration_ms}`) and one per run (`rarity.run_complete` with `{regions_total, regions_processed, duration_ms, api_calls, throttles}`). Cloud Monitoring alerts fire on:

- Run duration > 12 minutes (signal: we're close to self-continuation).
- Run has not completed for 48 hours (signal: cursor stuck or cron not firing).
- iNat 429 count > 10 in a run (signal: pacing logic needs tuning).

The runbook section for stalled rarity jobs is in `runbook.md`.

## What this doc doesn't cover

- **Seasonality adjustment.** A bird that's rare in winter but abundant in spring should tier differently by month. Planned for Phase 3 (the Seasons handler), which will read a `SPECIES#<taxonId>/SEASON#<month>` row layered over the base rarity data.
- **Per-species confidence floors.** Taxa that iNat's community hasn't strongly verified — high `needs_id` vs `research_grade` ratio — may need a separate "observed_but_unverified" tier so we don't celebrate a questionable ID. Defer to post-beta.
- **The initial seed.** Before the first nightly run, rarity rows have to come from somewhere. Phase 1 Week 7 in `roadmap.md` ships a one-time seed script that imports iNat species-counts for the beta regions into the `REGION#` partition. After that, the nightly job maintains it.
