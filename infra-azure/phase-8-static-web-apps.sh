#!/usr/bin/env bash
# Phase 8 -- Azure Static Web Apps for Hinterland frontends.
#
# Provisions:
#   - dragonfly-landing-swa  -> serves dragonfly-app.net + www (web/public)
#   - dragonfly-parents-swa  -> serves parents.dragonfly-app.net (mobile web bundle)
#   - 4 KV secrets capturing the default hostnames + the deployment tokens.
#
# Idempotent.
#
# Run with:
#   bash infra-azure/phase-8-static-web-apps.sh
#
# Prerequisites: az CLI authenticated against management tenant
# 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 (brian@dragonfly-app.net).
# Also: gh CLI authenticated against the bzinkan/Hinterland repo so
# the deployment tokens can be written to GitHub Actions secrets.

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"

LANDING_SWA="dragonfly-landing-swa"
PARENTS_SWA="dragonfly-parents-swa"

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

ensure_swa() {
  local name="$1"
  if ! az staticwebapp show --name "$name" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    echo "==> create $name"
    az staticwebapp create \
      --name "$name" \
      --resource-group "$RG" \
      --subscription "$MGMT_SUB" \
      --location "$LOCATION" \
      --sku Free \
      --tags project=dragonfly env=dev managed-by=cli \
      --output none
  fi
}

ensure_swa "$LANDING_SWA"
ensure_swa "$PARENTS_SWA"

LANDING_HOST=$(az staticwebapp show --name "$LANDING_SWA" --resource-group "$RG" --subscription "$MGMT_SUB" --query "defaultHostname" -o tsv)
PARENTS_HOST=$(az staticwebapp show --name "$PARENTS_SWA" --resource-group "$RG" --subscription "$MGMT_SUB" --query "defaultHostname" -o tsv)

echo "==> save default hostnames to KV"
az keyvault secret set --vault-name "$KV_NAME" --name swa-landing-hostname \
  --value "$LANDING_HOST" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name swa-parents-hostname \
  --value "$PARENTS_HOST" --subscription "$MGMT_SUB" --query id -o tsv

echo "==> fetch deployment tokens"
LANDING_TOKEN=$(az staticwebapp secrets list --name "$LANDING_SWA" --resource-group "$RG" --subscription "$MGMT_SUB" --query "properties.apiKey" -o tsv)
PARENTS_TOKEN=$(az staticwebapp secrets list --name "$PARENTS_SWA" --resource-group "$RG" --subscription "$MGMT_SUB" --query "properties.apiKey" -o tsv)

echo "==> stash deployment tokens in KV (also pushed to GitHub Actions secrets out-of-band)"
az keyvault secret set --vault-name "$KV_NAME" --name swa-landing-deployment-token \
  --value "$LANDING_TOKEN" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name swa-parents-deployment-token \
  --value "$PARENTS_TOKEN" --subscription "$MGMT_SUB" --query id -o tsv

echo "==> push tokens to GitHub Actions repo secrets"
if command -v gh >/dev/null 2>&1; then
  echo "$LANDING_TOKEN" | gh secret set AZURE_LANDING_SWA_TOKEN > /dev/null
  echo "$PARENTS_TOKEN" | gh secret set AZURE_PARENTS_SWA_TOKEN > /dev/null
  echo "  -> secrets AZURE_LANDING_SWA_TOKEN + AZURE_PARENTS_SWA_TOKEN set"
else
  echo "  -> gh CLI not on PATH; set secrets manually:"
  echo "     gh secret set AZURE_LANDING_SWA_TOKEN --body \"<token>\""
  echo "     gh secret set AZURE_PARENTS_SWA_TOKEN --body \"<token>\""
fi

echo
echo "done."
echo "  Landing SWA:  https://${LANDING_HOST}"
echo "  Parents SWA:  https://${PARENTS_HOST}"
echo
echo "Custom domain wiring (apex + www + parents) lands in Phase 9 along"
echo "with the Cloud DNS repoint."
