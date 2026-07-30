[CmdletBinding()]
param(
    [ValidateSet('SecurePrompt', 'StandardInput')]
    [string] $InputMode = 'SecurePrompt',

    [ValidatePattern('^inat-cv-token$')]
    [string] $SecretName = 'inat-cv-token',

    [string] $MetadataOutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedSubscriptionId = '3ac5dfb0-91b7-47d3-8187-9dc8d6305e96'
$ExpectedTenantId = '18dbd7fa-c411-49bc-82fc-9ccaa26e3404'
$ExpectedResourceGroup = 'hinterland-qa-rg'
$ExpectedVaultName = 'hinterland-kv-qa'
$MinimumRemainingSeconds = 1800
$MaximumRemainingSeconds = 172800

function Invoke-AzJson {
    param([Parameter(Mandatory)][string[]] $Arguments)

    $output = & az @Arguments --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure read failed: az $($Arguments -join ' ')"
    }
    return (($output -join "`n") | ConvertFrom-Json)
}

function ConvertFrom-Base64Url {
    param([Parameter(Mandatory)][string] $Value)

    $normalized = $Value.Replace('-', '+').Replace('_', '/')
    switch ($normalized.Length % 4) {
        2 { $normalized += '==' }
        3 { $normalized += '=' }
        0 { }
        default { throw 'JWT payload is not valid base64url.' }
    }
    return [Convert]::FromBase64String($normalized)
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required to acquire a data-plane Key Vault access token.'
}

$account = Invoke-AzJson -Arguments @('account', 'show')
if ([string] $account.id -cne $ExpectedSubscriptionId) {
    throw 'Refusing token rotation outside the approved Adult QA subscription.'
}
if ([string] $account.tenantId -cne $ExpectedTenantId) {
    throw 'Refusing token rotation outside the approved Adult QA tenant.'
}

$vault = Invoke-AzJson -Arguments @(
    'keyvault', 'show',
    '--name', $ExpectedVaultName,
    '--resource-group', $ExpectedResourceGroup
)
if ([string] $vault.name -cne $ExpectedVaultName) {
    throw 'Adult QA Key Vault lookup returned an unexpected resource.'
}
if (-not ([string] $vault.id).ToLowerInvariant().Contains('/resourcegroups/hinterland-qa-rg/')) {
    throw 'Refusing a Key Vault outside hinterland-qa-rg.'
}
if ([string] $vault.name -ieq 'hinterland-kv-dev' -or [string] $vault.id -match '(?i)gordi-pilot-rg') {
    throw 'Refusing a W1 or Gordi Key Vault.'
}
$environmentTag = if ($null -ne $vault.tags) { [string] $vault.tags.environment } else { '' }
$releaseLaneTag = if ($null -ne $vault.tags) { [string] $vault.tags.'release-lane' } else { '' }
if ($environmentTag -cne 'adult-qa' -or $releaseLaneTag -cne 'adult_qa') {
    throw 'Adult QA Key Vault is missing the required environment/release-lane tags.'
}

