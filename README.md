# Dragonfly

Citizen science field app for kids 9–12. Every observation is real science via iNaturalist, fills a personal Dex, claims map territory, and earns standing in a class or friend group. Invite-only.

## Repo layout

Exists today:

```
backend/    FastAPI app. Runs locally via uvicorn; Mangum handler kept
            for the AWS path. Dockerfile targets Cloud Run.
infra/      AWS CDK (Python) stacks: api, auth, data. Legacy path —
            kept until the GCP migration ADR lands and removes it.
docs/       Architecture, data model, ADRs. Read these first.
AGENTS.md   Project plan, invariants, and guardrails for coding agents.
```

Planned, not yet present:

```
lambdas/    Moderation, iNat submit, rarity refresh.
mobile/     Expo (React Native) — iOS, Android, web.
content/    Expedition JSON. Source of truth; datastore is a view.
scripts/    sync_expeditions, seed_dev_data, backfill_rarity, validate.
```

## Current direction

Active migration target is **GCP / Cloud Run** for the API runtime. The GCP target architecture is documented in [ADR 0005](docs/adr/0005-gcp-target-architecture.md). The AWS CDK path under `infra/` remains in the repo until the `infra-gcp/` migration completes. See AGENTS.md §"Current Direction" for the compatibility rules during the transition.

## Getting started (Phase 0)

Prereqs: Python 3.12, `uv`. AWS CLI + CDK v2 only required for the legacy AWS deploy path.

```bash
make install
make dev-db                 # optional local Postgres for Phase 3+ API work
make dev                    # FastAPI on :8080
curl localhost:8080/health
curl localhost:8080/ready
curl localhost:8080/v1/meta
```

Cloud Run (current target):

```bash
docker build -t dragonfly-api backend/
docker run --rm -p 8080:8080 -e DRAGONFLY_ENV=local dragonfly-api
curl localhost:8080/health
```

AWS CDK (legacy path, dev account):

```bash
cd infra
export DRAGONFLY_ENV=dev
uv run cdk bootstrap        # once per account/region
uv run cdk deploy --all
```

Phase 0 exit criterion: the Expo app shows the response from `/health` served by the deployed API. Don't build Phase 1 features until this round-trip works.

## Where to look when

- **How a feature works end-to-end:** `docs/architecture.md`
- **What the DB looks like:** `docs/data-model.md`
- **How to add a reward type:** `docs/dispatcher.md`
- **How to write an expedition:** `docs/expedition-authoring.md`
- **What decision was made and why:** `docs/adr/`

## Current phase

Phase 1 (MVP). 10–12 weeks solo. See `docs/roadmap.md` for the week-by-week plan.
