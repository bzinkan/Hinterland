[CmdletBinding()]
param(
    [ValidateSet('Preview', 'ProvisionReadiness', 'PromotionReadiness')]
    [string] $Mode = 'Preview',

    [string] $EnvironmentFile = (Join-Path $PSScriptRoot 'environments/hinterland-qa.env'),

    [string] $CostFile = (Join-Path $PSScriptRoot 'cost/adult-qa-monthly-estimate.json'),

    [string] $OutputPath = '',

    [string] $Confirmation = '',

    [switch] $SkipAzureReads
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ContractSubscriptionId = '3ac5dfb0-91b7-47d3-8187-9dc8d6305e96'
$ContractTenantId = '18dbd7fa-c411-49bc-82fc-9ccaa26e3404'
$ContractResourceGroup = 'hinterland-qa-rg'
$ContractSharedResourceGroup = 'hinterland-dev-rg'
$ContractApprovalPhrase = 'APPROVE_HINTERLAND_ADULT_QA_COSTS'

$ForbiddenResourceGroups = @('hinterland-dev-rg', 'gordi-pilot-rg')
$ForbiddenDatabaseNames = @('hinterland', 'postgres', 'azure_maintenance')
$ForbiddenDatabaseUsers = @('hadmin', 'postgres')
$ForbiddenResourceNames = @(
    'hinterland-kv-dev',
    'hinterlandphotosdev',
    'hinterland-sb-dev',
    'hinterland-api-central-mi',
    'hinterland-api-mi',
    'hinterland-cae-central-dev',
    'hinterland-cae-dev',
    'hinterland-api-central',
    'hinterland-api',
    'moderation-pending',
    'inat-submit'
)

function Read-EnvironmentContract {
    param([Parameter(Mandatory)][string] $Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Adult QA environment contract not found: $Path"
    }

    $values = @{}
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lineNumber += 1
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        if ($trimmed -notmatch '^(?<name>[A-Z][A-Z0-9_]*)=(?<value>.*)$') {
            throw "Invalid environment contract line ${lineNumber}: $trimmed"
        }
        $name = $Matches.name
        $value = $Matches.value.Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($values.ContainsKey($name)) {
            throw "Duplicate environment contract key: $name"
        }
        $values[$name] = $value
    }
    return $values
}

function Require-Value {
    param(
        [Parameter(Mandatory)][hashtable] $Contract,
        [Parameter(Mandatory)][string] $Name
    )

    if (-not $Contract.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace($Contract[$Name])) {
        throw "Adult QA environment contract is missing $Name."
    }
    return [string] $Contract[$Name]
}

function Assert-Exact {
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][string] $Actual,
        [Parameter(Mandatory)][string] $Expected
    )

    if ($Actual -cne $Expected) {
        throw "$Name must be exactly '$Expected'; received '$Actual'."
    }
}

function Assert-NotForbidden {
    param(
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][string] $Actual,
        [Parameter(Mandatory)][string[]] $Forbidden
    )

    foreach ($item in $Forbidden) {
        if ($Actual -ieq $item) {
            throw "$Name resolves to protected W1/Gordi value '$item'."
        }
    }
}

function Invoke-AzJson {
    param([Parameter(Mandatory)][string[]] $Arguments)

    $output = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure read failed: az $($Arguments -join ' ')"
    }
    if ([string]::IsNullOrWhiteSpace(($output -join "`n"))) {
        return $null
    }
    return (($output -join "`n") | ConvertFrom-Json)
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)][string] $Value)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

$contract = Read-EnvironmentContract -Path $EnvironmentFile
$cost = Get-Content -LiteralPath $CostFile -Raw | ConvertFrom-Json

