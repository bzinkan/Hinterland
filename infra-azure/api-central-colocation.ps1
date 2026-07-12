[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string] $ExpectedSubscriptionId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string] $ExpectedTenantId,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $ExpectedResourceGroup,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Image,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string] $AppVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# This is deliberately an additive API-only operation. Jobs remain in the
# established East US environment and DNS/TLS cutover is a separately reviewed
# operation after the generated Central US endpoint passes its canaries.
$ContractSubscriptionId = '3ac5dfb0-91b7-47d3-8187-9dc8d6305e96'
$ContractTenantId = '18dbd7fa-c411-49bc-82fc-9ccaa26e3404'
$ContractResourceGroup = 'hinterland-dev-rg'
$CentralLocation = 'centralus'
$CentralEnvironmentName = 'hinterland-cae-central-dev'
$CentralIdentityName = 'hinterland-api-central-mi'
$CentralAppName = 'hinterland-api-central'
$SourceIdentityName = 'hinterland-api-mi'
$LogAnalyticsWorkspaceName = 'hinterland-law-dev'
$KeyVaultName = 'hinterland-kv-dev'
$StorageAccountName = 'hinterlandphotosdev'
$PhotosContainerName = 'photos'
$TaxonomyPacksContainerName = 'taxonomy-packs'
$AcrName = 'hinterlandacrdev'
$ServiceBusNamespaceName = 'hinterland-sb-dev'
$ModerationQueueName = 'moderation-pending'
$PostgresServerName = 'hinterland-postgres-dev'
$PostgresDatabaseName = 'hinterland'
$PostgresUserName = 'hadmin'
$EntraApiAudience = '7dd9da3c-b7d6-45d4-955b-d7561c43f209'
$EntraClientAppId = '60504e4c-6b5f-4031-a80a-3e4bdfae29b2'
$KidJwtKeyId = 'k1-2026-07'
$KidJwksPath = '/.well-known/hinterland-kid-jwks.json'

function Invoke-AzJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $AzArguments,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    $jsonText = (& az @AzArguments --only-show-errors --output json 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        throw "$FailureMessage Azure returned no resource document."
    }
    try {
        return $jsonText | ConvertFrom-Json
    }
    catch {
        throw "$FailureMessage Azure returned an unreadable resource document."
    }
}

function Invoke-AzNone {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $AzArguments,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    & az @AzArguments --only-show-errors --output none 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Test-AzResource {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $AzArguments
    )

    & az @AzArguments --only-show-errors --output none 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Normalize-Location {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Location
    )

    return $Location.ToLowerInvariant().Replace(' ', '')
}

