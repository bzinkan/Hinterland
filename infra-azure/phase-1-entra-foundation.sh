#!/usr/bin/env bash
# Phase 1 -- Entra foundation for Hinterland (ADR 0010).
#
# Builds on phase-1-external-id-tenant.sh (which created the CIAM tenant).
# This script provisions:
#   - Key Vault dragonfly-kv-dev (management tenant/subscription)
#   - RBAC role 'Key Vault Secrets Officer' on Brian for the vault
#   - App registration 'dragonfly-api' in the CIAM tenant
#       * identifier URI api://dragonfly-api
#       * v2 access tokens
#       * user.access OAuth2 permission scope (fixed GUID)
#   - App registration 'dragonfly-client' in the CIAM tenant
#       * SPA + public-client redirect URIs
#       * fallback-public-client = true
#   - Pre-authorized application grant linking client -> api (user.access)
#   - RS256 kid-handoff JWT keypair in Key Vault (kid = k1-2026-06)
#   - Four entra-* config secrets in Key Vault
#
# Idempotent. Re-running it should produce no changes if the resources
# already exist with the expected shape.
#
# Run with:
#   bash infra-azure/phase-1-entra-foundation.sh
#
# Prerequisites: az CLI authenticated against BOTH tenants
# (management 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 and CIAM
# dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f) as an account with admin access
# to both tenants.
# openssl must be on PATH for kid JWT keypair generation.

set -euo pipefail

cat >&2 <<'MSG'
FATAL: phase-1-entra-foundation.sh is a historical Dragonfly bootstrap script.

The active Hinterland app registrations, tenant, Key Vault, and kid-token
settings are recorded in infra-azure/entra/manifest.json. ADR 0014 removed the
old Firebase/GCP rollback path and this script still contains Dragonfly-era
tenant/resource identifiers, so it must not be run for current environments.
MSG
exit 1

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
CIAM_TENANT="dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f"
# Az treats the CIAM tenant id as a placeholder "subscription" so that
# `--subscription $CIAM_PLACEHOLDER_SUB` routes to the credential in that tenant
# for `az rest` calls.
CIAM_PLACEHOLDER_SUB="$CIAM_TENANT"

RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"
KV_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.KeyVault/vaults/${KV_NAME}"
KV_URI="https://${KV_NAME}.vault.azure.net/"

# Well-known role definition GUID for "Key Vault Secrets Officer".
KV_SECRETS_OFFICER_ROLE_DEF="b86a8fe4-44ce-4948-aee5-eccb2c155cd7"

API_APP_NAME="dragonfly-api"
API_IDENTIFIER_URI="api://dragonfly-api"
# LOCKED. Do not regenerate -- this GUID is load-bearing for the
# pre-authorized-application grant and is referenced by mobile + backend.
API_SCOPE_USER_ACCESS_GUID="7a4fc048-4930-eb02-b9df-179c2f8e0fb2"

CLIENT_APP_NAME="dragonfly-client"

KID="k1-2026-06"

# ---------------------------------------------------------------------------
# Tenant-context assertion helpers
# ---------------------------------------------------------------------------

assert_tenant() {
  local expected="$1"
  local actual
  actual=$(az account show --query tenantId -o tsv)
  if [[ "$actual" != "$expected" ]]; then
    echo "FATAL: az current tenant is $actual, expected $expected" >&2
    echo "       Run: az account set --subscription <sub-in-expected-tenant>" >&2
    exit 1
  fi
}

set_mgmt_context() {
  az account set --subscription "$MGMT_SUB"
  assert_tenant "$MGMT_TENANT"
}

set_ciam_context() {
  az account set --subscription "$CIAM_PLACEHOLDER_SUB"
  assert_tenant "$CIAM_TENANT"
}

# ---------------------------------------------------------------------------
# 1. Key Vault (management tenant)
# ---------------------------------------------------------------------------

set_mgmt_context