$requiredKeys = @(
    'PROJECT_SLUG',
    'ENVIRONMENT',
    'HINTERLAND_RELEASE_LANE',
    'AZURE_SUBSCRIPTION_ID',
    'AZURE_TENANT_ID',
    'AZURE_LOCATION',
    'HINTERLAND_RESOURCE_GROUP',
    'HINTERLAND_CONTAINER_APPS_ENV',
    'HINTERLAND_CONTAINER_APP_NAME',
    'HINTERLAND_USER_ASSIGNED_IDENTITY',
    'HINTERLAND_JOBS_USER_ASSIGNED_IDENTITY',
    'HINTERLAND_KEY_VAULT_NAME',
    'HINTERLAND_STORAGE_ACCOUNT',
    'HINTERLAND_PHOTOS_CONTAINER',
    'HINTERLAND_TAXONOMY_PACKS_CONTAINER',
    'HINTERLAND_SERVICE_BUS_NAMESPACE',
    'HINTERLAND_MODERATION_QUEUE',
    'HINTERLAND_CONTENT_SAFETY_ACCOUNT',
    'HINTERLAND_OPS_ACTION_GROUP',
    'HINTERLAND_PARENT_STATIC_WEB_APP',
    'HINTERLAND_ACR_NAME',
    'HINTERLAND_LOG_ANALYTICS_WORKSPACE',
    'HINTERLAND_SHARED_RESOURCE_GROUP',
    'HINTERLAND_DATABASE_HOST',
    'HINTERLAND_DATABASE_SERVER',
    'HINTERLAND_DATABASE_NAME',
    'HINTERLAND_DATABASE_USER',
    'HINTERLAND_API_DOMAIN',
    'HINTERLAND_PARENT_DOMAIN',
    'HINTERLAND_KID_ISSUER',
    'HINTERLAND_KID_AUDIENCE',
    'HINTERLAND_KID_KEY_ID',
    'HINTERLAND_ENTRA_API_AUDIENCE',
    'HINTERLAND_ENTRA_CLIENT_APP_ID',
    'HINTERLAND_MODERATION_PROVIDER',
    'HINTERLAND_INAT_SUBMIT_ENABLED',
    'HINTERLAND_DEV_AUTH_ENABLED',
    'HINTERLAND_ALLOW_STUB_AUTH',
    'HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED'
)
foreach ($key in $requiredKeys) {
    $null = Require-Value -Contract $contract -Name $key
}

Assert-Exact 'PROJECT_SLUG' $contract.PROJECT_SLUG 'hinterland'
Assert-Exact 'ENVIRONMENT' $contract.ENVIRONMENT 'adult-qa'
Assert-Exact 'HINTERLAND_RELEASE_LANE' $contract.HINTERLAND_RELEASE_LANE 'adult_qa'
Assert-Exact 'AZURE_SUBSCRIPTION_ID' $contract.AZURE_SUBSCRIPTION_ID $ContractSubscriptionId
Assert-Exact 'AZURE_TENANT_ID' $contract.AZURE_TENANT_ID $ContractTenantId
Assert-Exact 'AZURE_LOCATION' $contract.AZURE_LOCATION 'centralus'
Assert-Exact 'HINTERLAND_RESOURCE_GROUP' $contract.HINTERLAND_RESOURCE_GROUP $ContractResourceGroup
Assert-Exact 'HINTERLAND_SHARED_RESOURCE_GROUP' $contract.HINTERLAND_SHARED_RESOURCE_GROUP $ContractSharedResourceGroup
Assert-Exact 'HINTERLAND_CONTAINER_APPS_ENV' $contract.HINTERLAND_CONTAINER_APPS_ENV 'hinterland-cae-qa'
Assert-Exact 'HINTERLAND_CONTAINER_APP_NAME' $contract.HINTERLAND_CONTAINER_APP_NAME 'hinterland-api-qa'
Assert-Exact 'HINTERLAND_USER_ASSIGNED_IDENTITY' $contract.HINTERLAND_USER_ASSIGNED_IDENTITY 'hinterland-api-qa-mi'
Assert-Exact 'HINTERLAND_JOBS_USER_ASSIGNED_IDENTITY' $contract.HINTERLAND_JOBS_USER_ASSIGNED_IDENTITY 'hinterland-jobs-qa-mi'
Assert-Exact 'HINTERLAND_KEY_VAULT_NAME' $contract.HINTERLAND_KEY_VAULT_NAME 'hinterland-kv-qa'
Assert-Exact 'HINTERLAND_STORAGE_ACCOUNT' $contract.HINTERLAND_STORAGE_ACCOUNT 'hinterlandphotosqa'
Assert-Exact 'HINTERLAND_PHOTOS_CONTAINER' $contract.HINTERLAND_PHOTOS_CONTAINER 'qa-photos'
Assert-Exact 'HINTERLAND_TAXONOMY_PACKS_CONTAINER' $contract.HINTERLAND_TAXONOMY_PACKS_CONTAINER 'qa-taxonomy-packs'
Assert-Exact 'HINTERLAND_SERVICE_BUS_NAMESPACE' $contract.HINTERLAND_SERVICE_BUS_NAMESPACE 'hinterland-sb-qa'
Assert-Exact 'HINTERLAND_MODERATION_QUEUE' $contract.HINTERLAND_MODERATION_QUEUE 'moderation-pending-qa'
Assert-Exact 'HINTERLAND_CONTENT_SAFETY_ACCOUNT' $contract.HINTERLAND_CONTENT_SAFETY_ACCOUNT 'hinterland-content-safety-qa'
Assert-Exact 'HINTERLAND_OPS_ACTION_GROUP' $contract.HINTERLAND_OPS_ACTION_GROUP 'hinterland-ops-qa'
Assert-Exact 'HINTERLAND_PARENT_STATIC_WEB_APP' $contract.HINTERLAND_PARENT_STATIC_WEB_APP 'hinterland-parents-qa-swa'
Assert-Exact 'HINTERLAND_ACR_NAME' $contract.HINTERLAND_ACR_NAME 'hinterlandacrdev'
Assert-Exact 'HINTERLAND_LOG_ANALYTICS_WORKSPACE' $contract.HINTERLAND_LOG_ANALYTICS_WORKSPACE 'hinterland-law-dev'
Assert-Exact 'HINTERLAND_DATABASE_SERVER' $contract.HINTERLAND_DATABASE_SERVER 'hinterland-postgres-dev'
Assert-Exact 'HINTERLAND_DATABASE_HOST' $contract.HINTERLAND_DATABASE_HOST 'hinterland-postgres-dev.postgres.database.azure.com'
Assert-Exact 'HINTERLAND_DATABASE_NAME' $contract.HINTERLAND_DATABASE_NAME 'hinterland_qa'
Assert-Exact 'HINTERLAND_DATABASE_USER' $contract.HINTERLAND_DATABASE_USER 'hinterland_qa_app'
Assert-Exact 'HINTERLAND_API_DOMAIN' $contract.HINTERLAND_API_DOMAIN 'qa-api.thehinterlandguide.app'
Assert-Exact 'HINTERLAND_PARENT_DOMAIN' $contract.HINTERLAND_PARENT_DOMAIN 'qa-parents.thehinterlandguide.app'
Assert-Exact 'HINTERLAND_KID_ISSUER' $contract.HINTERLAND_KID_ISSUER 'https://qa-api.thehinterlandguide.app'
Assert-Exact 'HINTERLAND_KID_AUDIENCE' $contract.HINTERLAND_KID_AUDIENCE 'hinterland-api-qa'
Assert-Exact 'HINTERLAND_MODERATION_PROVIDER' $contract.HINTERLAND_MODERATION_PROVIDER 'azure_content_safety'
Assert-Exact 'HINTERLAND_INAT_SUBMIT_ENABLED' $contract.HINTERLAND_INAT_SUBMIT_ENABLED 'false'
Assert-Exact 'HINTERLAND_DEV_AUTH_ENABLED' $contract.HINTERLAND_DEV_AUTH_ENABLED 'false'
Assert-Exact 'HINTERLAND_ALLOW_STUB_AUTH' $contract.HINTERLAND_ALLOW_STUB_AUTH 'false'
Assert-Exact 'HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED' $contract.HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED 'true'

