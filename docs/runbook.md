# Dragonfly GCP Runbook

This runbook covers the closed-beta GCP path. The AWS CDK path in `infra/` is
legacy reference only.

## Smoke Test Cloud Run

The canonical URL for dev is the Cloud DNS-mapped custom domain. Hit the
three platform probes from any shell — no auth header required, since
[ADR 0008](adr/0008-public-cloud-run-with-firebase-enforcement.md) made
dev `/health`, `/ready`, and `/v1/meta` publicly invokable:

```bash
curl -fsS https://api.dragonfly-app.net/health
curl -fsS https://api.dragonfly-app.net/ready
curl -fsS https://api.dragonfly-app.net/v1/meta
```

Each should return a JSON body with HTTP 200. If any returns 403, ADR 0008's
Terraform was never applied (or the org policy override regressed); re-run
the targeted `terraform apply` for `google_org_policy_policy.domain_restricted_sharing`
and `google_cloud_run_v2_service_iam_member.api_invokers`.

The Cloud Run-assigned URL (`https://dragonfly-api-<hash>-uc.a.run.app`) is
also valid but is an implementation detail. Discover it via
`gcloud run services describe dragonfly-api --region us-central1
--format='value(status.url)'` if needed.

Once Phase 4 lands (parent signup, group create, kid provisioning), every
endpoint other than the three platform probes will require a Firebase ID
token in `Authorization: Bearer ...`. Auth is enforced at the application
layer; the IAM gate stays open for `allUsers` so mobile clients can reach
the service without a Google identity.

## Phase 4 End-to-End Smoke (Postman Equivalent)

`scripts/smoke_phase4.py` runs the full Phase 4 round-trip end-to-end:

1. Firebase signUp creates a parent (Firebase REST, public Web API key).
2. `POST /v1/auth/parent-signup` materializes the `users` row + sets the
   Firebase custom claim `role=parent`.
3. Force-refresh the parent's ID token so the new claim takes effect.
4. `POST /v1/groups` creates the family group, returns a 6-char join code.
5. `POST /v1/groups/{group_id}/kids` admin-creates a kid via the Firebase
   Admin SDK on the server side, returns a Firebase custom token.
6. `signInWithCustomToken` exchanges the kid's custom token for an ID token.
7. `GET /v1/me` as the kid asserts the kid's identity context.

Run:

```bash
python scripts/smoke_phase4.py
```

Stdlib only -- no third-party deps. Defaults target the dev Cloud Run service
(`api.dragonfly-app.net`) and the dev Firebase Web API key. Override via env
vars (`DRAGONFLY_API_BASE_URL`, `DRAGONFLY_FIREBASE_API_KEY`,
`DRAGONFLY_SMOKE_EMAIL`, `DRAGONFLY_SMOKE_PASSWORD`).

**Preconditions for a green run:**

- Firebase Auth is configured on `dragonflyapp-495423` with Email/Password
  sign-in enabled (see `docs/auth.md` once written, or `dragonfly.md` in
  agent memory).
- ADR 0008 Terraform has been applied so `/v1/auth/parent-signup` is publicly
  reachable (the script does not pass a Google identity token).
- **Cloud SQL is provisioned and connected.** Steps 2/4/5 write to Postgres
  and will 500 if the API service has no DB. As of 2026-05-08 the dev
  Cloud SQL instance is not yet provisioned (the `db-g1-small` tier is
  rejected by ENTERPRISE_PLUS edition; needs a `db-perf-optimized-N-*`
  tier in `infra-gcp/environments/dev.tfvars`).

**Test data left behind:** every run creates a parent + a kid in Firebase
Auth and the corresponding rows in Postgres. They accumulate. Periodic
cleanup is a follow-up. The test email pattern is `smoke+<ts>@dragonfly-test.invalid`
so leakage is grep-friendly.

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

The `dragonfly-migrate` Cloud Run Job already exists and runs `alembic
upgrade head` against `dragonfly-postgres-dev` using the runtime SA's
Cloud SQL connection. Re-execute it after any new migration:

