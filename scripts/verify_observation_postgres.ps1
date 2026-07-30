param(
    [int]$DispatcherSamples = 50
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$suffix = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$containerName = "hinterland-observation-verify-$suffix"
$databaseName = "hinterland_observation_verify"
$windowsPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$posixPython = Join-Path $backendRoot ".venv/bin/python"
if (Test-Path -LiteralPath $windowsPython) {
    $python = $windowsPython
}
elseif (Test-Path -LiteralPath $posixPython) {
    $python = $posixPython
}
else {
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
        docker exec $containerName psql -v ON_ERROR_STOP=1 -U hinterland `
            -d $databaseName -c "SELECT 1" | Out-Null
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
        # Exercise the additive cutover with real legacy rows, not only a
        # fresh-schema upgrade. The Field Journal projection must discard the
        # rejected newest row, retain observed chronology, and choose only a
        # clean representative photo.
        & $python -m alembic upgrade 20260709_0015
        if ($LASTEXITCODE -ne 0) { throw "Alembic pre-projection upgrade failed" }

        $legacySeed = @'
INSERT INTO users (id, firebase_uid, role, display_name)
VALUES
  ('01ARZ3NDEKTSV4RRFFQ69G5FAA', 'migration-parent', 'parent', 'Parent'),
  ('01ARZ3NDEKTSV4RRFFQ69G5FAB', 'migration-kid', 'kid', 'Kid');
INSERT INTO groups (id, name, join_code, owner_user_id)
VALUES ('01ARZ3NDEKTSV4RRFFQ69G5FAC', 'Migration', 'ABC123', '01ARZ3NDEKTSV4RRFFQ69G5FAA');
INSERT INTO memberships (id, group_id, user_id, role, observation_count, dex_count)
VALUES ('01ARZ3NDEKTSV4RRFFQ69G5FAD', '01ARZ3NDEKTSV4RRFFQ69G5FAC',
        '01ARZ3NDEKTSV4RRFFQ69G5FAB', 'kid', 3, 1);
INSERT INTO photos (
  id, user_id, bucket, object_name, status, attachment_status, submission_key
)
VALUES
  ('01ARZ3NDEKTSV4RRFFQ69G5FAE', '01ARZ3NDEKTSV4RRFFQ69G5FAB', 'verify',
   'pending/old.jpg', 'pending', 'attached', '01ARZ3NDEKTSV4RRFFQ69G5FAE'),
  ('01ARZ3NDEKTSV4RRFFQ69G5FAF', '01ARZ3NDEKTSV4RRFFQ69G5FAB', 'verify',
   'observations/new.jpg', 'clean', 'attached', '01ARZ3NDEKTSV4RRFFQ69G5FAF'),
  ('01ARZ3NDEKTSV4RRFFQ69G5FAG', '01ARZ3NDEKTSV4RRFFQ69G5FAB', 'verify',
   'rejected/latest.jpg', 'deleted', 'deleted', '01ARZ3NDEKTSV4RRFFQ69G5FAG');
INSERT INTO observations (
  id, user_id, group_id, photo_id, submission_key, taxon_id, species_name,
  observed_at, location_source, identification_source, dispatch_status,
  moderation_status, moderation_source, rejected_at, rewards
)
VALUES
  ('01ARZ3NDEKTSV4RRFFQ69G5FAH', '01ARZ3NDEKTSV4RRFFQ69G5FAB',
   '01ARZ3NDEKTSV4RRFFQ69G5FAC', '01ARZ3NDEKTSV4RRFFQ69G5FAE',
   '01ARZ3NDEKTSV4RRFFQ69G5FAH', 12345, 'Northern Cardinal',
   '2026-07-01T12:00:00Z', 'none', 'catalog', 'complete', 'pilot_private', 'noop', NULL,
   '[]'::jsonb),
  ('01ARZ3NDEKTSV4RRFFQ69G5FAJ', '01ARZ3NDEKTSV4RRFFQ69G5FAB',
   '01ARZ3NDEKTSV4RRFFQ69G5FAC', '01ARZ3NDEKTSV4RRFFQ69G5FAF',
   '01ARZ3NDEKTSV4RRFFQ69G5FAJ', 12345, 'Northern Cardinal',
   '2026-07-03T12:00:00Z', 'none', 'catalog', 'complete', 'clean', 'adult', NULL,
   '[]'::jsonb),
  ('01ARZ3NDEKTSV4RRFFQ69G5FAK', '01ARZ3NDEKTSV4RRFFQ69G5FAB',
   '01ARZ3NDEKTSV4RRFFQ69G5FAC', '01ARZ3NDEKTSV4RRFFQ69G5FAG',
   '01ARZ3NDEKTSV4RRFFQ69G5FAK', 12345, 'Northern Cardinal',
   '2026-07-04T12:00:00Z', 'none', 'catalog', 'unverified', 'rejected', 'adult', now(),
   '[]'::jsonb);
INSERT INTO dex_entries (
  id, user_id, group_id, taxon_id, species_name, first_observation_id, first_seen_at
)
VALUES ('01ARZ3NDEKTSV4RRFFQ69G5FAM', '01ARZ3NDEKTSV4RRFFQ69G5FAB',
        '01ARZ3NDEKTSV4RRFFQ69G5FAC', 12345, 'Northern Cardinal',
        '01ARZ3NDEKTSV4RRFFQ69G5FAK', '2026-07-04T12:00:00Z');
'@
        docker exec $containerName psql -v ON_ERROR_STOP=1 -U hinterland `
            -d $databaseName -c $legacySeed | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Field Journal legacy seed failed" }

        & $python -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "Alembic upgrade failed" }

        $projection = docker exec $containerName psql -At -U hinterland -d $databaseName `
            -c "SELECT first_observation_id, observation_count, latest_seen_at::date, representative_observation_id, representative_photo_id FROM dex_entries WHERE id = '01ARZ3NDEKTSV4RRFFQ69G5FAM'"
        if ($LASTEXITCODE -ne 0) { throw "Field Journal projection query failed" }
        $expectedProjection = "01ARZ3NDEKTSV4RRFFQ69G5FAH|2|2026-07-03|01ARZ3NDEKTSV4RRFFQ69G5FAJ|01ARZ3NDEKTSV4RRFFQ69G5FAF"
        if ($projection.Trim() -ne $expectedProjection) {
            throw "Field Journal projection backfill mismatch: $projection"
        }

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
