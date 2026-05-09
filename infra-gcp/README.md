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
bucket was created via `gcloud storage buckets create` on 2026-05-07. The
`roles/storage.objectAdmin` binding for the deploy SA on that bucket is
also out-of-band and should be imported + codified together with the
bucket itself in a future PR.

The other deploy-SA bindings that were temporarily out-of-band are now in
this Terraform root: `roles/iam.securityReviewer` and `roles/viewer` at
project scope (for `terraform plan` refresh reads), and
`roles/artifactregistry.repoAdmin` on the `dragonfly` repo (for the
deploy workflow's `:latest` tag step). The runtime SA's
`roles/firebaseauth.admin` (for Admin SDK user-mgmt calls) and
`roles/iam.serviceAccountTokenCreator` self-binding (for
`create_custom_token` via `iam.signBlob`) are also codified.

The `roles/orgpolicy.policyAdmin` grant for `brian@dragonfly-app.net` at
the **organization** level is intentionally NOT in this Terraform root
— managing org-level IAM from a project-scoped Terraform is unusual and
that grant is a one-time bootstrap.

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

- Cloud Run public access is opt-in per environment via `var.cloud_run_public`
  (see ADR 0008). Setting it to `true` overrides
  `iam.allowedPolicyMemberDomains` at project scope so `allUsers` becomes a
  valid IAM principal; the actual auth boundary becomes Firebase ID token
  verification in `app/core/auth.py`. Dev sets `cloud_run_public = true` and
  includes `allUsers` in `cloud_run_invoker_members`. Staging and prod remain
  restricted until per-env ADRs decide otherwise.
- The first apply of `google_org_policy_policy` requires
  `roles/orgpolicy.policyAdmin` at project scope. The `github-deploy-dev`
  service account does not have this role, so the org-policy override must
  be applied locally by a project Owner the first time. Subsequent applies
  that don't touch the policy succeed via the SA's existing roles.
- `DRAGONFLY_DATABASE_PASSWORD` is mounted from Secret Manager into Cloud Run.
- Firebase Authentication is enabled at the service API level here. Firebase
  app/provider configuration should land in the auth milestone.
