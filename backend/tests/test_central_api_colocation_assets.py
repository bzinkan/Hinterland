from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _ROOT / "infra-azure/api-central-colocation.ps1"


def _script() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def test_central_colocation_contract_is_explicit_and_fail_closed() -> None:
    script = _script()

    for required_parameter in (
        "$ExpectedSubscriptionId",
        "$ExpectedTenantId",
        "$ExpectedResourceGroup",
        "$Image",
        "$AppVersion",
    ):
        assert required_parameter in script

    assert "3ac5dfb0-91b7-47d3-8187-9dc8d6305e96" in script
    assert "18dbd7fa-c411-49bc-82fc-9ccaa26e3404" in script
    assert "$ContractResourceGroup = 'hinterland-dev-rg'" in script
    assert "Refusing to target gordi-pilot-rg." in script
    assert "^hinterlandacrdev\\.azurecr\\.io/hinterland-api@sha256:[0-9a-f]{64}$" in script
    assert "[ValidatePattern('^[0-9a-f]{40}$')]" in script
    assert "Set-StrictMode -Version Latest" in script
    assert "$ErrorActionPreference = 'Stop'" in script


def test_central_resources_and_placement_are_exact() -> None:
    script = _script()

    assert "$CentralLocation = 'centralus'" in script
    assert "$CentralEnvironmentName = 'hinterland-cae-central-dev'" in script
    assert "$CentralIdentityName = 'hinterland-api-central-mi'" in script
    assert "$CentralAppName = 'hinterland-api-central'" in script
    assert "$PostgresServerName = 'hinterland-postgres-dev'" in script
    assert "The Hinterland PostgreSQL server is not in Central US." in script
    assert "The Central Container Apps environment is not in Central US." in script
    assert "The Central API identity is not in Central US." in script
    assert "The Central API is not in Central US." in script
    assert "The Central API placement does not match" in script
    assert "--environment-mode', 'WorkloadProfiles'" in script
    assert "must use workload profiles" in script
    assert "--min-replicas', '0'" in script
    assert "runningState -ceq 'ScaledToZero'" in script
    assert "--max-replicas', '1'" in script
    assert script.count(") + $runtimeEnvironmentArguments") == 2
    assert "*.centralus.azurecontainerapps.io" in script
    assert "identity.type -cne 'UserAssigned'" in script
    assert "latest healthy revision" in script


def test_central_identity_mirrors_only_the_five_approved_roles() -> None:
    script = _script()
    approved_roles = (
        "AcrPull",
        "Key Vault Secrets User",
        "Storage Blob Data Contributor",
        "Azure Service Bus Data Sender",
        "Azure Service Bus Data Receiver",
    )

    assert "$SourceIdentityName = 'hinterland-api-mi'" in script
    assert "$roleMappings.Count 5" in script
    assert "$actualRoleKeys.Count -ne 5" in script
    assert "RBAC inventory must be exactly the five approved mirrored grants" in script
    assert "The source API identity does not hold the expected" in script
    assert "Ensure-MirroredRoleAssignment" in script
    for role in approved_roles:
        assert script.count(f"Role = '{role}'") == 1
    assert "Azure Service Bus Data Owner" not in script
    assert not re.search(r"Role\s*=\s*'Owner'", script)
    assert not re.search(r"Role\s*=\s*'Contributor'", script)


def test_central_runtime_uses_key_vault_and_w1_deny_defaults() -> None:
    script = _script()

    assert "$LogAnalyticsWorkspaceName = 'hinterland-law-dev'" in script
    assert "'workspace', 'show'" in script
    assert "'workspace', 'get-shared-keys'" in script
    assert "'workspace', 'create'" not in script
    assert "postgres-admin-password" in script
    assert "keyvaultref:" in script
    assert "identityref:" in script
    assert "HINTERLAND_DATABASE_PASSWORD               = 'secretref:pg-password'" in script

    expected_settings = {
        "HINTERLAND_MODERATION_PROVIDER": "noop",
        "HINTERLAND_INAT_CV_ENABLED": "false",
        "HINTERLAND_INAT_CV_DISCLOSURE_APPROVED": "false",
        "HINTERLAND_INAT_CV_BENCHMARK_APPROVED": "false",
        "HINTERLAND_INAT_SUBMIT_ENABLED": "false",
        "HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED": "true",
        "HINTERLAND_ORGANISM_FALLBACK_PROVIDER": "noop",
        "HINTERLAND_GEOCODING_PROVIDER": "noop",
        "HINTERLAND_DEV_AUTH_ENABLED": "false",
        "HINTERLAND_ALLOW_STUB_AUTH": "false",
    }
    for name, value in expected_settings.items():
        assert re.search(rf"{name}\s*= '{value}'", script)

    assert "The Central API environment variable inventory is not exact." in script
    assert "HINTERLAND_INAT_OAUTH_TOKEN" in script
    assert "HINTERLAND_DEV_AUTH_TOKEN" in script
    assert "The Central API must use only the PostgreSQL Key Vault secret reference." in script
    assert "Write-Host $workspace" not in script
    assert "Write-Host $postgresPasswordReference" not in script


def test_central_script_cannot_touch_jobs_dns_certificates_or_delete_resources() -> None:
    script = _script().lower()

    forbidden_commands = (
        "containerapp', 'job",
        "'dns'",
        "'hostname'",
        "'certificate'",
        "'delete'",
    )
    for command in forbidden_commands:
        assert command not in script

    assert "'containerapp', 'secret', 'remove'" in script
    assert ") + $retiredsecretnames" in script
    assert "properties.environmentid" in script
    assert "az @azarguments" in script
    assert "--only-show-errors" in script
    assert "--output none" in script
    assert "secret', 'show'" not in script
    assert "keyvault', 'secret'" not in script


def test_central_verification_is_digest_revision_and_readiness_specific() -> None:
    script = _script()

    assert "latestRevisionName" in script
    assert "latestReadyRevisionName" in script
    assert "properties.template.containers[0].image" in script
    assert "HINTERLAND_APP_VERSION" in script
    assert "provisioningState" in script
    assert "healthState" in script
    assert "runningState" in script
    assert 'Invoke-RestMethod -Uri "https://$fqdn/health"' in script
    assert 'Invoke-RestMethod -Uri "https://$fqdn/ready"' in script
    assert 'Invoke-RestMethod -Uri "https://$fqdn$KidJwksPath"' in script
    assert "The Central API kid JWKS is empty." in script
