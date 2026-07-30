from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _ROOT / "infra-azure/environments/hinterland-qa.env"
_PREFLIGHT_PATH = _ROOT / "infra-azure/adult-qa-preflight.ps1"
_TOKEN_PATH = _ROOT / "scripts/set-adult-qa-inat-cv-token.ps1"
_WORKFLOW_PATH = _ROOT / ".github/workflows/adult-qa-promotion.yml"
_QUALIFICATION_WORKFLOW_PATH = (
    _ROOT / ".github/workflows/adult-qa-cv-qualification.yml"
)
_QUALIFICATION_CLEANUP_PATH = (
    _ROOT / "infra-azure/adult-qa-cv-qualification-cleanup.sh"
)
_ADR_PATH = _ROOT / "docs/adr/0018-isolated-adult-qa-environment.md"
_POLICY_PATH = _ROOT / "infra-azure/policies/adult-qa-lifecycle.json"


def _pwsh() -> str:
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell 7 is required by the Azure operational contract")
    return executable


def _run_preflight(
    *,
    environment_file: Path = _ENV_PATH,
    mode: str = "Preview",
    confirmation: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        _pwsh(),
        "-NoProfile",
        "-File",
        str(_PREFLIGHT_PATH),
        "-Mode",
        mode,
        "-EnvironmentFile",
        str(environment_file),
        "-SkipAzureReads",
    ]
    if confirmation is not None:
        command.extend(["-Confirmation", confirmation])
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )


def test_adult_qa_environment_is_isolated_and_contains_no_credentials() -> None:
    contract = _ENV_PATH.read_text(encoding="utf-8")

    expected = (
        'ENVIRONMENT="adult-qa"',
        'HINTERLAND_RELEASE_LANE="adult_qa"',
        'HINTERLAND_RESOURCE_GROUP="hinterland-qa-rg"',
        'HINTERLAND_DATABASE_NAME="hinterland_qa"',
        'HINTERLAND_DATABASE_USER="hinterland_qa_app"',
        'HINTERLAND_KEY_VAULT_NAME="hinterland-kv-qa"',
        'HINTERLAND_STORAGE_ACCOUNT="hinterlandphotosqa"',
        'HINTERLAND_SERVICE_BUS_NAMESPACE="hinterland-sb-qa"',
        'HINTERLAND_MODERATION_QUEUE="moderation-pending-qa"',
        'HINTERLAND_MODERATION_PROVIDER="azure_content_safety"',
        'HINTERLAND_INAT_SUBMIT_ENABLED="false"',
        'HINTERLAND_DEV_AUTH_ENABLED="false"',
    )
    for value in expected:
        assert value in contract

    assert "HINTERLAND_DATABASE_PASSWORD=" not in contract
    assert "HINTERLAND_INAT_CV_TOKEN=" not in contract
    assert "HINTERLAND_INAT_SUBMIT_TOKEN=" not in contract
    assert "CLIENT_SECRET" not in contract


def test_preflight_is_read_only_and_requires_explicit_cost_approval() -> None:
    script = _PREFLIGHT_PATH.read_text(encoding="utf-8")
    lowered = script.lower()

    assert "$ContractApprovalPhrase = 'APPROVE_HINTERLAND_ADULT_QA_COSTS'" in script
    assert "$ContractResourceGroup = 'hinterland-qa-rg'" in script
    assert "'hinterland-dev-rg', 'gordi-pilot-rg'" in script
    assert "'hinterland', 'postgres', 'azure_maintenance'" in script
    assert "'hinterland-kv-dev'" in script
    assert "'hinterlandphotosdev'" in script
    assert "'hinterland-sb-dev'" in script
    assert "public_inaturalist_submission = 'disabled'" in script

    for forbidden in (
        "az group create",
        "az resource create",
        "az containerapp create",
        "az keyvault create",
        "az storage account create",
        "az servicebus namespace create",
        "az cognitiveservices account create",
        "az postgres flexible-server create",
        "az role assignment create",
    ):
        assert forbidden not in lowered

    preview = _run_preflight()
    assert preview.returncode == 0, preview.stderr
    payload = json.loads(preview.stdout)
    assert payload["result"] == "passed"
    assert payload["release_lane"] == "adult_qa"
    assert payload["provisioning_approval"]["present"] is False
    assert payload["cost_preview"]["monthly_min"] == 26
    assert payload["cost_preview"]["monthly_max"] == 115

    denied = _run_preflight(mode="ProvisionReadiness")
    assert denied.returncode != 0
    assert "Billable provisioning is not approved" in denied.stderr

    approved = _run_preflight(
        mode="ProvisionReadiness",
        confirmation="APPROVE_HINTERLAND_ADULT_QA_COSTS",
    )
    assert approved.returncode == 0, approved.stderr
    approved_payload = json.loads(approved.stdout)
    approval = approved_payload["provisioning_approval"]
    assert approval["present"] is True
    assert len(approval["approval_id"]) == 32
    assert len(approval["operator_identity_sha256"]) == 64


