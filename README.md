# Dragonfly

Citizen-science field app for kids ages 9-12. Every observation is real
science via iNaturalist, fills a personal Dex, contributes to expeditions, and
earns standing in an invite-only class or family group.

## Repo Layout

Exists today:

```text
backend/     FastAPI app. Runs locally via uvicorn; Mangum handler kept for
             the legacy AWS path until Cloud Run is fully production.
infra-gcp/   Terraform for Cloud Run, Cloud SQL, GCS, Artifact Registry, IAM,
             Workload Identity Federation, monitoring, and DNS.
infra/       Legacy AWS CDK stacks. Kept only until the GCP path is serving
             production traffic.
docs/        Architecture, data model, ingest rules, ADRs, and runbooks.
internal/    Internal-only tooling such as future AI-agent experiments.
AGENTS.md    Project plan, invariants, and guardrails for coding agents.
```

Planned, not yet present:

```text
mobile/      Expo app for iOS, Android, and web.
content/     Expedition JSON. Source of truth; Postgres is a materialized view.
scripts/     Content sync, replay, seed, validation, and operational helpers.
```

## Current Direction

Active migration target is **GCP / Cloud Run** for the API runtime. The GCP
target architecture is documented in
[`docs/adr/0005-gcp-target-architecture.md`](docs/adr/0005-gcp-target-architecture.md).
Postgres replaces the old DynamoDB data model. Ingest pipelines are explicit
and replayable per [`docs/adr/0006-ingest-pipelines.md`](docs/adr/0006-ingest-pipelines.md).
AI agent tooling is internal-only per
[`docs/adr/0007-internal-ai-agent-tooling.md`](docs/adr/0007-internal-ai-agent-tooling.md).

## Getting Started

Prereqs: Python 3.12, `uv`, Docker, and Terraform for GCP infra work.

```bash
make install
make dev-db
make db-migrate
make dev
curl localhost:8080/health
curl localhost:8080/ready
curl localhost:8080/v1/meta
```

Docker smoke:

```bash
docker build -t dragonfly-api backend/
docker run --rm -p 8080:8080 -e DRAGONFLY_ENV=local dragonfly-api
curl localhost:8080/health
```

Terraform dev plan:

```bash
make terraform-plan-dev
```

Legacy AWS CDK remains available only for historical/reference work:

```bash
cd infra
export DRAGONFLY_ENV=dev
uv run cdk bootstrap
uv run cdk deploy --all
```

## Where To Look

- **Agent instructions and current phase:** `AGENTS.md`
- **Architecture:** `docs/architecture.md`
- **Postgres model:** `docs/data-model.md`
- **Ingest/replay rules:** `docs/ingest.md`
- **Rewards and dispatcher:** `docs/dispatcher.md`
- **Mobile constraints:** `docs/mobile.md`
- **Sanctuary (Phase 2 design):** `docs/sanctuary.md`
- **Decisions:** `docs/adr/`

## Current Phase

Phase 1 MVP, targeting closed beta. The current implementation focus is the
production foundation: Cloud Run, Cloud SQL/Postgres, Terraform, ingest
contracts, and deterministic API/runtime scaffolding before product features.
