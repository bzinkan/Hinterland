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

## Dev Plan

```bash
cd infra-gcp
terraform init
terraform plan -var-file=environments/dev.tfvars
```

Staging/prod plans use `environments/staging.tfvars` and
`environments/prod.tfvars` after those projects exist.

The dev project already has a manually-created `dragonfly-api` service. Before
the first Terraform apply in dev, import it or intentionally replace it:

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
