from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_w1_promotion_is_manual_protected_and_never_skips_auth() -> None:
    workflow = (_ROOT / ".github/workflows/observation-w1-promotion.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "name: w1-promotion" in workflow
    assert "HINTERLAND_SMOKE_ENTRA_BEARER" in workflow
    assert "authenticated checks never skip" in workflow
    assert "HINTERLAND_SMOKE_KID_BEARER" not in workflow
    assert "skipping" not in workflow.lower()
    assert "uv sync --frozen --no-dev" in workflow
    assert "pip install ./backend" not in workflow
    assert "actions/upload-artifact@v4" in workflow


def test_w1_promotion_order_and_containment_are_explicit() -> None:
    workflow = (_ROOT / ".github/workflows/observation-w1-promotion.yml").read_text(
        encoding="utf-8"
    )

    preflight = workflow.index("run_job hinterland-obs-preflight")
    migrate = workflow.index("run_job hinterland-migrate")
    pin_all = workflow.index("Pin every consumer and job only after migration success")
    catalog = workflow.index("run_job hinterland-taxa-catalog-ingest")
    expeditions = workflow.index("run_job hinterland-sync-expeditions")
    rebuild = workflow.index("run_job hinterland-state-rebuild")
    deploy = workflow.index("Deploy the API only after migrations and rebuild")
    authenticated = workflow.index("Non-skipped authenticated handoff and Observation canary")

    assert preflight < migrate < pin_all < catalog < expeditions < rebuild < deploy < authenticated
    assert "HINTERLAND_MODERATION_PROVIDER=noop" in workflow
    assert "HINTERLAND_INAT_CV_ENABLED=false" in workflow
    assert "HINTERLAND_INAT_CV_DISCLOSURE_APPROVED=false" in workflow
    assert "HINTERLAND_INAT_CV_BENCHMARK_APPROVED=false" in workflow
    assert "HINTERLAND_INAT_SUBMIT_ENABLED=false" in workflow
    assert "ENTRA_API_AUDIENCE: 7dd9da3c-b7d6-45d4-955b-d7561c43f209" in workflow
    assert "ENTRA_CLIENT_APP_ID: 60504e4c-6b5f-4031-a80a-3e4bdfae29b2" in workflow
    assert 'claims.get("aud") != os.environ["ENTRA_API_AUDIENCE"]' in workflow
    assert 'claims.get("azp") != os.environ["ENTRA_CLIENT_APP_ID"]' in workflow
    assert 'claims.get("ver") != "2.0"' in workflow
    assert '"user.access" not in scopes.split()' in workflow
    assert workflow.count('HINTERLAND_ENTRA_API_AUDIENCE="${ENTRA_API_AUDIENCE}"') >= 7
    assert "HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED=true" in workflow
    for explicit_setting in (
        "HINTERLAND_MODERATION_PROVIDER",
        "HINTERLAND_INAT_CV_ENABLED",
        "HINTERLAND_INAT_CV_DISCLOSURE_APPROVED",
        "HINTERLAND_INAT_CV_BENCHMARK_APPROVED",
        "HINTERLAND_INAT_SUBMIT_ENABLED",
        "HINTERLAND_ENTRA_API_AUDIENCE",
        "HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED",
        "HINTERLAND_DATABASE_PASSWORD",
        "HINTERLAND_APP_VERSION",
    ):
        assert workflow.count(f'select(.name != "{explicit_setting}")') >= 2
    assert "Microsoft.Storage.BlobCreated" in workflow
    assert "--query '[].[name,resourceGroup]'" in workflow
    assert 'resource-group "$topic_resource_group"' in workflow
    assert "az eventgrid event-subscription list" in workflow
    assert "az eventgrid event-subscription delete" in workflow
    assert workflow.count('--source-resource-id "$storage_id"') >= 2
    assert workflow.count('contains("/blobs/pending/")') >= 2
    assert "--include-inherited" in workflow
    assert "assert_only_runtime_identity" in workflow
    assert "verify_observation_postgres.ps1" in workflow
    assert "HINTERLAND_DERIVED_REBUILD_STRICT_DRAIN=true" in workflow
    assert "gordi-pilot-rg" in workflow


def test_parent_smoke_passes_throwaway_kid_session_in_memory() -> None:
    parent_smoke = (_ROOT / "scripts/smoke_azure_parent_kid.py").read_text(encoding="utf-8")
    observation_smoke = (_ROOT / "scripts/smoke_observation_w1.py").read_text(encoding="utf-8")

    assert "run_canary(base_url=base_url, bearer=kid_session_token)" in parent_smoke
    assert "HINTERLAND_SMOKE_KID_BEARER" not in parent_smoke
    assert 'bearer=_required("HINTERLAND_SMOKE_BEARER")' in observation_smoke
    assert (
        'reservation["upload_url"]'
        not in observation_smoke.split("def _write_evidence", maxsplit=1)[1].split(
            "def run_canary", maxsplit=1
        )[0]
    )
    assert "response.text" not in observation_smoke
    assert "response.request.url}" not in observation_smoke
    assert '"moderation_status"' in observation_smoke
    assert 'print(f"  body:' not in parent_smoke


def test_monitoring_artifact_covers_w1_and_revocation_signals() -> None:
    monitoring = (_ROOT / "infra-azure/observation-w1-monitoring.sh").read_text(encoding="utf-8")

    for alert in (
        "moderation-queue-depth",
        "moderation-dlq",
        "observation-work-age",
        "rebuild-backlog-failure",
        "dispatcher-backlog",
        "dispatcher-p95",
        "observation-idempotency-conflicts",
        "observation-state-mismatch",
        "photo-revocation-failure",
        "observation-job-failures",
    ):
        assert alert in monitoring
    assert "test-notifications create" in monitoring
    assert "stale_photo_revocations" in monitoring
    assert "failed_photo_revocations" in monitoring
    assert "monitor metrics alert delete" in monitoring
    assert "monitor scheduled-query delete" in monitoring
    assert "protected alert receiver is not enabled" in monitoring
    assert "gordi-pilot-rg" in monitoring