function Assert-ContractValue {
    param(
        [AllowNull()]
        [object] $Actual,

        [Parameter(Mandatory = $true)]
        [object] $Expected,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    if ([string] $Actual -cne [string] $Expected) {
        throw $FailureMessage
    }
}

function Test-ExactRoleAssignment {
    param(
        [Parameter(Mandatory = $true)]
        [string] $PrincipalId,

        [Parameter(Mandatory = $true)]
        [string] $RoleName,

        [Parameter(Mandatory = $true)]
        [string] $Scope
    )

    $assignments = @(Invoke-AzJson -AzArguments @(
            'role', 'assignment', 'list',
            '--assignee-object-id', $PrincipalId,
            '--scope', $Scope
        ) -FailureMessage 'Unable to inspect the required RBAC scope.')
    return @($assignments | Where-Object {
            $_.roleDefinitionName -ceq $RoleName -and $_.scope -ieq $Scope
        }).Count -gt 0
}

function Ensure-MirroredRoleAssignment {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourcePrincipalId,

        [Parameter(Mandatory = $true)]
        [string] $TargetPrincipalId,

        [Parameter(Mandatory = $true)]
        [string] $RoleName,

        [Parameter(Mandatory = $true)]
        [string] $Scope
    )

    if (-not (Test-ExactRoleAssignment `
                -PrincipalId $SourcePrincipalId `
                -RoleName $RoleName `
                -Scope $Scope)) {
        throw "The source API identity does not hold the expected $RoleName grant at its exact scope."
    }

    if (-not (Test-ExactRoleAssignment `
                -PrincipalId $TargetPrincipalId `
                -RoleName $RoleName `
                -Scope $Scope)) {
        Invoke-AzNone -AzArguments @(
            'role', 'assignment', 'create',
            '--assignee-object-id', $TargetPrincipalId,
            '--assignee-principal-type', 'ServicePrincipal',
            '--role', $RoleName,
            '--scope', $Scope
        ) -FailureMessage "Unable to mirror the $RoleName grant to the Central US API identity."
    }

    if (-not (Test-ExactRoleAssignment `
                -PrincipalId $TargetPrincipalId `
                -RoleName $RoleName `
                -Scope $Scope)) {
        throw "The Central US API identity is missing the required $RoleName grant."
    }
}

if ($ExpectedSubscriptionId -cne $ContractSubscriptionId) {
    throw 'The requested subscription does not match the isolated Hinterland subscription contract.'
}
if ($ExpectedTenantId -cne $ContractTenantId) {
    throw 'The requested tenant does not match the isolated Hinterland tenant contract.'
}
if ($ExpectedResourceGroup -cne $ContractResourceGroup) {
    throw 'The requested resource group is not the isolated Hinterland resource group.'
}
if ($ExpectedResourceGroup -eq 'gordi-pilot-rg') {
    throw 'Refusing to target gordi-pilot-rg.'
}
if ($Image -cnotmatch '^hinterlandacrdev\.azurecr\.io/hinterland-api@sha256:[0-9a-f]{64}$') {
    throw 'Image must be the Hinterland ACR repository pinned to an immutable sha256 digest.'
}
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required.'
}

$account = Invoke-AzJson -AzArguments @('account', 'show') `
    -FailureMessage 'Unable to read the active Azure account.'
Assert-ContractValue $account.id $ExpectedSubscriptionId 'The active Azure subscription is not approved.'
Assert-ContractValue $account.tenantId $ExpectedTenantId 'The active Azure tenant is not approved.'

Invoke-AzNone -AzArguments @('group', 'show', '--name', $ExpectedResourceGroup) `
    -FailureMessage 'The isolated Hinterland resource group is unavailable.'

$workspace = Invoke-AzJson -AzArguments @(
    'monitor', 'log-analytics', 'workspace', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--workspace-name', $LogAnalyticsWorkspaceName
) -FailureMessage 'The existing Hinterland Log Analytics workspace is unavailable.'
if ([string]::IsNullOrWhiteSpace([string] $workspace.customerId)) {
    throw 'The existing Log Analytics workspace has no customer ID.'
}

$postgres = Invoke-AzJson -AzArguments @(
    'postgres', 'flexible-server', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $PostgresServerName
) -FailureMessage 'The existing Hinterland PostgreSQL server is unavailable.'
Assert-ContractValue `
    (Normalize-Location ([string] $postgres.location)) `
    $CentralLocation `
    'The Hinterland PostgreSQL server is not in Central US.'
if ([string]::IsNullOrWhiteSpace([string] $postgres.fullyQualifiedDomainName)) {
    throw 'The existing Hinterland PostgreSQL server has no FQDN.'
}

$keyVault = Invoke-AzJson -AzArguments @(
    'keyvault', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $KeyVaultName
) -FailureMessage 'The existing Hinterland Key Vault is unavailable.'
$storage = Invoke-AzJson -AzArguments @(
    'storage', 'account', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $StorageAccountName
) -FailureMessage 'The existing Hinterland photo storage account is unavailable.'
$acr = Invoke-AzJson -AzArguments @(
    'acr', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $AcrName
) -FailureMessage 'The existing Hinterland container registry is unavailable.'
$moderationQueue = Invoke-AzJson -AzArguments @(
    'servicebus', 'queue', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--namespace-name', $ServiceBusNamespaceName,
    '--name', $ModerationQueueName
) -FailureMessage 'The existing Hinterland moderation queue is unavailable.'
$sourceIdentity = Invoke-AzJson -AzArguments @(
    'identity', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $SourceIdentityName
) -FailureMessage 'The established Hinterland API identity is unavailable.'

if (-not (Test-AzResource -AzArguments @(
            'containerapp', 'env', 'show',
            '--resource-group', $ExpectedResourceGroup,
            '--name', $CentralEnvironmentName
        ))) {
    $workspaceKeys = Invoke-AzJson -AzArguments @(
        'monitor', 'log-analytics', 'workspace', 'get-shared-keys',
        '--resource-group', $ExpectedResourceGroup,
        '--workspace-name', $LogAnalyticsWorkspaceName
    ) -FailureMessage 'Unable to obtain the existing Log Analytics workspace binding material.'
    $workspaceKey = [string] $workspaceKeys.primarySharedKey
    if ([string]::IsNullOrWhiteSpace($workspaceKey)) {
        throw 'The existing Log Analytics workspace binding material is unavailable.'
    }

    Invoke-AzNone -AzArguments @(
        'containerapp', 'env', 'create',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralEnvironmentName,
        '--location', $CentralLocation,
        '--environment-mode', 'WorkloadProfiles',
        '--logs-destination', 'log-analytics',
        '--logs-workspace-id', [string] $workspace.customerId,
        '--logs-workspace-key', $workspaceKey,
        '--tags', 'project=hinterland', 'environment=dev', 'role=api-central', 'managed-by=script'
    ) -FailureMessage 'Unable to create the Central US Container Apps environment.'

    $workspaceKey = $null
    $workspaceKeys = $null
}

$centralEnvironment = Invoke-AzJson -AzArguments @(
    'containerapp', 'env', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $CentralEnvironmentName
) -FailureMessage 'The Central US Container Apps environment is unavailable.'
Assert-ContractValue `
    (Normalize-Location ([string] $centralEnvironment.location)) `
    $CentralLocation `
    'The Central Container Apps environment is not in Central US.'
Assert-ContractValue `
    $centralEnvironment.properties.provisioningState `
    'Succeeded' `
    'The Central Container Apps environment is not provisioned.'
Assert-ContractValue `
    $centralEnvironment.properties.environmentMode `
    'WorkloadProfiles' `
    'The Central Container Apps environment must use workload profiles.'
Assert-ContractValue `
    $centralEnvironment.properties.appLogsConfiguration.destination `
    'log-analytics' `
    'The Central Container Apps environment is not using Log Analytics.'
Assert-ContractValue `
    $centralEnvironment.properties.appLogsConfiguration.logAnalyticsConfiguration.customerId `
    $workspace.customerId `
    'The Central Container Apps environment is not using the established Log Analytics workspace.'
Assert-ContractValue `
    $centralEnvironment.properties.publicNetworkAccess `
    'Enabled' `
    'The Central Container Apps environment is not publicly reachable for API ingress.'

if (-not (Test-AzResource -AzArguments @(
            'identity', 'show',
            '--resource-group', $ExpectedResourceGroup,
            '--name', $CentralIdentityName
        ))) {
    Invoke-AzNone -AzArguments @(
        'identity', 'create',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralIdentityName,
        '--location', $CentralLocation,
        '--tags', 'project=hinterland', 'environment=dev', 'role=api-central', 'managed-by=script'
    ) -FailureMessage 'Unable to create the Central US API managed identity.'
}

$centralIdentity = Invoke-AzJson -AzArguments @(
    'identity', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $CentralIdentityName
) -FailureMessage 'The Central US API managed identity is unavailable.'
Assert-ContractValue `
    (Normalize-Location ([string] $centralIdentity.location)) `
    $CentralLocation `
    'The Central API identity is not in Central US.'
if ([string]::IsNullOrWhiteSpace([string] $centralIdentity.id) -or
    [string]::IsNullOrWhiteSpace([string] $centralIdentity.principalId) -or
    [string]::IsNullOrWhiteSpace([string] $centralIdentity.clientId)) {
    throw 'The Central API identity is incomplete.'
}

$roleMappings = @(
    [pscustomobject] @{ Role = 'AcrPull'; Scope = [string] $acr.id },
    [pscustomobject] @{ Role = 'Key Vault Secrets User'; Scope = [string] $keyVault.id },
    [pscustomobject] @{ Role = 'Storage Blob Data Contributor'; Scope = [string] $storage.id },
    [pscustomobject] @{ Role = 'Azure Service Bus Data Sender'; Scope = [string] $moderationQueue.id },
    [pscustomobject] @{ Role = 'Azure Service Bus Data Receiver'; Scope = [string] $moderationQueue.id }
)
Assert-ContractValue $roleMappings.Count 5 'The Central API RBAC contract must contain exactly five grants.'
foreach ($mapping in $roleMappings) {
    Ensure-MirroredRoleAssignment `
        -SourcePrincipalId ([string] $sourceIdentity.principalId) `
        -TargetPrincipalId ([string] $centralIdentity.principalId) `
        -RoleName ([string] $mapping.Role) `
        -Scope ([string] $mapping.Scope)
}
$centralRoleAssignments = @(Invoke-AzJson -AzArguments @(
        'role', 'assignment', 'list',
        '--assignee-object-id', [string] $centralIdentity.principalId,
        '--all'
    ) -FailureMessage 'Unable to verify the Central API RBAC inventory.')
$expectedRoleKeys = @(
    $roleMappings | ForEach-Object { "$($_.Role)|$($_.Scope)".ToLowerInvariant() }
)
$actualRoleKeys = @(
    $centralRoleAssignments | ForEach-Object {
        "$($_.roleDefinitionName)|$($_.scope)".ToLowerInvariant()
    }
)
if ($actualRoleKeys.Count -ne 5 -or
    @($actualRoleKeys | Where-Object { $_ -notin $expectedRoleKeys }).Count -ne 0 -or
    @($expectedRoleKeys | Where-Object { $_ -notin $actualRoleKeys }).Count -ne 0) {
    throw 'The Central API identity RBAC inventory must be exactly the five approved mirrored grants.'
}

$keyVaultBaseUrl = ([string] $keyVault.properties.vaultUri).TrimEnd('/')
$postgresPasswordReference = "pg-password=keyvaultref:$keyVaultBaseUrl/secrets/postgres-admin-password,identityref:$($centralIdentity.id)"
$runtimeEnvironment = [ordered] @{
    PORT                                        = '8080'
    HINTERLAND_ENV                             = 'dev'
    HINTERLAND_APP_VERSION                     = $AppVersion
    HINTERLAND_LOG_LEVEL                       = 'INFO'
    HINTERLAND_STORAGE_PROVIDER                = 'blob'
    HINTERLAND_BLOB_ACCOUNT_ENDPOINT           = [string] $storage.primaryEndpoints.blob
    HINTERLAND_PHOTOS_BUCKET                   = $PhotosContainerName
    HINTERLAND_TAXONOMY_PACKS_BUCKET           = $TaxonomyPacksContainerName
    HINTERLAND_KEY_VAULT_URL                   = $keyVaultBaseUrl + '/'
    HINTERLAND_KEY_VAULT_KID_SIGNING_SECRET    = 'kid-jwt-signing-key'
    HINTERLAND_KEY_VAULT_KID_PUBLIC_SECRET     = 'kid-jwt-public-key'
    HINTERLAND_DATABASE_HOST                   = [string] $postgres.fullyQualifiedDomainName
    HINTERLAND_DATABASE_PORT                   = '5432'
    HINTERLAND_DATABASE_NAME                   = $PostgresDatabaseName
    HINTERLAND_DATABASE_USER                   = $PostgresUserName
    HINTERLAND_DATABASE_PASSWORD               = 'secretref:pg-password'
    HINTERLAND_READINESS_DATABASE_REQUIRED     = 'true'
    HINTERLAND_ENTRA_TENANT_ID                 = $ExpectedTenantId
    HINTERLAND_ENTRA_API_AUDIENCE              = $EntraApiAudience
    HINTERLAND_ENTRA_CLIENT_APP_ID             = $EntraClientAppId
    HINTERLAND_ENTRA_REQUIRED_SCOPE            = 'user.access'
    HINTERLAND_ENTRA_ISSUER                    = "https://login.microsoftonline.com/$ExpectedTenantId/v2.0"
    HINTERLAND_ENTRA_JWKS_URL                  = "https://login.microsoftonline.com/$ExpectedTenantId/discovery/v2.0/keys"
    HINTERLAND_KID_JWT_ISSUER                  = 'https://api.thehinterlandguide.app'
    HINTERLAND_KID_JWT_AUDIENCE                = 'hinterland-api'
    HINTERLAND_KID_JWT_KID                     = $KidJwtKeyId
    HINTERLAND_ALLOW_STUB_AUTH                 = 'false'
    HINTERLAND_DEV_AUTH_ENABLED                = 'false'
    HINTERLAND_MODERATION_PROVIDER             = 'noop'
    HINTERLAND_INAT_CV_ENABLED                 = 'false'
    HINTERLAND_INAT_CV_DISCLOSURE_APPROVED     = 'false'
    HINTERLAND_INAT_CV_BENCHMARK_APPROVED      = 'false'
    HINTERLAND_INAT_SUBMIT_ENABLED             = 'false'
    HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED = 'true'
    HINTERLAND_ORGANISM_FALLBACK_PROVIDER      = 'noop'
    HINTERLAND_GEOCODING_PROVIDER              = 'noop'
    HINTERLAND_SERVICE_BUS_NAMESPACE           = "$ServiceBusNamespaceName.servicebus.windows.net"
    HINTERLAND_SERVICE_BUS_MODERATION_QUEUE    = $ModerationQueueName
    AZURE_CLIENT_ID                            = [string] $centralIdentity.clientId
}
$runtimeEnvironmentArguments = @(
    foreach ($entry in $runtimeEnvironment.GetEnumerator()) {
        "$($entry.Key)=$($entry.Value)"
    }
)

$centralAppExists = Test-AzResource -AzArguments @(
    'containerapp', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $CentralAppName
)
if (-not $centralAppExists) {
    $createArguments = @(
        'containerapp', 'create',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--environment', [string] $centralEnvironment.id,
        '--image', $Image,
        '--user-assigned', [string] $centralIdentity.id,
        '--registry-server', [string] $acr.loginServer,
        '--registry-identity', [string] $centralIdentity.id,
        '--ingress', 'external',
        '--target-port', '8080',
        '--transport', 'auto',
        '--revisions-mode', 'single',
        '--min-replicas', '0',
        '--max-replicas', '1',
        '--cpu', '0.5',
        '--memory', '1Gi',
        '--secrets', $postgresPasswordReference,
        '--env-vars'
    ) + $runtimeEnvironmentArguments
    Invoke-AzNone -AzArguments $createArguments `
        -FailureMessage 'Unable to create the Central US API.'
}
else {
    $existingCentralApp = Invoke-AzJson -AzArguments @(
        'containerapp', 'show',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName
    ) -FailureMessage 'Unable to inspect the existing Central US API.'
    if ([string] $existingCentralApp.properties.environmentId -ine [string] $centralEnvironment.id) {
        throw 'The existing Central API belongs to an unexpected Container Apps environment.'
    }

    Invoke-AzNone -AzArguments @(
        'containerapp', 'identity', 'assign',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--user-assigned', [string] $centralIdentity.id
    ) -FailureMessage 'Unable to assign the Central API identity.'
    Invoke-AzNone -AzArguments @(
        'containerapp', 'registry', 'set',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--server', [string] $acr.loginServer,
        '--identity', [string] $centralIdentity.id
    ) -FailureMessage 'Unable to configure Central API registry authentication.'
    Invoke-AzNone -AzArguments @(
        'containerapp', 'secret', 'set',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--secrets', $postgresPasswordReference
    ) -FailureMessage 'Unable to configure the Central API PostgreSQL Key Vault reference.'
    Invoke-AzNone -AzArguments @(
        'containerapp', 'ingress', 'enable',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--type', 'external',
        '--allow-insecure', 'false',
        '--target-port', '8080',
        '--transport', 'auto'
    ) -FailureMessage 'Unable to configure Central API ingress.'
    Invoke-AzNone -AzArguments @(
        'containerapp', 'revision', 'set-mode',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--mode', 'single'
    ) -FailureMessage 'Unable to configure Central API revision mode.'
    $updateArguments = @(
        'containerapp', 'update',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--image', $Image,
        '--min-replicas', '0',
        '--max-replicas', '1',
        '--cpu', '0.5',
        '--memory', '1Gi',
        '--replace-env-vars'
    ) + $runtimeEnvironmentArguments
    Invoke-AzNone -AzArguments $updateArguments `
        -FailureMessage 'Unable to reconcile the Central US API revision.'
}

$deadline = [DateTime]::UtcNow.AddMinutes(10)
$readyRevision = $null
do {
    $centralApp = Invoke-AzJson -AzArguments @(
        'containerapp', 'show',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName
    ) -FailureMessage 'Unable to inspect the Central US API rollout.'
    $latestRevision = [string] $centralApp.properties.latestRevisionName
    $latestReadyRevision = [string] $centralApp.properties.latestReadyRevisionName
    if (-not [string]::IsNullOrWhiteSpace($latestRevision) -and
        $latestRevision -ceq $latestReadyRevision) {
        $revision = Invoke-AzJson -AzArguments @(
            'containerapp', 'revision', 'show',
            '--resource-group', $ExpectedResourceGroup,
            '--name', $CentralAppName,
            '--revision', $latestRevision
        ) -FailureMessage 'Unable to inspect the Central US API revision.'
        $revisionVersion = @($revision.properties.template.containers[0].env | Where-Object {
                $_.name -ceq 'HINTERLAND_APP_VERSION'
            })[0].value
        if ([string] $revision.properties.template.containers[0].image -ceq $Image -and
            [string] $revisionVersion -ceq $AppVersion -and
            [string] $revision.properties.provisioningState -ceq 'Provisioned' -and
            [string] $revision.properties.healthState -ceq 'Healthy' -and
            ([string] $revision.properties.runningState -like 'Running*' -or
                [string] $revision.properties.runningState -ceq 'ScaledToZero')) {
            $readyRevision = $latestRevision
            break
        }
    }
    Start-Sleep -Seconds 10
} while ([DateTime]::UtcNow -lt $deadline)

if ([string]::IsNullOrWhiteSpace([string] $readyRevision)) {
    throw 'The exact Central US API image did not become healthy within ten minutes.'
}

$preCleanupApp = Invoke-AzJson -AzArguments @(
    'containerapp', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $CentralAppName
) -FailureMessage 'Unable to inspect the Central API secret inventory.'
$retiredSecretNames = @(
    @($preCleanupApp.properties.configuration.secrets) |
        Where-Object { [string] $_.name -cne 'pg-password' } |
        ForEach-Object { [string] $_.name }
)
if ($retiredSecretNames.Count -gt 0) {
    $removeSecretArguments = @(
        'containerapp', 'secret', 'remove',
        '--resource-group', $ExpectedResourceGroup,
        '--name', $CentralAppName,
        '--secret-names'
    ) + $retiredSecretNames
    Invoke-AzNone -AzArguments $removeSecretArguments `
        -FailureMessage 'Unable to remove retired Central API secret references.'
}

$centralApp = Invoke-AzJson -AzArguments @(
    'containerapp', 'show',
    '--resource-group', $ExpectedResourceGroup,
    '--name', $CentralAppName
) -FailureMessage 'Unable to verify the Central US API.'
Assert-ContractValue `
    (Normalize-Location ([string] $centralApp.location)) `
    $CentralLocation `
    'The Central API is not in Central US.'
if ([string] $centralApp.properties.environmentId -ine [string] $centralEnvironment.id) {
    throw 'The Central API placement does not match the Central Container Apps environment.'
}
Assert-ContractValue `
    $centralApp.properties.configuration.activeRevisionsMode `
    'Single' `
    'The Central API must use single-revision mode.'
Assert-ContractValue `
    $centralApp.properties.configuration.ingress.external `
    $true `
    'The Central API ingress must be external.'
Assert-ContractValue `
    $centralApp.properties.configuration.ingress.allowInsecure `
    $false `
    'The Central API must reject insecure ingress.'
Assert-ContractValue `
    $centralApp.properties.configuration.ingress.targetPort `
    8080 `
    'The Central API target port must be 8080.'
Assert-ContractValue `
    $centralApp.properties.template.containers[0].image `
    $Image `
    'The Central API is not pinned to the expected immutable image.'
Assert-ContractValue `
    $centralApp.properties.template.scale.minReplicas `
    0 `
    'The Central API must retain scale-to-zero during W1.'
Assert-ContractValue `
    $centralApp.properties.template.scale.maxReplicas `
    1 `
    'The Central API must remain bounded to one W1 replica.'

$assignedIdentityIds = @($centralApp.identity.userAssignedIdentities.PSObject.Properties.Name)
if ([string] $centralApp.identity.type -cne 'UserAssigned' -or
    $assignedIdentityIds.Count -ne 1 -or
    $assignedIdentityIds[0] -ine [string] $centralIdentity.id) {
    throw 'The Central API must use only the dedicated Central US managed identity.'
}
$traffic = @($centralApp.properties.configuration.ingress.traffic)
if ($traffic.Count -ne 1 -or
    [bool] $traffic[0].latestRevision -ne $true -or
    [int] $traffic[0].weight -ne 100) {
    throw 'The Central API must send all ingress traffic to its latest healthy revision.'
}
$registries = @($centralApp.properties.configuration.registries)
if ($registries.Count -ne 1 -or
    [string] $registries[0].server -cne [string] $acr.loginServer -or
    [string] $registries[0].identity -ine [string] $centralIdentity.id) {
    throw 'The Central API registry identity contract is not exact.'
}
$secrets = @($centralApp.properties.configuration.secrets)
if ($secrets.Count -ne 1 -or
    [string] $secrets[0].name -cne 'pg-password' -or
    [string] $secrets[0].keyVaultUrl -cne "$keyVaultBaseUrl/secrets/postgres-admin-password" -or
    [string] $secrets[0].identity -ine [string] $centralIdentity.id) {
    throw 'The Central API must use only the PostgreSQL Key Vault secret reference.'
}

$actualEnvironment = @{}
foreach ($entry in @($centralApp.properties.template.containers[0].env)) {
    if ($actualEnvironment.ContainsKey([string] $entry.name)) {
        throw 'The Central API contains a duplicate environment variable.'
    }
    $secretRefProperty = $entry.PSObject.Properties['secretRef']
    if ($null -ne $secretRefProperty -and
        -not [string]::IsNullOrWhiteSpace([string] $secretRefProperty.Value)) {
        $actualEnvironment[[string] $entry.name] = "secretref:$($secretRefProperty.Value)"
    }
    else {
        $actualEnvironment[[string] $entry.name] = [string] $entry.value
    }
}
if ($actualEnvironment.Count -ne $runtimeEnvironment.Count) {
    throw 'The Central API environment variable inventory is not exact.'
}
foreach ($expectedEntry in $runtimeEnvironment.GetEnumerator()) {
    if (-not $actualEnvironment.ContainsKey([string] $expectedEntry.Key) -or
        [string] $actualEnvironment[[string] $expectedEntry.Key] -cne [string] $expectedEntry.Value) {
        throw "The Central API has an unexpected value for $($expectedEntry.Key)."
    }
}
foreach ($forbiddenName in @(
        'HINTERLAND_INAT_OAUTH_TOKEN',
        'HINTERLAND_DEV_AUTH_TOKEN',
        'HINTERLAND_KID_JWT_SIGNING_PEM',
        'HINTERLAND_KID_JWT_PUBLIC_PEM'
    )) {
    if ($actualEnvironment.ContainsKey($forbiddenName)) {
        throw "The Central API contains the forbidden runtime secret $forbiddenName."
    }
}

$fqdn = [string] $centralApp.properties.configuration.ingress.fqdn
if ([string]::IsNullOrWhiteSpace($fqdn) -or
    $fqdn -notlike '*.centralus.azurecontainerapps.io') {
    throw 'The Central API does not expose a Central US generated hostname.'
}
$health = Invoke-RestMethod -Uri "https://$fqdn/health" -TimeoutSec 30
$readiness = Invoke-RestMethod -Uri "https://$fqdn/ready" -TimeoutSec 30
$jwks = Invoke-RestMethod -Uri "https://$fqdn$KidJwksPath" -TimeoutSec 30
Assert-ContractValue $health.status 'ok' 'The Central API health probe failed.'
Assert-ContractValue $health.version $AppVersion 'The Central API health version is not exact.'
Assert-ContractValue $readiness.status 'ready' 'The Central API readiness probe failed.'
Assert-ContractValue $readiness.version $AppVersion 'The Central API readiness version is not exact.'
if (@($jwks.keys).Count -lt 1) {
    throw 'The Central API kid JWKS is empty.'
}

Write-Host "Central US API verified: app=$CentralAppName revision=$readyRevision image=$Image"
Write-Host "Generated endpoint verified: https://$fqdn"
