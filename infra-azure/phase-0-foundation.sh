#!/usr/bin/env bash
# Phase 0 -- Azure foundation for Hinterland (ADR 0010).
#
# Idempotent. Re-running it should produce no changes if the resources
# already exist with the expected shape.
#
# Run with:
#   bash infra-azure/phase-0-foundation.sh
#
# Prerequisites: az CLI authenticated against tenant
# 3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05 (brian@dragonfly-app.net).

set -euo pipefail

SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
RG="dragonfly-dev-rg"
LOCATION="eastus2"

echo "==> resource group $RG in $LOCATION"
az group create \
  --name "$RG" \
  --location "$LOCATION" \
  --subscription "$SUB" \
  --tags project=dragonfly env=dev owner=brian managed-by=cli \
  --output table

echo "==> register required resource providers"
PROVIDERS=(
  Microsoft.App                # Container Apps
  Microsoft.DBforPostgreSQL    # Postgres Flexible Server
  Microsoft.Storage            # Blob Storage
  Microsoft.ContainerRegistry  # ACR
  Microsoft.Web                # Static Web Apps
  Microsoft.CognitiveServices  # Content Safety AI
  Microsoft.OperationalInsights  # Log Analytics (dep of Container Apps)
  Microsoft.Insights           # App Insights
  Microsoft.Network            # Generic networking
)
for p in "${PROVIDERS[@]}"; do
  az provider register --namespace "$p" --subscription "$SUB" > /dev/null &
done
wait
echo "  -> registration kicked off; check state with:"
echo "     az provider list --subscription $SUB --query \"[?contains('${PROVIDERS[*]}', namespace)].[namespace,registrationState]\" -o tsv"

echo "done."
