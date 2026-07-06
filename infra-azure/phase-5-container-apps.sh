#!/usr/bin/env bash
# Phase 5 -- Azure Container Apps for Hinterland backend.
#
# Provisions:
#   - Log Analytics workspace `dragonfly-law-dev` (~$10/mo at default
#     5GB ingestion)
#   - User-Assigned Managed Identity `dragonfly-api-mi` with RBAC:
#       * AcrPull on `dragonflyacrdev`
#       * Key Vault Secrets User on `dragonfly-kv-dev`
#       * Storage Blob Data Contributor on `dragonflyphotosdev`
#   - Container Apps managed environment `dragonfly-cae-dev`
#   - Container App `dragonfly-api` running the Phase 4 bootstrap image
#     with min-replicas=0 so idle cost is zero. External ingress on
#     port 8080. Postgres connection wired through env vars; password
#     stored as a Container Apps secret (not yet a KV reference --
#     deferred to Phase 6 when the backend reads from KV via the UAMI).
#   - 2 KV secrets: containerapp-fqdn, containerapp-name
#
# Important: the deployed image is the Phase 4 bootstrap image which
# still points at GCP services (firebase-admin, GCS, Cloud Vision).
# `/health` responds 200 (the deploy plumbing works), but any
# authenticated route will fail until Phase 6 swaps the backend code
# to Entra / Blob / Content Safety.
#
# Idempotent. Re-running:
#   - Skips Log Analytics / UAMI / role assignments / CAE / app if
#     present.
#   - `az containerapp update` style mutations are not in this script
#     -- redeploy via Phase 6's CI workflow when the image changes.
#
# Run with:
#   MSYS_NO_PATHCONV=1 bash infra-azure/phase-5-container-apps.sh
#
# (The MSYS_NO_PATHCONV is REQUIRED on Git Bash on Windows -- otherwise
# the leading `/` of resource IDs gets path-converted to
# `C:/Program Files/Git/subscriptions/...` and breaks --user-assigned
# and --registry-identity.)

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"

LAW_NAME="dragonfly-law-dev"
UAMI_NAME="dragonfly-api-mi"
UAMI_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${UAMI_NAME}"

ACR_NAME="dragonflyacrdev"
SA_NAME="dragonflyphotosdev"

CAE_NAME="dragonfly-cae-dev"
APP_NAME="dragonfly-api"
IMAGE="${ACR_NAME}.azurecr.io/dragonfly-api:phase4-bootstrap"

# Well-known role definition GUIDs.
ROLE_ACR_PULL="7f951dda-4ed3-4680-a7ca-43fe172d538d"
ROLE_KV_SECRETS_USER="4633458b-17de-408a-b874-0445c86b69e6"
ROLE_BLOB_DATA_CONTRIBUTOR="ba92f5b4-2d11-453d-a403-e96b0029c9fe"

KV_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.KeyVault/vaults/${KV_NAME}"
SA_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.Storage/storageAccounts/${SA_NAME}"
ACR_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.ContainerRegistry/registries/${ACR_NAME}"

assert_tenant() {
  local expected="$1"
  local actual
  actual=$(az account show --query tenantId -o tsv)
  if [[ "$actual" != "$expected" ]]; then
    echo "FATAL: az current tenant is $actual, expected $expected" >&2
    exit 1
  fi
}

az account set --subscription "$MGMT_SUB"
assert_tenant "$MGMT_TENANT"

# ---------------------------------------------------------------------------
# 1. Providers
# ---------------------------------------------------------------------------

for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ManagedIdentity; do
  STATE=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$STATE" != "Registered" ]]; then
    az provider register --namespace "$ns" --subscription "$MGMT_SUB" --wait
  fi
done

# ---------------------------------------------------------------------------
# 2. Log Analytics workspace
# ---------------------------------------------------------------------------

echo "==> ensure Log Analytics workspace $LAW_NAME"
if ! az monitor log-analytics workspace show --workspace-name "$LAW_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az monitor log-analytics workspace create \
    --name "$LAW_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --sku PerGB2018 \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

# ---------------------------------------------------------------------------
# 3. User-Assigned Managed Identity
# ---------------------------------------------------------------------------

echo "==> ensure UAMI $UAMI_NAME"
if ! az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az identity create \
    --name "$UAMI_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

UAMI_PRINCIPAL=$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query principalId -o tsv)
UAMI_CLIENT_ID=$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query clientId -o tsv)

# ---------------------------------------------------------------------------
# 4. Role assignments for the UAMI (KV / Blob / ACR pull)
# ---------------------------------------------------------------------------

