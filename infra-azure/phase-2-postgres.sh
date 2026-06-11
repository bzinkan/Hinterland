#!/usr/bin/env bash
# Phase 2 -- Azure Database for PostgreSQL Flexible Server for Dragonfly.
#
# Provisions:
#   - Burstable B1ms Flexible Server `dragonfly-postgres-dev` in eastus2
#   - One database `dragonfly` (created at server-create time via
#     --database-name)
#   - Admin user `dfadmin` with a random password stored in Key Vault as
#     `postgres-admin-password`
#   - Firewall rule allowing the operator's current public IP for
#     management (psql, alembic) and the special `AllowAllAzure` rule
#     (0.0.0.0/0.0.0.0) so Container Apps can connect from any Azure
#     service IP in Phase 5+. The latter is a dev-only posture; in prod
#     this gets replaced with private VNet integration.
#   - Five `postgres-*` Key Vault secrets capturing the connection-string
#     parts the backend reads at boot via managed identity.
#
# Idempotent. Server create skips if the server already exists. Password
# is regenerated only if the KV secret is absent (so re-running does not
# silently rotate the password and break the running backend).
#
# Run with:
#   bash infra-azure/phase-2-postgres.sh
#
# Prerequisites: az CLI authenticated against management tenant
# 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 (brian@dragonfly-app.net).

set -euo pipefail

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"

RG="dragonfly-dev-rg"
# Postgres Flexible Server lives in centralus (not eastus2 where the RG +
# Key Vault are) because this Sponsored subscription's quotaId is
# `Sponsored_2016-01-01`, which has regional restrictions on Burstable
# Postgres in eastus + eastus2. centralus succeeded on the first try.
# RG itself is location-tagged eastus2 but the RG location only affects
# metadata; per-resource locations override. Cross-region latency from
# Container Apps (eastus2) to Postgres (centralus) is ~25ms one-way,
# tolerable for the beta.
LOCATION="centralus"
KV_NAME="dragonfly-kv-dev"

PG_SERVER="dragonfly-postgres-dev"
PG_ADMIN="dfadmin"
PG_DATABASE="dragonfly"
PG_VERSION="16"
PG_SKU="Standard_B1ms"
PG_TIER="Burstable"
PG_STORAGE_GB="32"

PG_HOST="${PG_SERVER}.postgres.database.azure.com"
PG_PORT="5432"

# ---------------------------------------------------------------------------
# Tenant context guard
# ---------------------------------------------------------------------------

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
# 1. Provider registration (idempotent)
# ---------------------------------------------------------------------------