echo "==> register Microsoft.KeyVault provider on management subscription"
KV_RP_STATE=$(az provider show --namespace Microsoft.KeyVault --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
if [[ "$KV_RP_STATE" != "Registered" ]]; then
  az provider register --namespace Microsoft.KeyVault --subscription "$MGMT_SUB" --wait
fi

echo "==> ensure Key Vault $KV_NAME exists (RBAC auth, standard SKU)"
if ! az keyvault show --name "$KV_NAME" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az keyvault create \
    --name "$KV_NAME" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    --subscription "$MGMT_SUB" \
    --enable-rbac-authorization true \
    --sku standard \
    --tags project=dragonfly env=dev managed-by=cli \
    --output table
fi

echo "==> grant Brian 'Key Vault Secrets Officer' on the vault"
USER_OID=$(az ad signed-in-user show --query id -o tsv)
# Both list and create against Microsoft.Authorization need az rest -- the
# `az role assignment` subcommands emit MissingSubscription on Windows even
# with --subscription set. PUT is idempotent for a given (principal, role,
# scope) triple as long as the RA GUID is stable; we filter by principalId
# server-side to detect existence.
EXISTING_ROLE=$(az rest --method GET \
  --url "https://management.azure.com${KV_ID}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&\$filter=principalId%20eq%20'${USER_OID}'" \
  --subscription "$MGMT_SUB" \
  --query "value[?properties.roleDefinitionId=='/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${KV_SECRETS_OFFICER_ROLE_DEF}'].id" -o tsv 2>/dev/null || true)
if [[ -z "$EXISTING_ROLE" ]]; then
  RA_GUID=$(uuidgen | tr 'A-Z' 'a-z')
  az rest --method put \
    --url "https://management.azure.com${KV_ID}/providers/Microsoft.Authorization/roleAssignments/${RA_GUID}?api-version=2022-04-01" \
    --subscription "$MGMT_SUB" \
    --body "{\"properties\":{\"roleDefinitionId\":\"/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${KV_SECRETS_OFFICER_ROLE_DEF}\",\"principalId\":\"${USER_OID}\",\"principalType\":\"User\"}}" \
    > /dev/null
fi

# ---------------------------------------------------------------------------
# 2. dragonfly-api app registration (CIAM tenant)
# ---------------------------------------------------------------------------
#
# NOTE: `az ad app *` commands do NOT accept --subscription. We must switch
# the default subscription with `az account set` and assert we ended up in
# the CIAM tenant before issuing any app-registration commands.

set_ciam_context

echo "==> ensure $API_APP_NAME app registration exists in CIAM tenant"
API_LOOKUP=$(az ad app list --display-name "$API_APP_NAME" --query "[].{appId:appId,id:id}" -o json)
API_APP_ID=$(echo "$API_LOOKUP" | python -c "import json,sys; d=json.load(sys.stdin); print(d[0]['appId'] if d else '')")
API_OBJECT_ID=$(echo "$API_LOOKUP" | python -c "import json,sys; d=json.load(sys.stdin); print(d[0]['id'] if d else '')")

if [[ -z "$API_APP_ID" ]]; then
  CREATED=$(az ad app create \
    --display-name "$API_APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --query "{appId:appId,id:id}" -o json)
  API_APP_ID=$(echo "$CREATED" | python -c "import json,sys; print(json.load(sys.stdin)['appId'])")
  API_OBJECT_ID=$(echo "$CREATED" | python -c "import json,sys; print(json.load(sys.stdin)['id'])")
fi

echo "==> set identifier URI $API_IDENTIFIER_URI"
az ad app update --id "$API_APP_ID" --identifier-uris "$API_IDENTIFIER_URI"

echo "==> set v2 access tokens + user.access OAuth2 scope (GUID $API_SCOPE_USER_ACCESS_GUID)"
SCOPE_BODY=$(cat <<EOF
{
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {
        "id": "${API_SCOPE_USER_ACCESS_GUID}",
        "adminConsentDescription": "Access Dragonfly API as the signed-in user",
        "adminConsentDisplayName": "Access Dragonfly",
        "userConsentDescription": "Access Dragonfly on your behalf",
        "userConsentDisplayName": "Access Dragonfly",
        "value": "user.access",
        "type": "User",
        "isEnabled": true
      }
    ]
  }
}
EOF
)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/${API_OBJECT_ID}" \
  --headers Content-Type=application/json \
  --body "$SCOPE_BODY"

# ---------------------------------------------------------------------------
# 3. dragonfly-client app registration (CIAM tenant)
# ---------------------------------------------------------------------------

assert_tenant "$CIAM_TENANT"