def test_preflight_rejects_a_w1_target_even_without_azure_access(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.env"
    unsafe.write_text(
        _ENV_PATH.read_text(encoding="utf-8").replace(
            'HINTERLAND_RESOURCE_GROUP="hinterland-qa-rg"',
            'HINTERLAND_RESOURCE_GROUP="hinterland-dev-rg"',
        ),
        encoding="utf-8",
    )

    result = _run_preflight(environment_file=unsafe)
    assert result.returncode != 0
    assert "HINTERLAND_RESOURCE_GROUP must be exactly 'hinterland-qa-rg'" in result.stderr


def test_cv_token_operation_is_qa_only_and_never_uses_a_temp_file() -> None:
    script = _TOKEN_PATH.read_text(encoding="utf-8")
    lowered = script.lower()

    assert "$ExpectedResourceGroup = 'hinterland-qa-rg'" in script
    assert "$ExpectedVaultName = 'hinterland-kv-qa'" in script
    assert "[ValidatePattern('^inat-cv-token$')]" in script
    assert "Read-Host" in script
    assert "-AsSecureString" in script
    assert "https://vault.azure.net" in script
    assert "Invoke-RestMethod" in script
    assert "'public-submit' = 'disabled'" in script
    assert "remaining_seconds_at_rotation" in script
    assert "ZeroFreeBSTR" in script
    assert "hinterland-kv-dev" in script
    assert "gordi-pilot-rg" in script

    assert "mktemp" not in lowered
    assert "get-clipboard" not in lowered
    assert "set-clipboard" not in lowered
    assert "--file" not in lowered
    assert "keyvault secret set" not in lowered
    assert "containerapp update" not in lowered
    assert "write-host $token" not in lowered
    assert not (_ROOT / "scripts/refresh-inat-token.sh").exists()


def test_promotion_is_protected_ordered_digest_pinned_and_submit_denied() -> None:
    workflow = _WORKFLOW_PATH.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert "name: adult-qa-promotion" in lowered
    assert "group: adult-qa-runtime-mutation" in lowered
    assert "PROMOTE_ADULT_QA" in workflow
    assert "HINTERLAND_QA_COST_APPROVAL_ID" in workflow
    assert "hinterland-qa-rg" in workflow
    assert "gordi-pilot-rg" in workflow
    assert "hinterland_qa" in workflow
    assert "qa-api.thehinterlandguide.app" in workflow
    assert "qa-parents.thehinterlandguide.app" in workflow
    assert "HINTERLAND_RELEASE_LANE=adult_qa" in workflow
    assert "HINTERLAND_DEPLOYMENT_IMAGE_DIGEST" in workflow
    assert workflow.count('AZURE_CLIENT_ID="${jobs_identity_client_id}"') == 2
    assert 'AZURE_CLIENT_ID="${identity_client_id}"' in workflow
    assert 'select(.name == $name)] | length' in workflow
    assert "HINTERLAND_DATABASE_HOST=\"${AZURE_POSTGRES_HOST}\"" in workflow
    assert "HINTERLAND_DATABASE_PASSWORD=secretref:pg-password" in workflow
    assert "HINTERLAND_PHOTOS_BUCKET=\"${AZURE_PHOTOS_CONTAINER}\"" in workflow
    assert "HINTERLAND_SERVICE_BUS_NAMESPACE=" in workflow
    assert "HINTERLAND_KEY_VAULT_URL=\"${QA_KEY_VAULT_URL}\"" in workflow
    assert "HINTERLAND_ENTRA_API_AUDIENCE=\"${QA_ENTRA_API_AUDIENCE}\"" in workflow
    assert "HINTERLAND_KID_JWT_AUDIENCE=\"${QA_KID_JWT_AUDIENCE}\"" in workflow
    assert "HINTERLAND_CORS_ORIGINS=\"${QA_CORS_ORIGINS}\"" in workflow
    assert "HINTERLAND_SERVICE_BUS_INAT_QUEUE=\"${AZURE_DISABLED_INAT_QUEUE}\"" in workflow
    assert "HINTERLAND_MODERATION_PROVIDER=azure_content_safety" in workflow
    assert "HINTERLAND_MODERATION_PROVIDER=noop" in workflow
    assert "HINTERLAND_INAT_CV_TOKEN=secretref:inat-cv-token" in workflow
    assert "HINTERLAND_INAT_SUBMIT_ENABLED=false" in workflow
    assert "inat-submit-token|inat-oauth-token" in workflow
    assert "HINTERLAND_QA_SMOKE_ENTRA_BEARER" in workflow
    assert "smoke_adult_qa_parent_kid.py" in workflow
    assert "cv_qualification_enabled == false" in workflow
    assert "HINTERLAND_QA_CV_APPROVED_RUN_ID" in workflow
    assert "HINTERLAND_QA_CV_APPROVED_EVIDENCE_SHA256" in workflow
    assert "adult-qa-promotion-${{ github.run_id }}" in workflow
    assert "Bind the protected parent deployment token" in workflow
    assert "Prove the QA database reached the exact Alembic head" in workflow
    assert "assert_only_identity" in workflow
    assert "assert_secret_ref" in workflow
    assert "if: always()" in workflow

    migration = workflow.index(
        "- name: Run read-only preflight and additive migrations first"
    )
    consumers = workflow.index(
        "- name: Pin every worker and scheduled job after migration succeeds"
    )
    api = workflow.index(
        "- name: Deploy API last with controlled CV and no public submit"
    )
    ready = workflow.index("- name: Wait for exact healthy API revision")
    secret_cleanup = workflow.index("forbidden_app_secrets=(", ready)
    assert migration < consumers < api
    assert api < ready < secret_cleanup
    assert "az containerapp secret remove" not in workflow[api:ready]

    for forbidden in (
        "az group create",
        "az postgres flexible-server create",
        "az keyvault create",
        "az storage account create",
        "az servicebus namespace create",
        "az cognitiveservices account create",
        "az identity create",
        "az containerapp env create",
    ):
        assert forbidden not in lowered


def test_cv_qualification_is_bounded_same_digest_and_self_disabling() -> None:
    workflow = _QUALIFICATION_WORKFLOW_PATH.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert "name: adult qa cv qualification" in lowered
    assert "name: adult-qa-promotion" in lowered
    assert "group: adult-qa-runtime-mutation" in lowered
    assert "QUALIFY_ADULT_QA_CV" in workflow
    assert "HINTERLAND_QA_CV_BENCHMARK_MANIFEST_B64" in workflow
    assert "HINTERLAND_QA_BENCHMARK_BEARER" in workflow
    assert "HINTERLAND_QA_CV_QUALIFICATION_KEY" in workflow
    assert "${{ secrets.HINTERLAND_QA_CV_QUALIFICATION_KEY }}" not in workflow
    assert 'AZURE_CLIENT_ID="${api_identity_client_id}"' in workflow
    assert 'AZURE_CLIENT_ID="${identity_client_id}"' in workflow
    assert "HINTERLAND_DEPLOYMENT_IMAGE_DIGEST" in workflow
    assert "scripts/qa_inat_cv_benchmark.py" in workflow
    assert ".case_count == 50" in workflow
    assert ".unique_photo_hash_count == 50" in workflow
    assert ".provider_call_count == 50" in workflow
    assert ".top_three_accuracy >= 0.70" in workflow
    assert "HINTERLAND_INAT_SUBMIT_ENABLED=false" in workflow
    assert (
        "HINTERLAND_INAT_CV_QUALIFICATION_ENABLED=false"
        in _QUALIFICATION_CLEANUP_PATH.read_text(encoding="utf-8")
    )
    assert "--remove-env-vars" in workflow
    assert "--secret-names" in _QUALIFICATION_CLEANUP_PATH.read_text(
        encoding="utf-8"
    )
    assert "Destroy the private manifest copy" in workflow
    assert 'cron: "17 * * * *"' in workflow
    assert "sweep-expired-qualification" in workflow
    assert "adult-qa-cv-qualification-cleanup.sh --expired-only" in workflow
    assert "adult-qa-cv-qualification-${{ github.run_id }}" in workflow
    assert "if: always()" in workflow

    for forbidden in (
        "inat-submit-token=secretref",
        "hinterland-dev-rg",
        "gordi-pilot-rg",
        "print(hinterland_qa_cv_qualification_key",
        "echo \"${hinterland_qa_cv_qualification_key}\"",
    ):
        assert forbidden not in lowered


def test_cv_qualification_cleanup_is_qa_only_expiry_aware_and_fail_closed() -> None:
    cleanup = _QUALIFICATION_CLEANUP_PATH.read_text(encoding="utf-8")
    lowered = cleanup.lower()

    assert "resource_group=hinterland-qa-rg" in cleanup
    assert "container_app=hinterland-api-qa" in cleanup
    assert "key_vault=hinterland-kv-qa" in cleanup
    assert "--expired-only" in cleanup
    assert "qualification_residue" in cleanup
    assert "normal promoted CV token is intentionally left alone" in cleanup
    assert "read_app_json" in cleanup
    assert "read_app_secrets" in cleanup
    assert "read_key_vault_secrets" in cleanup
    assert "read_meta" in cleanup
    assert "latestRevisionName" in cleanup
    assert "latestReadyRevisionName" in cleanup
    assert "for secret in inat-cv-token inat-cv-qualification-key" in cleanup
    assert "--secret-names \"${secret}\" --output none" in cleanup
    assert "key_vault_secret_is_retired" in cleanup
    assert "az keyvault secret delete" in cleanup
    assert "az keyvault secret set-attributes" in cleanup
    assert "--enabled false" in cleanup
    assert "key_vault_secret_retired=true" in cleanup
    assert "cv_qualification_enabled == false" in cleanup
    assert "public_inat_submission_enabled == false" in cleanup
    assert "fail-closed posture" in cleanup
    assert "|| echo '{}'" not in cleanup
    assert "|| echo '[]'" not in cleanup
    assert "hinterland-dev-rg" not in lowered
    assert "gordi-pilot-rg" not in lowered


def test_lifecycle_and_adr_capture_the_adult_qa_privacy_boundary() -> None:
    policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    names = {rule["name"] for rule in policy["rules"]}
    assert names == {
        "adult-qa-unattached-upload-24h",
        "adult-qa-quarantine-rejected-90d",
    }
    prefixes = {
        prefix
        for rule in policy["rules"]
        for prefix in rule["definition"]["filters"]["prefixMatch"]
    }
    assert "qa-photos/pending/uploads/" in prefixes
    assert "qa-photos/quarantine/" in prefixes
    assert "qa-photos/rejected/" in prefixes
    assert all(prefix.startswith("qa-photos/") for prefix in prefixes)

    adr = _ADR_PATH.read_text(encoding="utf-8")
    assert "synthetic child profiles" in adr
    assert "adult-owned, non-sensitive photos" in adr
    assert "hinterland_qa" in adr
    assert "Public iNaturalist submission remains disabled" in adr
    assert "moderation result never" in adr
    assert "APPROVE_HINTERLAND_ADULT_QA_COSTS" in adr
