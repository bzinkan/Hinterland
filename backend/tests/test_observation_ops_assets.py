from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_w1_lifecycle_policy_has_only_safe_prefix_rules() -> None:
    policy = json.loads(
        (_ROOT / "infra-azure/policies/observation-w1-lifecycle.json").read_text(encoding="utf-8")
    )
    rules = {rule["name"]: rule for rule in policy["rules"]}

    raw = rules["observation-unattached-upload-24h"]["definition"]
    assert raw["filters"]["prefixMatch"] == ["photos/pending/uploads/"]
    assert raw["actions"]["baseBlob"]["delete"]["daysAfterModificationGreaterThan"] == 1

    held = rules["observation-quarantine-rejected-90d"]["definition"]
    assert held["filters"]["prefixMatch"] == ["photos/quarantine/", "photos/rejected/"]
    assert held["actions"]["baseBlob"]["delete"]["daysAfterModificationGreaterThan"] == 90

    pilot = rules["observation-pilot-private-7d"]["definition"]
    assert pilot["filters"]["prefixMatch"] == ["photos/pilot-private/"]
    assert pilot["actions"]["baseBlob"]["delete"]["daysAfterModificationGreaterThan"] == 7

    prefixes = [
        prefix
        for rule in policy["rules"]
        for prefix in rule["definition"]["filters"]["prefixMatch"]
    ]
    assert "photos/pending/finalized/" not in prefixes


def test_active_azure_environment_is_hinterland_only() -> None:
    environment = (_ROOT / "infra-azure/environments/hinterland-dev.env").read_text(
        encoding="utf-8"
    )

    assert 'PROJECT_SLUG="hinterland"' in environment
    assert 'HINTERLAND_RESOURCE_GROUP="hinterland-dev-rg"' in environment
    assert 'HINTERLAND_CONTAINER_APP_NAME="hinterland-api"' in environment
    assert 'HINTERLAND_KID_JWKS_PATH="/.well-known/hinterland-kid-jwks.json"' in environment
    assert not (_ROOT / "infra-azure/phase-9-observation-w1.sh").exists()
    assert not (_ROOT / "infra-gcp").exists()
    assert not (_ROOT / "infra").exists()


def test_active_workflow_migrates_before_api_and_removes_retired_aliases() -> None:
    workflow = (_ROOT / ".github/workflows/deploy-azure-api-dev.yml").read_text(encoding="utf-8")

    retire_jobs = workflow.index("Retire obsolete recovery jobs")
    pin_jobs = workflow.index("Pin Hinterland jobs to this image")
    seed_settings = workflow.index("Seed Hinterland job settings")
    remove_aliases = workflow.index("Remove retired runtime variable aliases")
    required_jobs = workflow.index("Run required pre-deploy jobs")
    deploy_api = workflow.index("Deploy API revision")
    rebuild = workflow.index("Rebuild derived state")
    smoke = workflow.index("Smoke public API surfaces")
    verify = workflow.index("Verify deployed naming and image")

    assert (
        retire_jobs
        < pin_jobs
        < seed_settings
        < remove_aliases
        < required_jobs
        < deploy_api
        < rebuild
        < smoke
        < verify
    )
    assert "HINTERLAND_KID_JWKS_PATH" in workflow
    assert "HINTERLAND_SMOKE_ENTRA_BEARER" in workflow
    assert "HINTERLAND_DATABASE_PASSWORD=secretref:pg-password" in workflow
    assert "--remove-env-vars" in workflow
    assert "hinterland-obs-preflight" in workflow
    assert "hinterland-migrate" in workflow
    assert "hinterland-taxa-catalog-ingest" in workflow
    assert "hinterland-sync-expeditions" in workflow
    assert "hinterland-state-rebuild" in workflow
    assert not (_ROOT / ".github/workflows/deploy-cloud-run-dev.yml").exists()
    assert not (_ROOT / ".github/workflows/deploy-dev.yml").exists()


def test_api_image_includes_every_runtime_authored_content_tree() -> None:
    dockerfile = (_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY content/expeditions/ ./content/expeditions/" in dockerfile
    assert "COPY content/sanctuary/ ./content/sanctuary/" in dockerfile
    assert "COPY content/taxa/ ./content/taxa/" in dockerfile
    assert "!content/expeditions/**" in dockerignore
    assert "!content/sanctuary/**" in dockerignore
    assert "!content/taxa/**" in dockerignore


def test_migration_registers_pending_work_before_relay() -> None:
    migration = (
        _ROOT / "backend/alembic/versions/20260709_0014_observation_w1_contract.py"
    ).read_text(encoding="utf-8")

    assert "INSERT INTO moderation_outbox" in migration
    assert "p.attachment_status = 'attached'" in migration
    assert "admin.observation_legacy_reconcile" in migration


def test_ci_runs_observation_contract_against_real_postgres() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "observation-postgres:" in workflow
    assert "postgres:16-alpine" in workflow
    assert "hinterland_observation_verify" in workflow
    assert "uv sync --locked" in workflow
    assert "uv run alembic upgrade head" in workflow
    assert "tests/integration/test_observation_postgres.py" in workflow
    assert "OBSERVATION_TEST_DATABASE_URL" in workflow
    assert 'OBSERVATION_DISPATCHER_PROBE_RUNS: "50"' in workflow
