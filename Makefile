# Hinterland common dev commands.
# Everything in here should run from the repo root.

.PHONY: help install dev dev-db dev-db-down dev-db-logs db-migrate db-revision \
        test lint fmt typecheck validate-content terraform-fmt terraform-validate \
        terraform-plan-dev cdk-synth cdk-diff cdk-deploy clean

help:
	@echo "Hinterland dev commands:"
	@echo "  make install             - install backend deps"
	@echo "  make dev                 - run FastAPI locally (hot reload)"
	@echo "  make dev-db              - start local Postgres for backend dev"
	@echo "  make dev-db-down         - stop local Postgres"
	@echo "  make db-migrate          - apply backend Alembic migrations"
	@echo "  make test                - run backend tests"
	@echo "  make lint                - ruff check"
	@echo "  make fmt                 - ruff format + fix"
	@echo "  make typecheck           - mypy"
	@echo "  make validate-content    - validate expedition JSON files"
	@echo "  make terraform-plan-dev  - Terraform plan for dev GCP foundation"
	@echo "  make cdk-synth           - legacy CDK synth (dry run)"
	@echo "  make cdk-diff            - legacy CDK diff against deployed stack"
	@echo "  make cdk-deploy          - legacy CDK deploy (requires HINTERLAND_ENV)"

install:
	cd backend && uv sync

dev:
	cd backend && uv run uvicorn app.main:app --reload --port 8080

dev-db:
	docker compose -f backend/compose.yaml up -d postgres

dev-db-down:
	docker compose -f backend/compose.yaml down

dev-db-logs:
	docker compose -f backend/compose.yaml logs -f postgres

db-migrate:
	cd backend && uv run alembic upgrade head

db-revision:
	cd backend && uv run alembic revision --autogenerate -m "$$MESSAGE"

test:
	cd backend && uv run pytest -v

lint:
	cd backend && uv run ruff check .

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

typecheck:
	cd backend && uv run mypy app

validate-content:
	cd backend && uv run python ../scripts/validate_content.py

terraform-fmt:
	cd infra-gcp && terraform fmt -recursive

terraform-validate:
	cd infra-gcp && terraform init -backend=false && terraform validate

terraform-plan-dev:
	cd infra-gcp && terraform init && terraform plan -var-file=environments/dev.tfvars

cdk-synth:
	cd infra && uv run cdk synth

cdk-diff:
	cd infra && uv run cdk diff

cdk-deploy:
	@if [ -z "$$HINTERLAND_ENV" ]; then \
		echo "Set HINTERLAND_ENV=dev|staging|prod first"; exit 1; \
	fi
	cd infra && uv run cdk deploy --all --require-approval never

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name cdk.out -exec rm -rf {} +
