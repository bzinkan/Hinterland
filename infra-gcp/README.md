# Dragonfly GCP Infrastructure

Terraform is the source of truth for durable GCP resources.

Environment isolation is project-per-env:

- `dragonflyapp-495423` for dev
- `dragonflyapp-staging` for staging
- `dragonflyapp-prod` for prod

If the Workspace admin cannot create staging/prod projects yet, use only the
dev tfvars until those projects exist. Do not silently convert the production
plan to one-project env-suffixed resources.

The Cloud Run service name is `dragonfly-api` in every project. Project
boundaries carry the environment, so scripts should discover URLs through:

```bash
gcloud run services describe dragonfly-api --format='value(status.url)'
```

This root module provisions the closed-beta foundation:

- Cloud Run API service
- Artifact Registry repository
- Cloud SQL for PostgreSQL
- Cloud Storage photo bucket with lifecycle rules
- Secret Manager database password
- API and GitHub deploy service accounts
- GitHub Workload Identity Federation
- baseline Monitoring and optional budget resources

## Remote state

State lives in GCS, in `gs://dragonflyapp-tfstate/infra-gcp/default.tfstate`.
The bucket is in `dragonflyapp-495423`, has versioning enabled, uniform
bucket-level access, and public access prevention enforced. The `gcs` backend
provides native state locking — no external lock table needed.

A fresh clone needs no special bucket bootstrap; `terraform init` reads the
backend block in `versions.tf` and authenticates via Application Default
Credentials. If you hit `oauth2: invalid_rapt`, run
`gcloud auth application-default login` to refresh the Workspace re-auth.

**The state bucket itself is currently NOT managed by this Terraform root**
(chicken-and-egg with backend bootstrap). The `gs://dragonflyapp-tfstate`
bucket was created via `gcloud storage buckets create` on 2026-05-07.

Importing the existing dev Cloud Run service into state added refresh
reads on resources the deploy SA could previously skip. The deploy SA
`github-deploy-dev@dragonflyapp-495423.iam.gserviceaccount.com` was
granted these out-of-band roles to make `terraform plan` work in CI:

- `roles/storage.objectAdmin` on `gs://dragonflyapp-tfstate` — read state +
  acquire locks
- `roles/iam.securityReviewer` at project scope — `iam.serviceAccounts.getIamPolicy`
  for SA IAM refresh
- `roles/viewer` at project scope — broader resource refresh reads
  (workload identity pools, etc.) that the SA's apply-only role bundle
  didn't include

A follow-up PR should import the state bucket and codify all three
bindings in this Terraform root, then drop the broad `roles/viewer`
in favor of the narrower service-specific viewer roles.

## Dev Plan

```bash
cd infra-gcp
terraform init
terraform plan -var-file=environments/dev.tfvars
```

Staging/prod plans use `environments/staging.tfvars` and
`environments/prod.tfvars` after those projects exist.

The dev `dragonfly-api` Cloud Run service was originally created via
`gcloud run deploy --source` and was imported into Terraform state on
2026-05-07 (commit on branch `chore/terraform-remote-state`). Subsequent
applies will manage it in place; `terraform plan` should show only
intentional changes, never a recreation. If you need to re-import after a
state recovery, the command is:

```bash
terraform import \
  -var-file=environments/dev.tfvars \
  google_cloud_run_v2_service.api \
  projects/dragonflyapp-495423/locations/us-central1/services/dragonfly-api
```

The deploy workflow expects these GitHub secrets after the first Terraform
apply:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`

Use the Terraform outputs `github_workload_identity_provider` and
`github_deploy_service_account` for those values.

## Notes

- Cloud Run is not public by default. `dev.tfvars` grants invoker access to the
  `dragonfly-app.net` Workspace domain.
- `DRAGONFLY_DATABASE_PASSWORD` is mounted from Secret Manager into Cloud Run.
- Firebase Authentication is enabled at the service API level here. Firebase
  app/provider configuration should land in the auth milestone.
