# The Hinterland Guide Azure Environment

Azure is the only active runtime for The Hinterland Guide. All live resources
are in resource group `hinterland-dev-rg` in the Gordi subscription and use
the `hinterland` namespace.

The active service surface is:

- `hinterland-api` on Azure Container Apps
- `hinterland-postgres-dev` and `hinterlandphotosdev`
- `hinterland-kv-dev`, `hinterlandacrdev`, and `hinterland-sb-dev`
- `hinterland-landing-swa` and `hinterland-parents-swa`

Deploy the API with `.github/workflows/deploy-azure-api-dev.yml`. The workflow
builds in ACR, runs the required Container Apps jobs, updates the API, and
smokes the public Hinterland endpoints. Configuration uses `HINTERLAND_*`
variables only; secrets remain in Key Vault or GitHub Actions secrets.

Bootstrap and retired-platform scripts are intentionally absent. Recreate or
change Azure resources through reviewed infrastructure work, using the names
in `environments/hinterland-dev.env` and the current deployment workflow as
the source of truth.