echo "==> ensure $CLIENT_APP_NAME app registration exists in CIAM tenant"
CLIENT_LOOKUP=$(az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/applications?\$filter=displayName eq '${CLIENT_APP_NAME}'&\$select=id,appId,displayName" \
  --subscription "$CIAM_PLACEHOLDER_SUB" -o json)
CLIENT_APP_ID=$(echo "$CLIENT_LOOKUP" | python -c "import json,sys; v=json.load(sys.stdin).get('value',[]); print(v[0]['appId'] if v else '')")
CLIENT_OBJECT_ID=$(echo "$CLIENT_LOOKUP" | python -c "import json,sys; v=json.load(sys.stdin).get('value',[]); print(v[0]['id'] if v else '')")

if [[ -z "$CLIENT_APP_ID" ]]; then
  CREATED=$(az ad app create \
    --display-name "$CLIENT_APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --is-fallback-public-client true \
    -o json)
  CLIENT_APP_ID=$(echo "$CREATED" | python -c "import json,sys; print(json.load(sys.stdin)['appId'])")
  CLIENT_OBJECT_ID=$(echo "$CREATED" | python -c "import json,sys; print(json.load(sys.stdin)['id'])")
fi

echo "==> set redirect URIs on $CLIENT_APP_NAME (SPA + public-client buckets)"
# Inline JSON escaping for az rest --body is unreliable on PowerShell hosts;
# write to a tempfile and pass via @file.
REDIRECTS_FILE="$(mktemp)"
cat > "$REDIRECTS_FILE" <<'EOF'
{
  "spa": {
    "redirectUris": [
      "https://parents.thehinterlandguide.app/auth/callback",
      "http://localhost:8081/auth/callback",
      "http://localhost:19006/auth/callback"
    ]
  },
  "publicClient": {
    "redirectUris": [
      "dragonfly://auth/callback",
      "msauth.net.dragonfly.app://auth"
    ]
  }
}
EOF
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/${CLIENT_OBJECT_ID}" \
  --headers "Content-Type=application/json" \
  --subscription "$CIAM_PLACEHOLDER_SUB" \
  --body "@${REDIRECTS_FILE}"
rm -f "$REDIRECTS_FILE"

# ---------------------------------------------------------------------------
# 4. Pre-authorized application grant: client -> api (user.access)
# ---------------------------------------------------------------------------

assert_tenant "$CIAM_TENANT"

echo "==> pre-authorize $CLIENT_APP_NAME against $API_APP_NAME user.access"
PREAUTH_BODY=$(cat <<EOF
{
  "api": {
    "preAuthorizedApplications": [
      {
        "appId": "${CLIENT_APP_ID}",
        "delegatedPermissionIds": ["${API_SCOPE_USER_ACCESS_GUID}"]
      }
    ]
  }
}
EOF
)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/${API_OBJECT_ID}" \
  --headers Content-Type=application/json \
  --subscription "$CIAM_PLACEHOLDER_SUB" \
  --body "$PREAUTH_BODY"

# ---------------------------------------------------------------------------
# 5. RS256 kid-handoff JWT keypair in Key Vault (management tenant)
# ---------------------------------------------------------------------------
#
# Either BOTH secrets exist (skip) or BOTH are absent (generate + upload).
# A half-state aborts loudly so an operator can decide whether to rotate.

set_mgmt_context

echo "==> ensure kid-jwt RS256 keypair in $KV_NAME (kid=$KID)"
PRIV_EXISTS=$(az keyvault secret show --vault-name "$KV_NAME" --name kid-jwt-signing-key --subscription "$MGMT_SUB" --query id -o tsv 2>/dev/null || true)
PUB_EXISTS=$(az keyvault secret show --vault-name "$KV_NAME" --name kid-jwt-public-key --subscription "$MGMT_SUB" --query id -o tsv 2>/dev/null || true)

if [[ -n "$PRIV_EXISTS" && -n "$PUB_EXISTS" ]]; then
  echo "  -> both kid-jwt secrets present, skipping keygen"
elif [[ -z "$PRIV_EXISTS" && -z "$PUB_EXISTS" ]]; then
  TMP_PRIV="$(mktemp /tmp/kid-jwt-priv.XXXXXX.pem)"
  TMP_PUB="$(mktemp /tmp/kid-jwt-pub.XXXXXX.pem)"
  trap 'rm -f "$TMP_PRIV" "$TMP_PUB"' EXIT
  openssl genrsa -out "$TMP_PRIV" 2048
  openssl rsa -in "$TMP_PRIV" -pubout -out "$TMP_PUB"
  az keyvault secret set \
    --vault-name "$KV_NAME" \
    --name kid-jwt-signing-key \
    --file "$TMP_PRIV" \
    --subscription "$MGMT_SUB" \
    --tags kid="$KID" alg=RS256 purpose=kid-handoff-jwt \
    --query id -o tsv
  az keyvault secret set \
    --vault-name "$KV_NAME" \
    --name kid-jwt-public-key \
    --file "$TMP_PUB" \
    --subscription "$MGMT_SUB" \
    --tags kid="$KID" alg=RS256 purpose=kid-handoff-jwt \
    --query id -o tsv
  rm -f "$TMP_PRIV" "$TMP_PUB"
  trap - EXIT
else
  echo "FATAL: kid-jwt keypair is in a half-state" >&2
  echo "       priv=$PRIV_EXISTS pub=$PUB_EXISTS" >&2
  echo "       Operator must decide: rotate (delete both, re-run) or repair." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 6. entra-* config secrets in Key Vault (management tenant)
# ---------------------------------------------------------------------------
#
# `az keyvault secret set` is idempotent -- writing the same value creates a
# new version which is harmless. Backend consumers always read latest.

assert_tenant "$MGMT_TENANT"

echo "==> write entra-* config secrets to $KV_NAME"
az keyvault secret set --vault-name "$KV_NAME" --name entra-tenant-id \
  --value "$CIAM_TENANT" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name entra-api-app-id \
  --value "$API_APP_ID" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name entra-client-app-id \
  --value "$CLIENT_APP_ID" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name entra-api-scope-user-access-guid \
  --value "$API_SCOPE_USER_ACCESS_GUID" --subscription "$MGMT_SUB" --query id -o tsv

echo
echo "done."
echo "  Key Vault URI:           $KV_URI"
echo "  CIAM tenant id:          $CIAM_TENANT"
echo "  dragonfly-api appId:     $API_APP_ID"
echo "  dragonfly-client appId:  $CLIENT_APP_ID"
echo "  user.access scope GUID:  $API_SCOPE_USER_ACCESS_GUID"
echo "  kid-handoff kid:         $KID"