```bash
TOKEN=$(gcloud auth application-default print-access-token)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "X-Goog-User-Project: dragonflyapp-495423" \
  "https://run.googleapis.com/v2/projects/dragonflyapp-495423/locations/us-central1/jobs/dragonfly-migrate:run" \
  -d '{}'
```

## Cleanup Smoke Test Users

`scripts/smoke_phase4.py` runs after every dev deploy (per the workflow's
`Phase 4 end-to-end smoke` step) and creates a parent + a kid in Firebase
Auth and the corresponding rows in Postgres. Email pattern:
`smoke+<ts>@dragonfly-test.invalid`.

`backend/admin/cleanup_smoke_users.py` deletes the accumulated users from
both Firebase and Postgres in the right FK order. Run as a Cloud Run Job
using the same image as `dragonfly-api`. Create the job once (or per
re-image), then re-execute as needed.

Create the job (one-time, or after any change to the cleanup script):

```bash
PROJECT=dragonflyapp-495423
SA=dragonfly-api-dev@$PROJECT.iam.gserviceaccount.com
INSTANCE=$PROJECT:us-central1:dragonfly-postgres-dev
IMAGE=us-central1-docker.pkg.dev/$PROJECT/dragonfly/dragonfly-api:latest
TOKEN=$(gcloud auth application-default print-access-token)

# Replace the job spec; PUT-style upsert by re-creating after delete if it
# already exists, or use --jobId to a new name.
curl -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: $PROJECT" \
  "https://run.googleapis.com/v2/projects/$PROJECT/locations/us-central1/jobs?jobId=dragonfly-cleanup-smoke" \
  -d "{
    \"labels\": {\"purpose\": \"cleanup-smoke\"},
    \"template\": {\"template\": {
      \"serviceAccount\": \"$SA\",
      \"maxRetries\": 0,
      \"timeout\": \"600s\",
      \"volumes\": [{\"name\": \"cloudsql\", \"cloudSqlInstance\": {\"instances\": [\"$INSTANCE\"]}}],
      \"containers\": [{
        \"image\": \"$IMAGE\",
        \"command\": [\"python\", \"-m\", \"admin.cleanup_smoke_users\"],
        \"env\": [
          {\"name\": \"DRAGONFLY_ENV\", \"value\": \"dev\"},
          {\"name\": \"DRAGONFLY_DATABASE_HOST\", \"value\": \"/cloudsql/$INSTANCE\"},
          {\"name\": \"DRAGONFLY_DATABASE_NAME\", \"value\": \"dragonfly\"},
          {\"name\": \"DRAGONFLY_DATABASE_USER\", \"value\": \"dragonfly\"},
          {\"name\": \"DRAGONFLY_CLOUD_SQL_INSTANCE\", \"value\": \"$INSTANCE\"},
          {\"name\": \"DRAGONFLY_DATABASE_PASSWORD\", \"valueSource\": {\"secretKeyRef\": {\"secret\": \"dragonfly-dev-database-password\", \"version\": \"latest\"}}}
        ],
        \"volumeMounts\": [{\"name\": \"cloudsql\", \"mountPath\": \"/cloudsql\"}]
      }]
    }}
  }"
```

Run the job (idempotent; safe to call repeatedly):

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "X-Goog-User-Project: $PROJECT" \
  "https://run.googleapis.com/v2/projects/$PROJECT/locations/us-central1/jobs/dragonfly-cleanup-smoke:run" \
  -d '{}'
```

Job logs (Cloud Logging) will show the `cleanup_smoke.*` structured
events: `discovered`, `parent_pg_ids`, `kid_pg_ids`, the four delete
counts, and `deleted_firebase_users`.

A Cloud Scheduler cron (`dragonfly-cleanup-smoke-nightly`, codified in
`infra-gcp/main.tf`, dev only) fires the job nightly at 09:00 UTC
(04:00 America/Chicago CDT). The schedule and the
`dragonfly-scheduler-dev` service account that invokes it are managed
by Terraform; the job spec itself is still out-of-band (importing it is
a follow-up). Manual `:run` calls remain safe to interleave with the
nightly cron — the script is idempotent.

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
