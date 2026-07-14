from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _step_containing(workflow: str, marker: str, *, start_at: int = 0) -> tuple[str, int]:
    marker_index = workflow.index(marker, start_at)
    step_start = workflow.rfind("\n      - name:", 0, marker_index)
    assert step_start >= 0, f"{marker!r} is not inside a workflow step"
    step_end = workflow.find("\n      - name:", marker_index)
    if step_end < 0:
        step_end = len(workflow)
    return workflow[step_start:step_end], marker_index


def _assert_non_skippable_gate(step: str) -> None:
    assert "continue-on-error:" not in step
    assert "\n        if:" not in step
    assert "|| true" not in step
    assert "exit 0" not in step


def _origin_argument_lines(step: str) -> list[str]:
    return sorted(
        line.strip() for line in step.splitlines() if line.strip().startswith('--origin "')
    )


_PARENT_ORIGIN_ARGUMENTS = [
    '--origin "azure_swa=https://purple-coast-088e6b30f.7.azurestaticapps.net" \\',
    '--origin "public_parent=https://parents.thehinterlandguide.app" \\',
]


def test_w1_promotion_is_manual_protected_and_never_skips_auth() -> None:
    workflow = (_ROOT / ".github/workflows/observation-w1-promotion.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "name: w1-promotion" in workflow
    main_gate, main_call = _step_containing(workflow, "Require main for protected W1 promotion")
    assert 'test "${GITHUB_REF}" = "refs/heads/main"' in main_gate
    assert main_call < workflow.index("Initialize sanitized promotion evidence")
    _assert_non_skippable_gate(main_gate)
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
    rollout = workflow.index(
        "Require exact Central and East API revisions to be healthy and serving"
    )
    public_readiness = workflow.index("Smoke public readiness surfaces")
    browser_cors = workflow.index("Verify trusted parent browser CORS preflight")
    web_readiness = workflow.index("Verify exact W1 web deployments")
    authenticated = workflow.index("Non-skipped authenticated handoff and Observation canary")
    dispatcher_benchmark = workflow.index(
        "Require exact-revision deployed dispatcher p95 below 300 ms"
    )
    final_runtime = workflow.index("Assert final W1 runtime, queue, and digest state")
    restore_strict_modes = workflow.index("Restore temporary strict job modes")
    final_web_recheck = workflow.index("Recheck exact W1 web deployments before final evidence")
    finalize_evidence = workflow.index("Finalize sanitized evidence")
    publish_evidence = workflow.index("Publish promotion evidence")

    assert (
        preflight
        < migrate
        < pin_all
        < catalog
        < expeditions
        < rebuild
        < web_readiness
        < deploy
        < rollout
        < public_readiness
        < browser_cors
        < authenticated
        < dispatcher_benchmark
        < final_runtime
        < restore_strict_modes
        < final_web_recheck
        < finalize_evidence
        < publish_evidence
    )
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
    assert "AZURE_CONTAINER_APP: hinterland-api-central" in workflow
    assert "AZURE_CONTAINER_APP_ENV: hinterland-cae-central-dev" in workflow
    assert "AZURE_UAMI_NAME: hinterland-api-central-mi" in workflow
    assert "AZURE_ROLLBACK_CONTAINER_APP: hinterland-api" in workflow
    assert "AZURE_ROLLBACK_CONTAINER_APP_ENV: hinterland-cae-dev" in workflow
    assert "AZURE_ROLLBACK_UAMI_NAME: hinterland-api-mi" in workflow
    assert 'test "$location" = "${AZURE_JOB_LOCATION_EXPECTED}"' in workflow
    assert 'test "${environment##*/}" = "${AZURE_ROLLBACK_CONTAINER_APP_ENV}"' in workflow
    assert 'ready_revision="$(wait_for_exact_revision "${AZURE_CONTAINER_APP}")"' in workflow
    assert '"${AZURE_ROLLBACK_CONTAINER_APP}")"' in workflow
    assert "W1_ROLLBACK_READY_REVISION" in workflow
    assert "W1_ROLLBACK_API_BASE_URL" in workflow
    assert 'test "$rollback_api_image" = "$IMAGE"' in workflow
    assert 'assert_only_runtime_identity job "$job" "$rollback_uami_id"' in workflow
    assert "dig +short CNAME api.thehinterlandguide.app" in workflow
    assert "dig +short A api.thehinterlandguide.app | sort -u" in workflow
    assert 'test "${#public_ips[@]}" = 1' in workflow
    assert 'test "$rollback_domain_count" = 1' in workflow
    assert 'test "$public_ip" = "$primary_static_ip"' in workflow
    assert 'test "$primary_domain_count" = 1' in workflow
    assert ".dns={hostname:$hostname, public_ip:$public_ip," in workflow
    benchmark_step, _ = _step_containing(
        workflow, "Require exact-revision deployed dispatcher p95 below 300 ms"
    )
    _assert_non_skippable_gate(benchmark_step)
    assert "verify_deployed_dispatcher_benchmark.py" in benchmark_step
    assert '--expected-revision "$W1_API_READY_REVISION"' in benchmark_step
    assert '--expected-image "$IMAGE"' in benchmark_step
    assert "--threshold-ms 300" in benchmark_step
    assert "--timeout-seconds 900" in benchmark_step
    assert "AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}" in benchmark_step
    assert "AZURE_TENANT_ID: ${{ secrets.AZURE_TENANT_ID }}" in benchmark_step
    assert (
        "backend/.venv/bin/python scripts/verify_deployed_dispatcher_benchmark.py" in benchmark_step
    )
    assert "get-access-token" not in benchmark_step
    assert "--log-token-stdin" not in benchmark_step
    assert "log_analytics_token" not in benchmark_step
    assert "AZURE_CREDENTIALS" not in benchmark_step
    assert "id-token: write" in workflow
    assert "del(.observation_ids)" in workflow
    assert workflow.count("--slurpfile benchmark") >= 2
    assert 'HINTERLAND_DISPATCHER_BENCHMARK_SAMPLES: "50"' in workflow
    for explicit_setting in (
        "AZURE_CLIENT_ID",
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
    assert workflow.count('AZURE_CLIENT_ID="${job_uami_client_id}"') >= 2
    assert workflow.count('--arg expected_client "$job_uami_client_id"') >= 2
    assert workflow.count('.identity.type == "UserAssigned"') >= 2
    assert workflow.count('select(.name == "AZURE_CLIENT_ID")') >= 2
    assert workflow.count('== [$expected_client]') >= 2
    assert (
        'assert_env app "${AZURE_CONTAINER_APP}" AZURE_CLIENT_ID "$uami_client_id"'
        in workflow
    )
    assert (
        'assert_env app "${AZURE_ROLLBACK_CONTAINER_APP}" AZURE_CLIENT_ID \\\n            "$rollback_uami_client_id"'
        in workflow
    )
    assert 'assert_env job "$job" AZURE_CLIENT_ID "$rollback_uami_client_id"' in workflow
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
    assert "properties.latestRevisionName" in workflow
    assert "properties.latestReadyRevisionName" in workflow
    assert "activeRevisionsMode" in workflow
    assert "any(.latestRevision == true and .weight == 100)" in workflow
    assert "HINTERLAND_APP_VERSION" in workflow
    assert '"$revision_image" == "$IMAGE"' in workflow
    assert '"$app_version" == "$GITHUB_SHA"' in workflow
    assert '"${W1_API_READY_REVISION:-}"' in workflow
    assert '.status == "ok" and .version == $version' in workflow
    assert '.status == "ready" and .version == $version' in workflow
    assert '"https://parents.thehinterlandguide.app"' in workflow
    assert '"https://purple-coast-088e6b30f.7.azurestaticapps.net"' in workflow
    assert "Access-Control-Request-Method: POST" in workflow
    assert "Access-Control-Request-Headers: content-type" in workflow
    assert '"${base_url}/v1/auth/consent"' in workflow
    assert "access-control-allow-origin: ${origin}" in workflow
    assert "access-control-allow-credentials: true" in workflow
    assert 'untrusted_origin="https://example.invalid"' in workflow
    assert '.cors_preflight={result:"passed"' in workflow
    assert 'test "$probe_count" = 6' in workflow
    assert 'test "$rejected_probe_count" = 3' in workflow
    assert "Access-Control-Allow-Origin: *" not in workflow
    assert workflow.count("/.well-known/hinterland-build.json?sha={expected}") == 2
    assert workflow.count('("parents", "https://parents.thehinterlandguide.app")') == 2
    assert workflow.count('("landing", "https://thehinterlandguide.app")') == 2
    assert workflow.count('("landing", "https://www.thehinterlandguide.app")') == 2


def test_parent_auth_callback_contract_and_release_gates_are_fail_closed() -> None:
    config_path = _ROOT / "mobile/public/staticwebapp.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    callback_routes = [
        route for route in config.get("routes", []) if route.get("route") == "/auth/callback"
    ]

    assert callback_routes == [
        {
            "route": "/auth/callback",
            "methods": ["GET"],
            "rewrite": "/auth/callback.html",
            "headers": {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        }
    ]
    assert "navigationFallback" not in json.dumps(config)

    ci = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ci_gate, _ = _step_containing(ci, "npm run config:parent-web")
    _assert_non_skippable_gate(ci_gate)

    parent_deploy = (_ROOT / ".github/workflows/deploy-parents-swa.yml").read_text(encoding="utf-8")
    main_gate, main_call = _step_containing(
        parent_deploy, "Require main for live parent deployment"
    )
    export = parent_deploy.index("npx expo export --platform web")
    export_gate = parent_deploy.index("npm run config:parent-web -- --dist")
    deploy = parent_deploy.index("Azure/static-web-apps-deploy@v1")
    assert main_call < export < export_gate < deploy
    assert 'test "${GITHUB_REF}" = "refs/heads/main"' in main_gate
    _assert_non_skippable_gate(main_gate)
    deploy_gate, _ = _step_containing(parent_deploy, "npm run config:parent-web -- --dist")
    _assert_non_skippable_gate(deploy_gate)
    live_gate, live_call = _step_containing(
        parent_deploy, 'python "${GITHUB_WORKSPACE}/scripts/verify_parent_callback.py"'
    )
    assert deploy < live_call
    assert parent_deploy.count("scripts/verify_parent_callback.py") == 1
    assert "--expected-html" in live_gate
    assert _origin_argument_lines(live_gate) == _PARENT_ORIGIN_ARGUMENTS
    _assert_non_skippable_gate(live_gate)

    promotion = (_ROOT / ".github/workflows/observation-w1-promotion.yml").read_text(
        encoding="utf-8"
    )
    web_readiness = promotion.index("Verify exact W1 web deployments")
    api_rollout = promotion.index("Deploy the API only after migrations and rebuild")
    final_web_recheck = promotion.index("Recheck exact W1 web deployments before final evidence")
    finalize_evidence = promotion.index("Finalize sanitized evidence")

    initial_gate, initial_call = _step_containing(
        promotion, "python scripts/verify_parent_callback.py"
    )
    final_gate, final_call = _step_containing(
        promotion,
        "python scripts/verify_parent_callback.py",
        start_at=initial_call + 1,
    )
    assert promotion.count("python scripts/verify_parent_callback.py") == 2
    assert web_readiness < initial_call < api_rollout
    assert final_web_recheck < final_call < finalize_evidence

    for gate in (initial_gate, final_gate):
        assert "--expected-sha" in gate
        assert "GITHUB_SHA" in gate
        assert _origin_argument_lines(gate) == _PARENT_ORIGIN_ARGUMENTS
        _assert_non_skippable_gate(gate)


def test_static_web_deployments_are_bound_to_current_resources() -> None:
    parents = (_ROOT / ".github/workflows/deploy-parents-swa.yml").read_text(encoding="utf-8")
    landing = (_ROOT / ".github/workflows/deploy-landing-swa.yml").read_text(encoding="utf-8")

    landing_lines = set(landing.splitlines())

    assert "HINTERLAND_PARENTS_SWA_TOKEN" in parents
    assert "AZURE_PARENTS_SWA_TOKEN" not in parents
    assert _origin_argument_lines(parents) == _PARENT_ORIGIN_ARGUMENTS
    assert '"surface": "parents"' in parents

    assert "HINTERLAND_LANDING_SWA_TOKEN" in landing
    assert "AZURE_LANDING_SWA_TOKEN" not in landing
    assert (
        '              "current Azure resource": '
        '"https://polite-grass-042f5da0f.7.azurestaticapps.net",' in landing_lines
    )
    assert '              "public apex domain": "https://thehinterlandguide.app",' in landing_lines
    assert (
        '              "public www domain": "https://www.thehinterlandguide.app",' in landing_lines
    )
    assert '"surface": "landing"' in landing


def test_w1_parent_disclosures_are_private_and_truthful() -> None:
    consent = (_ROOT / "mobile/app/consent.tsx").read_text(encoding="utf-8")
    privacy = (_ROOT / "web/public/privacy.html").read_text(encoding="utf-8")
    normalized_consent = " ".join(consent.split())

    for stale_claim in (
        "Photos and species IDs become public scientific observations",
        "automatic suggester",
        "rounded to ~city block",
    ):
        assert stale_claim not in normalized_consent

    for required in (
        "server-hosted W1 private-pilot photo bytes",
        "purged after seven days",
        "Unsynced work",
        "send photos to iNaturalist",
        "publish observations",
        "random temporary setup proof",
        "stores only its SHA-256 digest",
        "Receipt: {phase.receiptId}",
    ):
        assert required in normalized_consent

    assert "iNaturalist public submission and photo-identification suggestions are" in privacy
    assert "disabled for the W1 Android Internal Testing pilot" in privacy
    assert "W1 uses a NoOp moderation provider" in privacy
    assert "it is not a safety approval" in privacy

    consent_policy = (_ROOT / "backend/app/core/parent_consent.py").read_text(encoding="utf-8")
    consent_proof = (_ROOT / "mobile/src/auth/consentProof.ts").read_text(encoding="utf-8")
    assert '"2026-07-11-W1-INTERNAL"' in consent_policy
    assert '"2026-07-11-W1-INTERNAL"' in consent_proof
    assert "globalThis.sessionStorage" in consent_proof
    assert "globalThis.crypto" in consent_proof

    msal = (_ROOT / "mobile/src/auth/msal.ts").read_text(encoding="utf-8")
    assert 'prompt: "select_account"' in msal
    assert "await ms.logoutRedirect();" in msal
    assert "logoutRedirect({ account" not in msal


def test_parent_smoke_passes_throwaway_kid_session_in_memory() -> None:
    parent_smoke = (_ROOT / "scripts/smoke_azure_parent_kid.py").read_text(encoding="utf-8")
    observation_smoke = (_ROOT / "scripts/smoke_observation_w1.py").read_text(encoding="utf-8")

    assert "run_canary(base_url=base_url, bearer=kid_session_token)" in parent_smoke
    assert "run_dispatcher_benchmark(" in parent_smoke
    assert "bearer=kid_session_token" in parent_smoke
    assert 'payload.get("id") != parent_user_id' in parent_smoke
    assert 'payload.get("display_name") != parent_name' in parent_smoke
    assert 'payload.get("id") != kid_user_id' in parent_smoke
    assert 'payload.get("display_name") != kid_name' in parent_smoke
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
    assert "MODERATION_NAMESPACE_ID=" in monitoring
    assert "MODERATION_QUEUE_ID=" not in monitoring
    assert '--scopes "$MODERATION_NAMESPACE_ID"' in monitoring
    assert "avg ActiveMessages > 25 where EntityName includes ${MODERATION_QUEUE}" in monitoring
    assert (
        "avg DeadletteredMessages > 0 where EntityName includes ${MODERATION_QUEUE}" in monitoring
    )
    assert 'expected_metric="ActiveMessages"' in monitoring
    assert 'expected_metric="DeadletteredMessages"' in monitoring
    assert ".criteria.allOf" in monitoring
    assert '.name == "EntityName"' in monitoring
    assert '.operator == "Include"' in monitoring
    assert "HINTERLAND_ALERT_EMAIL is required by --synthetic" in monitoring
    assert "synthetic notification has no uniquely enabled protected receiver" in monitoring
    assert "--add-action email" in monitoring
    assert "usecommonalertschema" in monitoring
    assert "--no-wait" in monitoring
