#!/usr/bin/env bash
# Phase 3b -- Blob lifecycle management policy for the photos account.
#
# Applies the retention rules the product docs assume (docs/moderation.md)
# but which were never ported from the GCS lifecycle rules
# (infra-gcp/main.tf) during the Azure migration:
#
#   pending/     delete 1 day after last modification  (abandoned uploads;
#                moderation normally moves blobs out within seconds)
#   quarantine/  delete 90 days after last modification (flagged/rejected
#                photos are retained for adult review, then purged)
#
# NOTES
#   * The management policy is a SINGLETON per storage account, and
#     `az storage account management-policy create` REPLACES the whole
#     policy document. That is exactly what makes re-runs idempotent --
#     but it also means THIS SCRIPT OWNS every lifecycle rule on the
#     account. Add future rules here, never ad hoc in the portal.
#   * prefixMatch must include the container name ("photos/pending/");
#     a bare "pending/" matches nothing.
#   * Azure runs lifecycle rules on its own schedule: expect the first
#     execution up to 24-48h after the policy is applied.
#   * The account has 7-day blob soft-delete (phase-3 section 3), so
#     effective removal is ~1+7 days for pending/ and ~90+7 days for
#     quarantine/.
#   * APPLYING THIS POLICY AUTO-DELETES DATA on the target account.
#     Running it against the live environment is an operator decision.
#
# Env overrides (defaults target the current dev environment):
#   DRAGONFLY_SA_NAME, DRAGONFLY_RG, DRAGONFLY_SUBSCRIPTION

set -euo pipefail

SA_NAME="${DRAGONFLY_SA_NAME:-dragonflyphotosdev}"
RG="${DRAGONFLY_RG:-dragonfly-dev-rg}"
SUBSCRIPTION="${DRAGONFLY_SUBSCRIPTION:-}"

SUB_ARGS=()
if [[ -n "$SUBSCRIPTION" ]]; then
  SUB_ARGS+=(--subscription "$SUBSCRIPTION")
fi

POLICY_JSON=$(cat <<'JSON'
{
  "rules": [
    {
      "enabled": true,
      "name": "expire-pending-1d",
      "type": "Lifecycle",
      "definition": {
        "actions": {
          "baseBlob": { "delete": { "daysAfterModificationGreaterThan": 1 } }
        },
        "filters": {
          "blobTypes": ["blockBlob"],
          "prefixMatch": ["photos/pending/"]
        }
      }
    },
    {
      "enabled": true,
      "name": "expire-quarantine-90d",
      "type": "Lifecycle",
      "definition": {
        "actions": {
          "baseBlob": { "delete": { "daysAfterModificationGreaterThan": 90 } }
        },
        "filters": {
          "blobTypes": ["blockBlob"],
          "prefixMatch": ["photos/quarantine/"]
        }
      }
    }
  ]
}
JSON
)

echo "Applying lifecycle management policy to storage account ${SA_NAME} (rg ${RG})"
az storage account management-policy create \
  ${SUB_ARGS[@]+"${SUB_ARGS[@]}"} \
  --account-name "$SA_NAME" \
  --resource-group "$RG" \
  --policy "$POLICY_JSON" \
  --output none

echo "Applied. Verify with:"
echo "  az storage account management-policy show --account-name ${SA_NAME} --resource-group ${RG}"
