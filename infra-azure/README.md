# The Hinterland Guide Azure Environment

Azure is the only active runtime for The Hinterland Guide. All live resources
are in resource group `hinterland-dev-rg` in the Gordi subscription and use
the `hinterland` namespace.

The active service surface is:

- `hinterland-api-central` in `hinterland-cae-central-dev` (Central US), the
  canonical API co-located with PostgreSQL
- `hinterland-api` in `hinterland-cae-dev` (East US), retained as the
  same-digest rollback API
- `hinterland-postgres-dev` and `hinterlandphotosdev`
- `hinterland-kv-dev`, `hinterlandacrdev`, and `hinterland-sb-dev`
- `hinterland-landing-swa` and `hinterland-parents-swa`

Deploy the API with `.github/workflows/deploy-azure-api-dev.yml`. The workflow
builds in ACR, runs the required East US Container Apps jobs, updates both API
apps from the same immutable digest, and
smokes the public Hinterland endpoints. Configuration uses `HINTERLAND_*`
variables only; secrets remain in Key Vault or GitHub Actions secrets.

`api-central-colocation.ps1` is the idempotent provisioning/verification
artifact for the additive Central environment, identity, and API. It never
creates jobs, changes DNS, binds certificates, or deletes the East rollback.
See [ADR 0016](../docs/adr/0016-central-us-api-colocation.md).

That workflow is the ordinary development path. Observation W1 promotion uses
the separate, protected `.github/workflows/observation-w1-promotion.yml`. It
restores the non-skippable containment, authenticated canary, job/digest,
lifecycle, database-health, and monitoring checks without restoring retired
platform scripts or old environment-variable aliases. See
`docs/observation-w1-promotion.md`.

`observation-w1-monitoring.sh` is the supported provisioning/audit artifact for
Observation operational alerts:

```bash
HINTERLAND_ALERT_EMAIL="ops@example.invalid" \
bash infra-azure/observation-w1-monitoring.sh --apply --verify --synthetic
```

It is isolated to `hinterland-dev-rg` and refuses `gordi-pilot-rg`. Its optional
evidence output contains only alert counts, action-group name, timestamp, and
test acceptance; email receivers are not written to the artifact.

Bootstrap and retired-platform scripts are intentionally absent. Recreate or
change Azure resources through reviewed infrastructure work, using the names
in `environments/hinterland-dev.env`, the ordinary deploy workflow, the W1
promotion workflow, and the monitoring artifact as the source of truth.
