from __future__ import annotations

import json
import re
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
    assert held["filters"]["prefixMatch"] == [
        "photos/quarantine/",
        "photos/rejected/",
    ]
    assert held["actions"]["baseBlob"]["delete"]["daysAfterModificationGreaterThan"] == 90

    pilot = rules["observation-pilot-private-7d"]["definition"]
    assert pilot["filters"]["prefixMatch"] == ["photos/pilot-private/"]
    assert pilot["actions"]["baseBlob"]["delete"]["daysAfterModificationGreaterThan"] == 7

    # Finalized/pilot state needs a relational check and must never be covered
    # by an unconditional Azure lifecycle prefix rule.
    prefixes = [
        prefix
        for rule in policy["rules"]
        for prefix in rule["definition"]["filters"]["prefixMatch"]
    ]
    assert "photos/pending/finalized/" not in prefixes


def test_hinterland_phase9_is_digest_pinned_and_egress_default_deny() -> None:
    script = (_ROOT / "infra-azure/phase-9-observation-w1.sh").read_text(encoding="utf-8")

    assert "environments/hinterland-dev.env" in script
    assert "HINTERLAND_DATABASE_HOST" in script
    assert "postgres-host" not in script
    assert "postgres-admin-user" not in script
    assert "postgres-database" not in script
    assert "az storage container-rm create" in script
    assert "az storage container create" not in script
    assert "command az \"$@\" | tr -d '\\r'" in script
    assert "azure_file_path" in script
    assert '--policy "@${POLICY_AZ_PATH}"' in script
    assert '--yaml "$job_yaml_az"' in script
    assert "< <(" not in script
    assert '"$RG" == "gordi-pilot-rg"' in script
    assert '"$IMAGE" != *@sha256:*' in script
    assert "HINTERLAND_MODERATION_PROVIDER=noop" in script
    assert "HINTERLAND_INAT_CV_ENABLED=false" in script
    assert "HINTERLAND_INAT_SUBMIT_ENABLED=false" in script
    assert "--remove-env-vars HINTERLAND_INAT_OAUTH_TOKEN" in script
    assert script.index("--remove-env-vars HINTERLAND_INAT_OAUTH_TOKEN") < script.index(
        "HINTERLAND_INAT_CV_ENABLED=false"
    )
    assert "admin.observation_migration_preflight" in script
    assert "admin.observation_legacy_reconcile" in script
    assert "HINTERLAND_OBSERVATION_PREFLIGHT_ACK" in script
    assert script.index("run read-only Observation migration preflight") < script.index(
        "run additive migrations"
    )
    assert "event-subscription delete" in script
    assert "event-subscription create" not in script
    assert "az eventgrid system-topic list" in script
    assert "STORAGE_ACCOUNT_ID" in script
    assert "${topic_source,,}" in script
    assert 'if ! remaining="$(az eventgrid system-topic event-subscription list' in script
    assert "could not verify direct moderation producer removal" in script
    assert script.index(
        'if ! remaining="$(az eventgrid system-topic event-subscription list'
    ) < script.index("could not verify direct moderation producer removal")
    assert "EVENT_GRID_TOPIC" not in script
    assert "inat-submit-worker" in script and "job delete" in script
    assert "remaining_inat_jobs" in script
    assert '--scope "$SB_NAMESPACE_ID"' in script
    assert "--include-inherited" in script
    assert "Azure Service Bus Data Owner" in script
    assert "wait_for_role_absent" in script
    assert "for attempt in $(seq 1 12)" in script
    assert "triggerType: Manual" in script
    assert "manualTriggerConfig:" in script
    assert script.index("triggerType: Manual") < script.index("manualTriggerConfig:")
    assert '"$raw_days" =~ ^1([.]0+)?$' in script
    assert '"$held_days" =~ ^90([.]0+)?$' in script
    assert '"$pilot_days" =~ ^7([.]0+)?$' in script
    assert script.index('wait_for_job "${PREFIX}-legacy-reconcile"') < script.index(
        'ensure_job "${PREFIX}-mod-outbox-relay"'
    )
    assert script.index('wait_for_job "${PREFIX}-taxa-catalog-ingest"') < script.index(
        'wait_for_job "${PREFIX}-legacy-reconcile"'
    )
    assert script.index('wait_for_job "${PREFIX}-sync-expeditions"') < script.index(
        'wait_for_job "${PREFIX}-legacy-reconcile"'
    )
    assert script.index('wait_for_job "${PREFIX}-legacy-reconcile"') < script.index(
        'wait_for_job "${PREFIX}-state-rebuild"'
    )
    assert script.index('wait_for_job "${PREFIX}-state-rebuild"') < script.index(
        'ensure_job "${PREFIX}-dispatcher-replay"'
    )
    assert script.index("countDetails.activeMessageCount") < script.index(
        'ensure_job "${PREFIX}-mod-outbox-relay"'
    )


