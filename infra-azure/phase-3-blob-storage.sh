#!/usr/bin/env bash
# Phase 3 -- Blob Storage for Hinterland photos.
#
# Provisions:
#   - Storage account `dragonflyphotosdev` in eastus2 (Standard_LRS,
#     StorageV2, Hot tier, TLS 1.2 min, no public blob access)
#   - Blob container `photos` (private)
#   - 7-day blob soft-delete
#   - Storage Blob Data Owner role for Brian on the account
#   - Three blob-* KV secrets capturing the account name / container /
#     endpoint, read by backend at boot via managed identity
#
# Idempotent. Re-running checks existence before each create. Soft-delete
# settings are re-applied (no-op when already set). Role assignments are
# created once with a stable GUID; if you need to re-issue, delete the
# existing assignment via az role assignment delete first.
#
# Run with:
#   bash infra-azure/phase-3-blob-storage.sh
#
# Prerequisites: az CLI authenticated against management tenant
# 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 (brian@dragonfly-app.net).

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"
SA_NAME="dragonflyphotosdev"
SA_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.Storage/storageAccounts/${SA_NAME}"
CONTAINER="photos"

# Storage Blob Data Owner -- read/write/delete blobs + change ACLs.
BLOB_DATA_OWNER_ROLE_DEF="b7e6dc6d-f1e8-4753-8033-0f276bb0955b"

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
# 1. Provider registration (idempotent; usually already registered)
# ---------------------------------------------------------------------------

echo "==> ensure Microsoft.Storage provider is Registered"
ST_RP_STATE=$(az provider show --namespace Microsoft.Storage --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
if [[ "$ST_RP_STATE" != "Registered" ]]; then
  az provider register --namespace Microsoft.Storage --subscription "$MGMT_SUB" --wait
fi

# ---------------------------------------------------------------------------
# 2. Storage account
# ---------------------------------------------------------------------------

echo "==> ensure storage account $SA_NAME"
if ! az storage account show --name "$SA_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az storage account create \
    --name "$SA_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --access-tier Hot \
    --min-tls-version TLS1_2 \
    --allow-blob-public-access false \
    --public-network-access Enabled \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

# ---------------------------------------------------------------------------
# 3. Blob soft-delete (7 days). Idempotent.
# ---------------------------------------------------------------------------

echo "==> ensure blob soft-delete (7 days) on $SA_NAME"
az storage account blob-service-properties update \
  --account-name "$SA_NAME" \
  --resource-group "$RG" \
  --subscription "$MGMT_SUB" \
  --enable-delete-retention true \
  --delete-retention-days 7 \
  --output none

# ---------------------------------------------------------------------------
# 4. Brian -> Storage Blob Data Owner on the account
# ---------------------------------------------------------------------------

echo "==> ensure Storage Blob Data Owner role for Brian on $SA_NAME"
USER_OID=$(az ad signed-in-user show --query id -o tsv)
EXISTING_ROLE=$(az rest --method GET \
  --url "https://management.azure.com${SA_ID}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&\$filter=principalId%20eq%20'${USER_OID}'" \
  --subscription "$MGMT_SUB" \
  --query "value[?properties.roleDefinitionId=='/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${BLOB_DATA_OWNER_ROLE_DEF}'].id" -o tsv 2>/dev/null || true)
if [[ -z "$EXISTING_ROLE" ]]; then
  RA_GUID=$(openssl rand -hex 16 | sed 's/\(........\)\(....\)\(....\)\(....\)\(............\)/\1-\2-\3-\4-\5/')
  az rest --method put \
    --url "https://management.azure.com${SA_ID}/providers/Microsoft.Authorization/roleAssignments/${RA_GUID}?api-version=2022-04-01" \
    --subscription "$MGMT_SUB" \
    --body "{\"properties\":{\"roleDefinitionId\":\"/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${BLOB_DATA_OWNER_ROLE_DEF}\",\"principalId\":\"${USER_OID}\",\"principalType\":\"User\"}}" \
    > /dev/null
  echo "  -> role assigned; waiting 30s for propagation before container create"
  sleep 30
fi

# ---------------------------------------------------------------------------
# 5. Blob container `photos` (private)
# ---------------------------------------------------------------------------

echo "==> ensure container $CONTAINER (auth-mode login uses the new role)"
if ! az storage container show --name "$CONTAINER" --account-name "$SA_NAME" --subscription "$MGMT_SUB" --auth-mode login >/dev/null 2>&1; then
  az storage container create \
    --name "$CONTAINER" \
    --account-name "$SA_NAME" \
    --subscription "$MGMT_SUB" \
    --auth-mode login \
    --public-access off \
    > /dev/null
fi

# ---------------------------------------------------------------------------
# 6. blob-* config secrets in KV
# ---------------------------------------------------------------------------

echo "==> write blob-* config secrets to $KV_NAME"
az keyvault secret set --vault-name "$KV_NAME" --name blob-account-name \
  --value "$SA_NAME" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name blob-photos-container \
  --value "$CONTAINER" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name blob-account-endpoint \
  --value "https://${SA_NAME}.blob.core.windows.net" --subscription "$MGMT_SUB" --query id -o tsv

echo
echo "done."
echo "  Storage account:  $SA_NAME"
echo "  Endpoint:         https://${SA_NAME}.blob.core.windows.net"
echo "  Container:        $CONTAINER (private)"
echo
echo "Smoke test (uses your blob-data-owner role for auth):"
echo "  az storage blob list --account-name $SA_NAME --container-name $CONTAINER --auth-mode login -o table"
echo
echo "Data migration from GCS (out-of-band, only if dev bucket has data"
echo "worth preserving):"
echo "  azcopy copy \"gs://dragonfly-photos-dev-dragonflyapp-495423/*\" \\"
echo "    \"https://${SA_NAME}.blob.core.windows.net/${CONTAINER}\" --recursive=true"
echo "  Source auth requires GOOGLE_APPLICATION_CREDENTIALS pointing at a"
echo "  GCS service-account key with read access to the bucket."