Assert-NotForbidden 'HINTERLAND_RESOURCE_GROUP' $contract.HINTERLAND_RESOURCE_GROUP $ForbiddenResourceGroups
Assert-NotForbidden 'HINTERLAND_DATABASE_NAME' $contract.HINTERLAND_DATABASE_NAME $ForbiddenDatabaseNames
Assert-NotForbidden 'HINTERLAND_DATABASE_USER' $contract.HINTERLAND_DATABASE_USER $ForbiddenDatabaseUsers
foreach ($name in @(
    'HINTERLAND_CONTAINER_APPS_ENV',
    'HINTERLAND_CONTAINER_APP_NAME',
    'HINTERLAND_USER_ASSIGNED_IDENTITY',
    'HINTERLAND_JOBS_USER_ASSIGNED_IDENTITY',
    'HINTERLAND_KEY_VAULT_NAME',
    'HINTERLAND_STORAGE_ACCOUNT',
    'HINTERLAND_SERVICE_BUS_NAMESPACE',
    'HINTERLAND_MODERATION_QUEUE',
    'HINTERLAND_CONTENT_SAFETY_ACCOUNT'
)) {
    Assert-NotForbidden $name $contract[$name] $ForbiddenResourceNames
}

foreach ($key in $contract.Keys) {
    if ($key -match 'INAT.*SUBMIT.*(TOKEN|SECRET|QUEUE)' -and -not [string]::IsNullOrWhiteSpace($contract[$key])) {
        throw "Public iNaturalist submission credential/queue '$key' is forbidden in Adult QA."
    }
}

if ($Mode -eq 'ProvisionReadiness' -and $Confirmation -cne $ContractApprovalPhrase) {
    throw "Billable provisioning is not approved. Review the cost preview and pass the exact approval phrase."
}
if ($Mode -eq 'PromotionReadiness') {
    foreach ($name in @('HINTERLAND_ENTRA_API_AUDIENCE', 'HINTERLAND_ENTRA_CLIENT_APP_ID')) {
        if ($contract[$name].StartsWith('REQUIRED_')) {
            throw "$name still contains a provisioning placeholder."
        }
    }
}

