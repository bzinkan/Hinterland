# Build context is the REPO ROOT, not backend/ -- expedition content ships
# inside the image so image version == content version:
#   docker build -f backend/Dockerfile .
#   az acr build --registry ... --image ... --file backend/Dockerfile .
# The context-level ignore rules live in the repo-root .dockerignore
# (Docker only reads <context>/.dockerignore; backend/.dockerignore does
# not apply to this build).
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY backend/app/ ./app/
COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/
COPY backend/admin/ ./admin/
# Expedition content baked into the image; admin.sync_expeditions
# materializes it into Postgres after each deploy (docs/runbook.md).
COPY content/expeditions/ ./content/expeditions/
COPY content/expedition_taxon_sets.json ./content/expedition_taxon_sets.json
# Sanctuary is runtime dispatcher content: rebuilds and live WorldHandler
# dispatches must use the same authored snapshot as the API image.
COPY content/sanctuary/ ./content/sanctuary/
# Audited runtime taxonomy snapshot used by the catalog ingest job.
COPY content/taxa/ ./content/taxa/

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
