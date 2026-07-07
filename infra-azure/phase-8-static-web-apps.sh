#!/usr/bin/env bash
# Phase 8 -- Azure Static Web Apps for Hinterland frontends.
#
# Provisions:
#   - hinterland-landing-swa -> serves thehinterlandguide.app + www (web/public)
#   - hinterland-parents-swa -> serves parents.thehinterlandguide.app (mobile web bundle)
#   - 4 KV secrets capturing the default hostnames + the deployment tokens.
#
# Idempotent.
#
# Run with:
#   bash infra-azure/phase-8-static-web-apps.sh
#
# Prerequisites: az CLI authenticated against the Azure tenant with access to
# the target resource group.
# Also: gh CLI authenticated against the bzinkan/Hinterland repo so
# the deployment tokens can be written to GitHub Actions secrets.

set -euo pipefail

MGMT_SUB="3ac5dfb0-91b7-47d3-8187-9dc8d6305e96"
MGMT_TENANT="18dbd7fa-c411-49bc-82fc-9ccaa26e3404"
RG="hinterland-dev-rg"
LOCATION="eastus2"
KV_NAME="hinterland-kv-dev"

LANDING_SWA="hinterland-landing-swa"
PARENTS_SWA="hinterland-parents-swa"

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
      --tags project=hinterland env=dev managed-by=cli \
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
echo "Custom domains should be bound to the Azure-backed thehinterlandguide.app"
echo "records before this host is used for app/store links."
