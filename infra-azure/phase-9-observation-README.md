# Observation W1 operations

This is the provisioning and recovery contract for supervised W1 testing in
isolated `hinterland-dev-rg`. It never targets `gordi-pilot-rg`.

## Components

| Component | Purpose |
|---|---|
| `phase-9-observation-w1.sh` | Removes Event Grid moderation, revokes/deletes iNaturalist work, runs preflight/migrations, provisions required jobs, applies lifecycle rules, and forces NoOp/default-deny gates. |
| `phase-9-observation-monitoring.sh` | Adds moderation queue/DLQ, stale work, dispatcher p95, rebuild, mismatch, retention, conflict, and job-failure alerts. |
| `policies/observation-w1-lifecycle.json` | Removes raw uploads after 24 hours, dedicated pilot-private bytes after seven days, and quarantine/rejected prefixes after 90 days. |
| `admin.observation_retention` | Database-aware orphan and seven-day `pilot_private` cleanup. |
| `admin.observation_health_probe` | Read-only relational health snapshot for alerts. |
| `admin.observation_migration_preflight` | Read-only duplicate/counter/location migration gate. |
| `admin.observation_legacy_reconcile` | Canonicalizes and registers observations written by the pre-W1 API during migration-first cutover. |
| `scripts/smoke_observation_w1.py` | BlockBlob, exactly-one replay, private NoOp, and photo-access canary. |

## First Repaired Deploy

Build an immutable image from the repository root, then provision jobs before
starting the repaired workflow. Run this block in Git Bash; the script handles
Windows Azure-CLI CRLF and local-path conversion explicitly:

```bash
MERGE_SHA="$(git rev-parse HEAD)"
az acr build --registry hinterlandacrdev \
  --image "hinterland-api:${MERGE_SHA}" \
  --file backend/Dockerfile .
DIGEST="$(az acr repository show --name hinterlandacrdev \
  --image "hinterland-api:${MERGE_SHA}" --query digest -o tsv | tr -d '\r')"
export HINTERLAND_PHASE9_IMAGE="hinterlandacrdev.azurecr.io/hinterland-api@${DIGEST}"
MSYS_NO_PATHCONV=1 bash infra-azure/phase-9-observation-w1.sh

export HINTERLAND_ALERT_EMAIL="ops@example.com"
MSYS_NO_PATHCONV=1 bash infra-azure/phase-9-observation-monitoring.sh
```

The W1 script creates/updates:

- `hinterland-obs-preflight`
- `hinterland-migrate`
- `hinterland-legacy-reconcile`
- `hinterland-moderation-job` (the existing canonical consumer name)
- `hinterland-mod-outbox-relay`
- `hinterland-dispatcher-replay`
- `hinterland-state-rebuild`
- `hinterland-obs-retention`
- `hinterland-obs-health`
- `hinterland-sweep-stale-reviews`
- `hinterland-taxa-catalog-ingest`
- `hinterland-rarity-refresh`
- `hinterland-expedition-funnel`
- `hinterland-sync-expeditions`

It pins every Hinterland job to the supplied digest. It first removes both
iNaturalist token aliases because the old API does not understand the new CV
flag. It deletes all known historical iNaturalist consumer/replay names,
including `hinterland-inat-job`, removes namespace-wide Service Bus data roles
before granting moderation-queue-only access, and discovers every Event Grid
system topic sourced by the photo storage account before removing subscriptions
targeting `moderation-pending`.

After migration, the script runs legacy reconciliation before it creates the
outbox relay. Migration has already registered attached pending observations in
`moderation_outbox`, but the relay excludes them until the reconciler verifies
and re-encodes old `pending/<photo_id>.jpg` bytes. Invalid legacy bytes fail
closed to rejection and deterministic rebuild. Database-aware retention scans
the whole `pending/` tree and protects every row that already has an
observation, including an old-API row whose attachment default has not yet been
repaired.

The old dispatcher replay job is removed before migration. Taxonomy ingest and
Expedition sync run from the immutable digest before legacy adoption; an initial
derived-state rebuild pass then runs before dispatcher replay is recreated.
Dispatcher replay independently excludes users with queued/running rebuilds,
so compatibility adoption cannot race an older partial projection.

Before the outbox relay is created, bootstrap drains any active moderation
queue messages through the repaired consumer. Legacy BlobCreated payloads are
fail-closed and dead-lettered; valid envelopes still require a matching
committed outbox row. The active queue must reach zero or bootstrap stops, and
the resulting DLQ count is printed for operator review/alert verification.

If preflight reports migration-managed duplicate photo/review/counter work,
review its JSON and rerun with the exact `HINTERLAND_OBSERVATION_PREFLIGHT_ACK`.
Duplicate submission keys cannot be waived.

If a Blob management policy already exists without the checked-in Observation
rules, the script stops. Merge policies manually or set
`HINTERLAND_REPLACE_LIFECYCLE_POLICY=1` only after reviewing the full account
policy.

## Fixed W1 Posture

- NoOp records `pilot_private`, not `clean`.
- No non-clean state receives a signed URL.
- CV requires enable + disclosure + benchmark gates, all false.
- Public submit stays false and has no running consumer/replay job.
- Outbox relay is the sole moderation producer.
- NoOp moves verified bytes to the private `pilot-private/` prefix; Azure
  lifecycle and database-aware retention independently enforce seven days.

The GitHub deploy workflow then runs preflight and additive migrations before
API cutover, reconciles legacy work both before consumers and after cutover,
pins every job to the same digest, syncs taxonomy and Expedition
content, and performs public probes. Smoke bearers are currently optional
secrets; their absence causes explicit skips, so physical/operator canaries
remain a release gate even when CI is green.

## Closed Beta

Do not enable Content Safety in this W1 script. Closed beta requires staged
safe/flagged/unavailable/malformed and copy/DLQ probes, concurrent review plus
deterministic rebuild, synthetic alerts, and a 24-hour or 25-submission canary.
Public iNaturalist submission stays disabled.