ensure_role() {
  local scope="$1"
  local role_def="$2"
  local description="$3"

  local existing
  existing=$(az rest --method GET \
    --url "https://management.azure.com${scope}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&\$filter=principalId%20eq%20'${UAMI_PRINCIPAL}'" \
    --subscription "$MGMT_SUB" \
    --query "value[?properties.roleDefinitionId=='/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${role_def}'].id" \
    -o tsv 2>/dev/null || true)
  if [[ -z "$existing" ]]; then
    echo "  -> granting $description"
    local ra
    ra=$(openssl rand -hex 16 | sed 's/\(........\)\(....\)\(....\)\(....\)\(............\)/\1-\2-\3-\4-\5/')
    az rest --method put \
      --url "https://management.azure.com${scope}/providers/Microsoft.Authorization/roleAssignments/${ra}?api-version=2022-04-01" \
      --subscription "$MGMT_SUB" \
      --body "{\"properties\":{\"roleDefinitionId\":\"/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${role_def}\",\"principalId\":\"${UAMI_PRINCIPAL}\",\"principalType\":\"ServicePrincipal\"}}" \
      > /dev/null
  fi
}

echo "==> ensure UAMI roles"
ensure_role "$KV_ID"  "$ROLE_KV_SECRETS_USER"       "Key Vault Secrets User"
ensure_role "$SA_ID"  "$ROLE_BLOB_DATA_CONTRIBUTOR" "Storage Blob Data Contributor"
ensure_role "$ACR_ID" "$ROLE_ACR_PULL"              "AcrPull"

# ---------------------------------------------------------------------------
# 5. Container Apps managed environment
# ---------------------------------------------------------------------------

echo "==> ensure Container Apps env $CAE_NAME"
if ! az containerapp env show --name "$CAE_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  LAW_CUSTOMER_ID=$(az monitor log-analytics workspace show --workspace-name "$LAW_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query customerId -o tsv)
  LAW_KEY=$(az monitor log-analytics workspace get-shared-keys --workspace-name "$LAW_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query primarySharedKey -o tsv)
  az containerapp env create \
    --name "$CAE_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --logs-workspace-id "$LAW_CUSTOMER_ID" \
    --logs-workspace-key "$LAW_KEY" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

# ---------------------------------------------------------------------------
# 6. Container App
# ---------------------------------------------------------------------------
#
# REQUIRES MSYS_NO_PATHCONV=1 on Git Bash so the UAMI resource ID's
# leading slash isn't path-converted.

echo "==> ensure Container App $APP_NAME"
if ! az containerapp show --name "$APP_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  PG_HOST=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-host --subscription "$MGMT_SUB" --query value -o tsv)
  PG_USER=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-admin-user --subscription "$MGMT_SUB" --query value -o tsv)
  PG_PASSWORD=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-admin-password --subscription "$MGMT_SUB" --query value -o tsv)
  PG_DB=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-database --subscription "$MGMT_SUB" --query value -o tsv)

  az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --environment "$CAE_NAME" \
    --image "$IMAGE" \
    --target-port 8080 \
    --ingress external \
    --min-replicas 0 \
    --max-replicas 3 \
    --cpu 0.5 \
    --memory 1.0Gi \
    --user-assigned "$UAMI_ID" \
    --registry-server "${ACR_NAME}.azurecr.io" \
    --registry-identity "$UAMI_ID" \
    --secrets "pg-password=${PG_PASSWORD}" \
    --env-vars \
        "DRAGONFLY_ENV=prod" \
        "DRAGONFLY_DATABASE_HOST=${PG_HOST}" \
        "DRAGONFLY_DATABASE_PORT=5432" \
        "DRAGONFLY_DATABASE_USER=${PG_USER}" \
        "DRAGONFLY_DATABASE_PASSWORD=secretref:pg-password" \
        "DRAGONFLY_DATABASE_NAME=${PG_DB}" \
        "DRAGONFLY_READINESS_DATABASE_REQUIRED=true" \
        "DRAGONFLY_MODERATION_PROVIDER=noop" \
        "DRAGONFLY_FIREBASE_PROJECT_ID=dragonflyapp-495423" \
        "DRAGONFLY_PHOTOS_BUCKET=dragonflyphotosdev" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query "properties.configuration.ingress.fqdn" -o tsv)

# ---------------------------------------------------------------------------
# 7. KV secrets
# ---------------------------------------------------------------------------

echo "==> write Container App config to $KV_NAME"
az keyvault secret set --vault-name "$KV_NAME" --name containerapp-fqdn \
  --value "$FQDN" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name containerapp-name \
  --value "$APP_NAME" --subscription "$MGMT_SUB" --query id -o tsv

echo
echo "done."
echo "  Container App:    $APP_NAME"
echo "  FQDN:             https://${FQDN}"
echo "  UAMI client id:   $UAMI_CLIENT_ID"
echo "  UAMI principal:   $UAMI_PRINCIPAL"
echo
echo "Cold-start smoke (first hit takes ~30-90s with min-replicas=0):"
echo "  curl https://${FQDN}/health"