$resourceGroupExists = $false
$existingQaResources = @()
$operatorIdentityHash = Get-Sha256Hex -Value ([Environment]::UserName)

if (-not $SkipAzureReads) {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw 'Azure CLI is required for live Adult QA preflight reads.'
    }

    $account = Invoke-AzJson -Arguments @('account', 'show')
    Assert-Exact 'active Azure subscription' ([string] $account.id) $ContractSubscriptionId
    Assert-Exact 'active Azure tenant' ([string] $account.tenantId) $ContractTenantId
    $operatorIdentityHash = Get-Sha256Hex -Value "$($account.id):$($account.user.name)"

    $groupExistsText = & az group exists --name $ContractResourceGroup --only-show-errors --output tsv
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to read Adult QA resource-group state.'
    }
    $resourceGroupExists = (($groupExistsText -join "`n").Trim() -eq 'true')

    # These are the only shared services. Reads prove their exact resource
    # groups; no role assignment, setting, or resource is mutated here.
    $acr = Invoke-AzJson -Arguments @('acr', 'show', '--name', $contract.HINTERLAND_ACR_NAME)
    if (-not ([string] $acr.id).ToLowerInvariant().Contains("/resourcegroups/$($ContractSharedResourceGroup.ToLowerInvariant())/")) {
        throw 'The shared ACR is not in the approved shared resource group.'
    }
    $workspace = Invoke-AzJson -Arguments @(
        'monitor', 'log-analytics', 'workspace', 'show',
        '--resource-group', $ContractSharedResourceGroup,
        '--workspace-name', $contract.HINTERLAND_LOG_ANALYTICS_WORKSPACE
    )
    if ([string] $workspace.name -cne $contract.HINTERLAND_LOG_ANALYTICS_WORKSPACE) {
        throw 'The shared Log Analytics workspace does not match the contract.'
    }
    $postgres = Invoke-AzJson -Arguments @(
        'postgres', 'flexible-server', 'show',
        '--resource-group', $ContractSharedResourceGroup,
        '--name', $contract.HINTERLAND_DATABASE_SERVER
    )
    if ([string] $postgres.location -replace '\s', '' -ine 'centralus') {
        throw 'The approved shared PostgreSQL server is not in Central US.'
    }

    if ($resourceGroupExists) {
        $resources = Invoke-AzJson -Arguments @('resource', 'list', '--resource-group', $ContractResourceGroup)
        foreach ($resource in @($resources)) {
            $resourceName = [string] $resource.name
            $existingQaResources += $resourceName
            if ($resourceName -match '(?i)gordi|(^|-)dev($|-)' -or $ForbiddenResourceNames -icontains $resourceName) {
                throw "Adult QA resource group contains protected/non-QA resource '$resourceName'."
            }
        }
    }
    elseif ($Mode -eq 'PromotionReadiness') {
        throw 'Adult QA resource group does not exist; promotion cannot continue.'
    }
}

$generatedAt = [DateTimeOffset]::UtcNow
$approvalPresent = ($Mode -eq 'ProvisionReadiness' -and $Confirmation -ceq $ContractApprovalPhrase)
$approvalId = if ($approvalPresent) { [Guid]::NewGuid().ToString('N') } else { $null }
$result = [ordered] @{
    schema_version = 1
    classification = 'sanitized-adult-qa-preflight'
    generated_at = $generatedAt.ToString('o')
    mode = $Mode
    result = 'passed'
    release_lane = 'adult_qa'
    subscription_id = $ContractSubscriptionId
    resource_group = $ContractResourceGroup
    resource_group_exists = $resourceGroupExists
    existing_qa_resource_count = @($existingQaResources).Count
    shared_services = @('acr', 'log_analytics', 'postgres_server')
    database_name = $contract.HINTERLAND_DATABASE_NAME
    database_user = $contract.HINTERLAND_DATABASE_USER
    public_inaturalist_submission = 'disabled'
    cost_preview = [ordered] @{
        currency = [string] $cost.currency
        classification = [string] $cost.classification
        monthly_min = [int] $cost.monthly_total_min
        monthly_max = [int] $cost.monthly_total_max
        estimate_file = 'infra-azure/cost/adult-qa-monthly-estimate.json'
    }
    provisioning_approval = [ordered] @{
        required = $true
        present = $approvalPresent
        approval_id = $approvalId
        operator_identity_sha256 = if ($approvalPresent) { $operatorIdentityHash } else { $null }
        approved_at = if ($approvalPresent) { $generatedAt.ToString('o') } else { $null }
    }
}

$json = $result | ConvertTo-Json -Depth 8
if ($OutputPath) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    $parent = Split-Path -Parent $resolvedOutput
    if ($parent) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($resolvedOutput, $json + [Environment]::NewLine)
}
$json
