param(
    [int]$DispatcherSamples = 50
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$containerName = "hinterland-observation-verify-$suffix"
$databaseName = "hinterland_observation_verify"
$python = Join-Path $backendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

try {
    docker run --name $containerName `
        -e POSTGRES_USER=hinterland `
        -e POSTGRES_PASSWORD=hinterland `
        -e POSTGRES_DB=$databaseName `
        -p "127.0.0.1::5432" `
        -d postgres:16-alpine | Out-Null

    $ready = $false
    foreach ($attempt in 1..30) {
        docker exec $containerName pg_isready -U hinterland -d $databaseName | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "PostgreSQL 16 did not become ready"
    }

    $portLine = docker port $containerName "5432/tcp" | Select-Object -First 1
    $port = [int]($portLine -replace ".*:", "")

    $env:HINTERLAND_DATABASE_HOST = "127.0.0.1"
    $env:HINTERLAND_DATABASE_PORT = "$port"
    $env:HINTERLAND_DATABASE_NAME = $databaseName
    $env:HINTERLAND_DATABASE_USER = "hinterland"
    $env:HINTERLAND_DATABASE_PASSWORD = "hinterland"
    $env:OBSERVATION_TEST_DATABASE_URL = `
        "postgresql+asyncpg://hinterland:hinterland@127.0.0.1:$port/$databaseName"
    $env:OBSERVATION_DISPATCHER_PROBE_RUNS = "$DispatcherSamples"

    Push-Location $backendRoot
    try {
        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade failed" }
        & $python -m pytest tests/integration/test_observation_postgres.py -q -s
        if ($LASTEXITCODE -ne 0) {
            throw "Observation PostgreSQL verification failed"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    docker rm -f $containerName 2>$null | Out-Null
}