$secureInput = $null
$bstr = [IntPtr]::Zero
$token = $null
$vaultAccessToken = $null
$requestBody = $null
$response = $null
try {
    if ($InputMode -eq 'SecurePrompt') {
        $secureInput = Read-Host (
            'Paste the fresh iNaturalist CV JWT for Adult QA. ' +
            'Input is hidden and is not saved'
        ) -AsSecureString
    }
    else {
        if (-not [Console]::IsInputRedirected) {
            throw 'StandardInput mode requires redirected input.'
        }
        $stdinValue = [Console]::In.ReadToEnd().Trim()
        if (-not $stdinValue) {
            throw 'Standard input was empty.'
        }
        $secureInput = ConvertTo-SecureString -String $stdinValue -AsPlainText -Force
        Remove-Variable stdinValue
    }

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureInput)
    $raw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim()
    try {
        $envelope = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($envelope -is [pscustomobject] -and $envelope.api_token -is [string]) {
            $token = $envelope.api_token.Trim()
        }
        else {
            throw 'JSON input does not contain an api_token string.'
        }
    }
    catch [System.ArgumentException] {
        throw
    }
    catch {
        $token = $raw
    }
    Remove-Variable raw

    if ($token -notmatch '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$') {
        throw 'Input is not a three-segment JWT or an api_token JSON object.'
    }

    $payloadBytes = ConvertFrom-Base64Url -Value ($token.Split('.')[1])
    try {
        $payload = [Text.Encoding]::UTF8.GetString($payloadBytes) | ConvertFrom-Json
    }
    catch {
        throw 'JWT payload is not a JSON object.'
    }
    if ($payload -isnot [pscustomobject] -or $null -eq $payload.exp) {
        throw 'JWT payload is missing an exp claim.'
    }
    $expiresAtUnix = 0L
    if (-not [long]::TryParse([string] $payload.exp, [ref] $expiresAtUnix)) {
        throw 'JWT exp claim is not an integer.'
    }
    $nowUnix = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $remaining = $expiresAtUnix - $nowUnix
    if ($remaining -lt $MinimumRemainingSeconds) {
        throw 'iNaturalist CV token has less than 30 minutes remaining.'
    }
    if ($remaining -gt $MaximumRemainingSeconds) {
        throw 'This QA-only operation accepts only short-lived iNaturalist tokens.'
    }
    $expiresAt = [DateTimeOffset]::FromUnixTimeSeconds($expiresAtUnix)

    # The CLI token is captured in memory, never printed. The secret itself is
    # sent directly to Key Vault's data plane; no temp file or command-line
    # argument contains the iNaturalist token.
    $vaultAccessToken = (
        & az account get-access-token `
            --resource 'https://vault.azure.net' `
            --query accessToken `
            --only-show-errors `
            --output tsv
    )
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($vaultAccessToken)) {
        throw 'Unable to acquire a Key Vault data-plane access token.'
    }
    $vaultAccessToken = ($vaultAccessToken -join "`n").Trim()

    $rotatedAt = [DateTimeOffset]::UtcNow
    $requestBody = @{
        value = $token
        attributes = @{
            enabled = $true
            exp = $expiresAtUnix
        }
        tags = @{
            environment = 'adult-qa'
            'release-lane' = 'adult_qa'
            purpose = 'post-clean-inaturalist-cv-only'
            'public-submit' = 'disabled'
            'expires-at-utc' = $expiresAt.ToString('o')
            'rotated-at-utc' = $rotatedAt.ToString('o')
        }
    } | ConvertTo-Json -Depth 5 -Compress

    $uri = "https://$ExpectedVaultName.vault.azure.net/secrets/$SecretName" +
        '?api-version=7.4'
    $response = Invoke-RestMethod `
        -Method Put `
        -Uri $uri `
        -Headers @{ Authorization = "Bearer $vaultAccessToken" } `
        -ContentType 'application/json' `
        -Body $requestBody

    if ([string] $response.id -notmatch "^https://$ExpectedVaultName\.vault\.azure\.net/secrets/$SecretName/") {
        throw 'Key Vault returned an unexpected secret identifier.'
    }

    $metadata = [ordered] @{
        schema_version = 1
        classification = 'sanitized-adult-qa-token-expiry'
        environment = 'adult-qa'
        release_lane = 'adult_qa'
        vault_name = $ExpectedVaultName
        secret_name = $SecretName
        rotated_at = $rotatedAt.ToString('o')
        expires_at = $expiresAt.ToString('o')
        remaining_seconds_at_rotation = $remaining
        public_inaturalist_submission = 'disabled'
    }
    $metadataJson = $metadata | ConvertTo-Json -Depth 4
    if ($MetadataOutputPath) {
        $resolvedOutput = [IO.Path]::GetFullPath($MetadataOutputPath)
        $parent = Split-Path -Parent $resolvedOutput
        if ($parent) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        [IO.File]::WriteAllText($resolvedOutput, $metadataJson + [Environment]::NewLine)
    }

    # Deliberately print expiry metadata only. Never print the JWT, Key Vault
    # access token, request body, or response.
    $metadataJson
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    if ($null -ne $secureInput) {
        $secureInput.Dispose()
    }
    Remove-Variable token, vaultAccessToken, requestBody, response -ErrorAction SilentlyContinue
}
