#!/usr/bin/env bash
# Phase 4 -- Azure Container Registry for Dragonfly backend image.
#
# Provisions:
#   - ACR `dragonflyacrdev` (Basic SKU, ~$5/mo) in eastus2
#   - Image build of backend/Dockerfile pushed as
#     dragonfly-api:phase4-bootstrap (image still points at GCP services
#     -- code-side cutover lands in Phase 6)
#   - One KV secret: acr-login-server
#
# Idempotent. Re-running checks existence before create. Re-running
# `az acr build` produces a new image layer / digest -- safe.
#
# Run with:
#   bash infra-azure/phase-4-container-registry.sh
#
# Prerequisites: az CLI authenticated against management tenant
# 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 (brian@dragonfly-app.net).

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"

ACR_NAME="dragonflyacrdev"
ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"
IMAGE_TAG="dragonfly-api:phase4-bootstrap"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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
# 1. Provider registration
# ---------------------------------------------------------------------------

echo "==> ensure Microsoft.ContainerRegistry provider is Registered"
ACR_RP_STATE=$(az provider show --namespace Microsoft.ContainerRegistry --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
if [[ "$ACR_RP_STATE" != "Registered" ]]; then
  az provider register --namespace Microsoft.ContainerRegistry --subscription "$MGMT_SUB" --wait
fi

# ---------------------------------------------------------------------------
# 2. ACR (Basic SKU)
# ---------------------------------------------------------------------------

echo "==> ensure ACR $ACR_NAME (Basic SKU)"
if ! az acr show --name "$ACR_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az acr create \
    --name "$ACR_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --sku Basic \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

# ---------------------------------------------------------------------------
# 3. Build + push backend image (Azure-side build, no local Docker needed)
# ---------------------------------------------------------------------------
#
# The image is built FROM THE CURRENT TIP OF MAIN -- whatever the
# backend/Dockerfile sees, including the still-GCP-pointed code in
# app/core/auth.py + app/core/storage.py + app/moderation/provider.py.
# This image will deploy in Phase 5 but the app won't be healthy until
# Phase 6 code lands. That's deliberate: the deploy path is what we want
# to validate now; the runtime correctness is Phase 6's job.

echo "==> build + push $IMAGE_TAG (Azure-side build)"
az acr build \
  --registry "$ACR_NAME" \
  --subscription "$MGMT_SUB" \
  --image "$IMAGE_TAG" \
  --file backend/Dockerfile \
  "${REPO_ROOT}/backend"

# ---------------------------------------------------------------------------
# 4. KV secret
# ---------------------------------------------------------------------------

echo "==> write acr-login-server to $KV_NAME"
az keyvault secret set --vault-name "$KV_NAME" --name acr-login-server \
  --value "$ACR_LOGIN_SERVER" --subscription "$MGMT_SUB" --query id -o tsv

echo
echo "done."
echo "  Registry:       $ACR_LOGIN_SERVER"
echo "  Pushed image:   $ACR_LOGIN_SERVER/$IMAGE_TAG"
echo
echo "List image tags:"
echo "  az acr repository show-tags --name $ACR_NAME --repository dragonfly-api -o tsv"