echo "==> ensure Microsoft.DBforPostgreSQL provider is Registered"
PG_RP_STATE=$(az provider show --namespace Microsoft.DBforPostgreSQL --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
if [[ "$PG_RP_STATE" != "Registered" ]]; then
  az provider register --namespace Microsoft.DBforPostgreSQL --subscription "$MGMT_SUB" --wait
fi

# ---------------------------------------------------------------------------
# 2. Admin password (KV-managed)
# ---------------------------------------------------------------------------
#
# Generate only if absent. Re-running this script must NOT rotate the
# password silently -- doing so would break any running backend reading
# the old version. Use `az keyvault secret delete --name
# postgres-admin-password` to force a rotation; then re-run.

echo "==> ensure postgres-admin-password in $KV_NAME"
if ! az keyvault secret show --vault-name "$KV_NAME" --name postgres-admin-password --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  # Fully random password; regenerate until the character classes Azure
  # Postgres requires (upper/lower/digit) are all present.
  while :; do
    PG_PASSWORD="$(openssl rand -base64 48 | tr -d '/+=' | head -c 32)"
    if [[ "$PG_PASSWORD" == *[A-Z]* && "$PG_PASSWORD" == *[a-z]* && "$PG_PASSWORD" == *[0-9]* ]]; then
      break
    fi
  done
  az keyvault secret set \
    --vault-name "$KV_NAME" \
    --name postgres-admin-password \
    --value "$PG_PASSWORD" \
    --subscription "$MGMT_SUB" \
    --tags purpose=postgres-admin server="$PG_SERVER" \
    --query id -o tsv
else
  PG_PASSWORD=$(az keyvault secret show --vault-name "$KV_NAME" --name postgres-admin-password --subscription "$MGMT_SUB" --query value -o tsv)
fi

# ---------------------------------------------------------------------------
# 3. Flexible Server (idempotent)
# ---------------------------------------------------------------------------

echo "==> ensure Postgres Flexible Server $PG_SERVER"
if ! az postgres flexible-server show --name "$PG_SERVER" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  MY_IP=$(curl -sS https://api.ipify.org)
  # --database-name is NOT accepted on the standalone tier (only on
  # ElasticCluster) -- create the database explicitly below instead.
  az postgres flexible-server create \
    --name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --admin-user "$PG_ADMIN" \
    --admin-password "$PG_PASSWORD" \
    --sku-name "$PG_SKU" \
    --tier "$PG_TIER" \
    --storage-size "$PG_STORAGE_GB" \
    --version "$PG_VERSION" \
    --high-availability Disabled \
    --public-access "$MY_IP" \
    --backup-retention 7 \
    --tags project=dragonfly env=dev managed-by=cli \
    --yes
fi

echo "==> ensure database $PG_DATABASE exists on $PG_SERVER"
if ! az postgres flexible-server db show \
    --server-name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --database-name "$PG_DATABASE" >/dev/null 2>&1; then
  az postgres flexible-server db create \
    --server-name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --database-name "$PG_DATABASE"
fi

# ---------------------------------------------------------------------------
# 4. Firewall: allow other Azure services (Container Apps in Phase 5)
# ---------------------------------------------------------------------------

echo "==> ensure AllowAllAzureServices firewall rule (0.0.0.0/0.0.0.0)"
if ! az postgres flexible-server firewall-rule show \
    --name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --rule-name AllowAllAzureServices >/dev/null 2>&1; then
  az postgres flexible-server firewall-rule create \
    --name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --rule-name AllowAllAzureServices \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0
fi

# Also ensure the operator's current public IP is allowed (rule is per-IP;
# rule-name reflects the IP so re-running from a new IP adds a new rule).
echo "==> ensure operator-IP firewall rule"
MY_IP=$(curl -sS https://api.ipify.org)
MY_IP_RULENAME="op-$(echo "$MY_IP" | tr '.' '-')"
if ! az postgres flexible-server firewall-rule show \
    --name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --rule-name "$MY_IP_RULENAME" >/dev/null 2>&1; then
  az postgres flexible-server firewall-rule create \
    --name "$PG_SERVER" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --rule-name "$MY_IP_RULENAME" \
    --start-ip-address "$MY_IP" \
    --end-ip-address "$MY_IP"
fi

# ---------------------------------------------------------------------------
# 5. Connection-string parts in KV (read by backend at boot)
# ---------------------------------------------------------------------------
#
# The backend assembles the SQLAlchemy URL from these:
#   postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}?ssl=require
#
# Storing the parts (not a full URL) makes rotation of any one piece
# safe -- the backend just re-reads from KV.

echo "==> write postgres-* config secrets"
az keyvault secret set --vault-name "$KV_NAME" --name postgres-host \
  --value "$PG_HOST" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name postgres-port \
  --value "$PG_PORT" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name postgres-database \
  --value "$PG_DATABASE" --subscription "$MGMT_SUB" --query id -o tsv
az keyvault secret set --vault-name "$KV_NAME" --name postgres-admin-user \
  --value "$PG_ADMIN" --subscription "$MGMT_SUB" --query id -o tsv

echo
echo "done."
echo "  Server FQDN:    $PG_HOST"
echo "  Admin user:     $PG_ADMIN"
echo "  Database:       $PG_DATABASE"
echo "  Postgres ver:   $PG_VERSION"
echo "  SKU/storage:    $PG_SKU / ${PG_STORAGE_GB}GiB"
echo
echo "Smoke connect (requires psql with TLS support):"
echo "  PGPASSWORD=\"\$(az keyvault secret show --vault-name $KV_NAME --name postgres-admin-password --query value -o tsv)\" \\"
echo "    psql \"host=$PG_HOST port=$PG_PORT dbname=$PG_DATABASE user=$PG_ADMIN sslmode=require\" -c '\\\\dt'"