def test_hinterland_workflow_migrates_before_api_and_uses_jwks_alias() -> None:
    # Observation deployment repairs the existing Azure workflow; a second
    # deploy workflow would create competing production authorities.
    workflow = (_ROOT / ".github/workflows/deploy-azure-api-dev.yml").read_text(encoding="utf-8")

    containment = workflow.index("Contain Observation egress before build")
    build = workflow.index("Build immutable image")
    preflight = workflow.index("Run read-only Observation migration preflight")
    migration = workflow.index("Run additive migrations first")
    catalog = workflow.index("Sync taxonomy catalog and Expedition content before rebuilds")
    legacy = workflow.index("Reconcile legacy pending photos before consumers")
    rebuild = workflow.index("Rebuild adopted state before dispatcher replay")
    pin_jobs = workflow.index("Pin every Hinterland consumer and job")
    deploy_api = workflow.index("Deploy API after migrations")
    post_cutover = workflow.index("Close the old-revision compatibility race")
    assert containment < build < preflight < migration < catalog < legacy < rebuild
    assert rebuild < pin_jobs < deploy_api
    assert deploy_api < post_cutover
    assert "HINTERLAND_KID_JWKS_PATH" in workflow
    assert "HINTERLAND_INAT_CV_ENABLED=false" in workflow
    assert "HINTERLAND_INAT_SUBMIT_ENABLED=false" in workflow
    assert "remaining_inat_jobs" in workflow
    assert "HINTERLAND_INAT_OAUTH_TOKEN" in workflow
    assert "az eventgrid system-topic list" in workflow
    assert "EVENT_GRID_TOPIC" not in workflow
    assert "--include-inherited" in workflow
    assert "Azure Service Bus Data Owner" in workflow
    assert "for attempt in $(seq 1 12)" in workflow
    assert "gordi-pilot-rg" in workflow
    assert "< <(" not in workflow

    required_block = re.search(r"required_jobs=\(\s*(.*?)\s*\)", workflow, re.DOTALL)
    assert required_block is not None
    required_jobs = [line.strip() for line in required_block.group(1).splitlines()]
    assert required_jobs
    assert all(len(name) < 32 for name in required_jobs), required_jobs

    script = (_ROOT / "infra-azure/phase-9-observation-w1.sh").read_text(encoding="utf-8")
    provisioned_jobs = {
        f"hinterland-{suffix}"
        for suffix in re.findall(r'ensure_job "\$\{PREFIX\}-([a-z0-9-]+)"', script)
    }
    assert provisioned_jobs
    assert all(len(name) < 32 for name in provisioned_jobs), provisioned_jobs


def test_api_image_includes_every_runtime_authored_content_tree() -> None:
    dockerfile = (_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY content/expeditions/ ./content/expeditions/" in dockerfile
    assert "COPY content/sanctuary/ ./content/sanctuary/" in dockerfile
    assert "COPY content/taxa/ ./content/taxa/" in dockerfile
    assert "!content/expeditions/**" in dockerignore
    assert "!content/sanctuary/**" in dockerignore
    assert "!content/taxa/**" in dockerignore


def test_migration_registers_legacy_pending_work_before_relay() -> None:
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
