# Dragonfly GCP Runbook

This runbook covers the closed-beta GCP path. The AWS CDK path in `infra/` is
legacy reference only.

## Smoke Test Cloud Run

```bash
gcloud config set project dragonflyapp-495423
gcloud config set run/region us-central1

URL="$(gcloud run services describe dragonfly-api --format='value(status.url)')"
TOKEN="$(gcloud auth print-identity-token)"

curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/health"
curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/ready"
curl -fsS -H "Authorization: Bearer $TOKEN" "$URL/v1/meta"
```

Use the URL returned by `status.url`. Cloud Run may also print alternate URL
formats during deploy, but scripts should not hard-code either hash format.
For service-account smoke tests, an audience-scoped token is fine:
`gcloud auth print-identity-token --audiences="$URL"`.

The `dragonfly-app.net` organization policy may reject public
`allUsers` access. Prefer authenticated smoke tests until Firebase Auth owns
the product auth boundary.

## Deploy Dev

Dev deploys are handled by `.github/workflows/deploy-cloud-run-dev.yml` after
Terraform has created the Cloud Run service, Artifact Registry repo, and GitHub
Workload Identity Federation.

Required GitHub secrets:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`

Use Terraform outputs from `infra-gcp` for both values.

Manual dev deploy from PowerShell:

```powershell
cd C:\GitHub\Dragonfly\backend
gcloud run deploy dragonfly-api `
  --source . `
  --region us-central1 `
  --project dragonflyapp-495423 `
  --set-env-vars="DRAGONFLY_ENV=dev,DRAGONFLY_READINESS_DATABASE_REQUIRED=false"
```

Keep `--set-env-vars="..."` quoted in PowerShell. Without quotes, Cloud Run may
receive one malformed environment value.

## Roll Back Cloud Run

```bash
gcloud run revisions list --service dragonfly-api --region us-central1
gcloud run services update-traffic dragonfly-api \
  --region us-central1 \
  --to-revisions REVISION_NAME=100
```

After rollback, run the smoke probes and check Cloud Logging for
`api.startup`, `api.shutdown`, and any `api.unhandled_exception` entries.

## Apply Database Migrations

For local development:

```bash
make dev-db
make db-migrate
```

For Cloud Run environments, run migrations from a controlled deploy job or
one-off admin machine with the same `DRAGONFLY_DATABASE_*` settings as the
target service. Do not run migrations from app startup.

## Restore Cloud SQL

1. Identify the target restore time.
2. Create a point-in-time restored instance in Cloud SQL.
3. Run read-only verification queries against the restored instance.
4. Promote by updating Terraform or Cloud Run database settings only after the
   restored instance is verified.
5. Run `/ready` and a read-only API smoke test.

## Replay Failed Ingest

Failed ingest jobs are discoverable in Postgres:

```sql
select id, source, source_run_id, cursor, retry_count, last_error
from ingest_runs
where status = 'failed'
order by updated_at desc;
```

Replay scripts must accept `source` plus `source_run_id` or cursor range, then
write a new or updated `ingest_runs` row. Replays must be idempotent.

## iNaturalist Submit DLQ

Signal: Cloud Tasks queue depth for the iNaturalist DLQ is above zero.

Triage:

1. Classify failures from worker logs by observation ID.
2. If iNaturalist is down, pause redrive and wait.
3. If credentials expired, rotate the Secret Manager secret and redeploy the
   worker.
4. If a photo was quarantined, mark the observation as abandoned for iNat
   submission and do not retry.
5. Redrive only after the root cause is fixed.

## Moderation Review

Clean photos move from `pending/` to `observations/`. Flagged photos move to
`quarantine/` and create `review_queue` rows.

Teacher/adult actions:

- `approved`: move or copy the photo to `observations/`, resolve the review row,
  and enqueue iNaturalist submission if appropriate.
- `rejected`: keep the photo hidden, resolve the review row, and mark the
  observation as rejected or abandoned.

## Monitoring Signals

Alert on:

- Cloud Run API 5xx responses
- Cloud Run latency regression
- Cloud SQL connection pressure
- iNaturalist DLQ depth
- moderation DLQ or failed Eventarc handling
- ingest failures
- rarity job duration
- observations without Dex rows older than one hour
- budget threshold crossings

Dashboard, but do not page on:

- observations per day
- Cloud Run startup count
- cache hit rates
- expedition completion rates
- iNaturalist CV confidence distribution

## Runtime AI Violation

Any live LLM or agent call from the kid-facing API path violates ADR 0002 and
ADR 0007.

Immediate response:

1. Identify the route or worker from logs.
2. Roll back the revision.
3. Add or fix a test that prevents the import/call path.
4. File an ADR variance only if the product direction is intentionally changing.
