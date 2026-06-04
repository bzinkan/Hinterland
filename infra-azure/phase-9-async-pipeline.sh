#!/usr/bin/env bash
# Phase 9 -- Service Bus + Event Grid + Container Apps Jobs for Dragonfly.
#
# Closes the infra half of Risk 0002 (Azure async safety/science pipeline).
# All Dragonfly app code that consumes this infra already landed in
# PRs #107-#112 on main; this script wires the Azure-side resources
# they expect.
#
# Provisions:
#   - 3 Key Vault secrets (empty values for the operator to populate
#     out-of-band): `inat-oauth-token`, `content-safety-endpoint`,
#     `content-safety-key`.
#   - Service Bus namespace `dragonfly-sb-dev` (Standard tier -- needed
#     for DLQ + dead-letter-on-expiration semantics).
#   - Two queues:
#       * `moderation-pending` (Event Grid delivery target; consumer is
#         `dragonfly-moderation-worker`)
#       * `inat-submit` (producer = moderation processor / review-queue
#         approve handler outbox; consumer = `dragonfly-inat-submit-worker`)
#     Both have max-delivery 5 and dead-lettering on message expiration.
#   - Event Grid system topic on `dragonflyphotosdev` with a subscription
#     filtering BlobCreated events under `photos/pending/` and delivering
#     to the moderation-pending queue. No webhook handshake -- Event
#     Grid -> Service Bus is a first-class destination.
#   - 5 Container Apps Jobs:
#       * `dragonfly-moderation-worker`     -- KEDA-scaled Service Bus consumer
#       * `dragonfly-inat-submit-worker`    -- KEDA-scaled Service Bus consumer
#       * `dragonfly-rarity-refresh`        -- nightly cron 03:00 UTC
#       * `dragonfly-sweep-stale-reviews`   -- nightly cron 04:00 UTC
#       * `dragonfly-inat-outbox-replay`    -- */15 * * * * cron
#       * `dragonfly-dispatcher-replay`     -- */15 * * * * cron
#     All use the same Container App image, the same UAMI, and the same
#     env-var set (Postgres connection + Service Bus + KV refs).
#   - UAMI role assignments for the new resources:
#       * Service Bus Data Sender + Data Receiver on both queues
#       * EventGrid EventSubscription Contributor on the storage account
#         (so Event Grid can write to the queue under the same identity).
#   - Container App env-var update: the existing `dragonfly-api` service
#     gets 6 new env vars wired to the new KV secrets / SB namespace.
#
# Idempotent. Every block checks for existence before creating;
# `az containerapp update --set-env-vars` is idempotent on its own.
#
# Run with:
#   MSYS_NO_PATHCONV=1 bash infra-azure/phase-9-async-pipeline.sh
#
# Prerequisites: phase-5 has already run (UAMI + Container App + Key
# Vault all in place).

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"
KV_NAME="dragonfly-kv-dev"

UAMI_NAME="dragonfly-api-mi"
UAMI_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${UAMI_NAME}"

ACR_NAME="dragonflyacrdev"
SA_NAME="dragonflyphotosdev"
APP_NAME="dragonfly-api"

# The Container App image to use for the 5 new Jobs. Same image as the
# main API service -- the Job's container command picks the right
# entry point.
IMAGE="${ACR_NAME}.azurecr.io/dragonfly-api:phase4-bootstrap"

# Service Bus.
SB_NAMESPACE="dragonfly-sb-dev"
SB_QUEUE_MODERATION="moderation-pending"
SB_QUEUE_INAT="inat-submit"
SB_FQDN="${SB_NAMESPACE}.servicebus.windows.net"

# Event Grid.
EG_TOPIC_NAME="dragonfly-photos-eg-dev"
EG_SUB_NAME="moderation-pending-sub"

# Well-known role definition GUIDs.
ROLE_SB_DATA_SENDER="69a216fc-b8fb-44d8-bc22-1f3c2cd27a39"
ROLE_SB_DATA_RECEIVER="4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0"
ROLE_EG_EVENT_SUB_CONTRIBUTOR="428e0ff0-5e57-4d9c-a221-2c70d0e0a443"

KV_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.KeyVault/vaults/${KV_NAME}"
SA_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.Storage/storageAccounts/${SA_NAME}"
SB_NAMESPACE_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}"
SB_QUEUE_MODERATION_ID="${SB_NAMESPACE_ID}/queues/${SB_QUEUE_MODERATION}"
SB_QUEUE_INAT_ID="${SB_NAMESPACE_ID}/queues/${SB_QUEUE_INAT}"

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

for ns in Microsoft.ServiceBus Microsoft.EventGrid; do
  STATE=$(az provider show --namespace "$ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
  if [[ "$STATE" != "Registered" ]]; then
    echo "==> register provider $ns"
    az provider register --namespace "$ns" --subscription "$MGMT_SUB" --wait
  fi
done

# ---------------------------------------------------------------------------
# 2. UAMI principal (already created by phase-5; just fetch the id)
# ---------------------------------------------------------------------------

UAMI_PRINCIPAL=$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query principalId -o tsv)
UAMI_CLIENT_ID=$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query clientId -o tsv)

# ---------------------------------------------------------------------------
# 3. Key Vault secrets (empty placeholders for operator to populate)
# ---------------------------------------------------------------------------

ensure_kv_secret_empty() {
  local name="$1"
  if ! az keyvault secret show --vault-name "$KV_NAME" --name "$name" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    echo "  -> creating empty placeholder secret $name (populate with: az keyvault secret set --vault-name $KV_NAME --name $name --value ...)"
    az keyvault secret set \
      --vault-name "$KV_NAME" \
      --name "$name" \
      --value "PLACEHOLDER_REPLACE_OUT_OF_BAND" \
      --subscription "$MGMT_SUB" \
      --query id -o tsv >/dev/null
  fi
}

echo "==> ensure Key Vault placeholder secrets in $KV_NAME"
ensure_kv_secret_empty "inat-oauth-token"
ensure_kv_secret_empty "content-safety-endpoint"
ensure_kv_secret_empty "content-safety-key"

# ---------------------------------------------------------------------------
# 4. Service Bus namespace + queues
# ---------------------------------------------------------------------------

echo "==> ensure Service Bus namespace $SB_NAMESPACE (Standard tier)"
if ! az servicebus namespace show --name "$SB_NAMESPACE" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az servicebus namespace create \
    --name "$SB_NAMESPACE" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --sku Standard \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

ensure_sb_queue() {
  local name="$1"
  echo "==> ensure Service Bus queue $name"
  if ! az servicebus queue show --namespace-name "$SB_NAMESPACE" --name "$name" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    az servicebus queue create \
      --namespace-name "$SB_NAMESPACE" \
      --name "$name" \
      --resource-group "$RG" \
      --subscription "$MGMT_SUB" \
      --max-delivery-count 5 \
      --dead-lettering-on-message-expiration true \
      --lock-duration PT5M \
      --output none
  fi
}

ensure_sb_queue "$SB_QUEUE_MODERATION"
ensure_sb_queue "$SB_QUEUE_INAT"

# ---------------------------------------------------------------------------
# 5. UAMI role assignments on the new resources
# ---------------------------------------------------------------------------

ensure_role() {
  local scope="$1"
  local role_def="$2"
  local principal="$3"
  local description="$4"

  local existing
  existing=$(az rest --method GET \
    --url "https://management.azure.com${scope}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&\$filter=principalId%20eq%20'${principal}'" \
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
      --body "{\"properties\":{\"roleDefinitionId\":\"/subscriptions/${MGMT_SUB}/providers/Microsoft.Authorization/roleDefinitions/${role_def}\",\"principalId\":\"${principal}\",\"principalType\":\"ServicePrincipal\"}}" \
      > /dev/null
  fi
}

echo "==> ensure UAMI roles on Service Bus queues"
ensure_role "$SB_QUEUE_MODERATION_ID" "$ROLE_SB_DATA_SENDER"   "$UAMI_PRINCIPAL" "SB Data Sender on $SB_QUEUE_MODERATION"
ensure_role "$SB_QUEUE_MODERATION_ID" "$ROLE_SB_DATA_RECEIVER" "$UAMI_PRINCIPAL" "SB Data Receiver on $SB_QUEUE_MODERATION"
ensure_role "$SB_QUEUE_INAT_ID"       "$ROLE_SB_DATA_SENDER"   "$UAMI_PRINCIPAL" "SB Data Sender on $SB_QUEUE_INAT"
ensure_role "$SB_QUEUE_INAT_ID"       "$ROLE_SB_DATA_RECEIVER" "$UAMI_PRINCIPAL" "SB Data Receiver on $SB_QUEUE_INAT"

# ---------------------------------------------------------------------------
# 6. Event Grid system topic + subscription -> Service Bus
# ---------------------------------------------------------------------------

echo "==> ensure Event Grid system topic $EG_TOPIC_NAME on $SA_NAME"
if ! az eventgrid system-topic show --name "$EG_TOPIC_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az eventgrid system-topic create \
    --name "$EG_TOPIC_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --location "$LOCATION" \
    --topic-type Microsoft.Storage.StorageAccounts \
    --source "$SA_ID" \
    --output none
fi

# Grant Event Grid system topic the right to write to the queue under
# its own system-assigned managed identity. The system topic exposes a
# principalId we can grant the role to. (The system topic identity
# auto-provisions on first event delivery; for the subscription create
# itself, an EventGrid Contributor on the SB namespace would also work,
# but Data Sender on the specific queue is tighter.)
EG_TOPIC_PRINCIPAL=$(az eventgrid system-topic show --name "$EG_TOPIC_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query identity.principalId -o tsv 2>/dev/null || true)
if [[ -z "$EG_TOPIC_PRINCIPAL" || "$EG_TOPIC_PRINCIPAL" == "null" ]]; then
  echo "  -> assigning system-assigned identity to Event Grid topic"
  az eventgrid system-topic update \
    --name "$EG_TOPIC_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --identity systemassigned \
    --output none
  EG_TOPIC_PRINCIPAL=$(az eventgrid system-topic show --name "$EG_TOPIC_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query identity.principalId -o tsv)
fi

ensure_role "$SB_QUEUE_MODERATION_ID" "$ROLE_SB_DATA_SENDER" "$EG_TOPIC_PRINCIPAL" "Event Grid topic SB Data Sender on $SB_QUEUE_MODERATION"

echo "==> ensure Event Grid subscription $EG_SUB_NAME -> $SB_QUEUE_MODERATION"
if ! az eventgrid system-topic event-subscription show --system-topic-name "$EG_TOPIC_NAME" --resource-group "$RG" --name "$EG_SUB_NAME" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az eventgrid system-topic event-subscription create \
    --system-topic-name "$EG_TOPIC_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --name "$EG_SUB_NAME" \
    --endpoint-type servicebusqueue \
    --endpoint "$SB_QUEUE_MODERATION_ID" \
    --delivery-identity systemassigned \
    --included-event-types Microsoft.Storage.BlobCreated \
    --subject-begins-with "/blobServices/default/containers/photos/blobs/pending/" \
    --event-delivery-schema cloudeventschemav1_0 \
    --output none
fi

# ---------------------------------------------------------------------------
# 7. Container App env-var update (idempotent)
# ---------------------------------------------------------------------------

echo "==> update env vars on Container App $APP_NAME"
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --subscription "$MGMT_SUB" \
  --set-env-vars \
    "DRAGONFLY_MODERATION_PROVIDER=azure_content_safety" \
    "DRAGONFLY_SERVICE_BUS_NAMESPACE=${SB_FQDN}" \
    "DRAGONFLY_SERVICE_BUS_MODERATION_QUEUE=${SB_QUEUE_MODERATION}" \
    "DRAGONFLY_SERVICE_BUS_INAT_QUEUE=${SB_QUEUE_INAT}" \
    "DRAGONFLY_INAT_OAUTH_TOKEN=secretref:inat-oauth-token" \
    "DRAGONFLY_CONTENT_SAFETY_ENDPOINT=secretref:content-safety-endpoint" \
    "DRAGONFLY_CONTENT_SAFETY_KEY=secretref:content-safety-key" \
  --output none

# Also register the three secrets ON the Container App so the secretref:
# bindings resolve at runtime (Container App secrets are mirrored from
# Key Vault via the UAMI; the names here must match KV secret names).
az containerapp secret set \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --subscription "$MGMT_SUB" \
  --secrets \
    "inat-oauth-token=keyvaultref:https://${KV_NAME}.vault.azure.net/secrets/inat-oauth-token,identityref:${UAMI_ID}" \
    "content-safety-endpoint=keyvaultref:https://${KV_NAME}.vault.azure.net/secrets/content-safety-endpoint,identityref:${UAMI_ID}" \
    "content-safety-key=keyvaultref:https://${KV_NAME}.vault.azure.net/secrets/content-safety-key,identityref:${UAMI_ID}" \
  --output none

# ---------------------------------------------------------------------------
# 8. Container Apps Jobs (workers + cron)
# ---------------------------------------------------------------------------

# Shared env-var spec for all jobs: they need DB + SB + KV secrets just
# like the main API. The full list is built from the API's current env.
JOB_ENV_VARS=(
  "DRAGONFLY_ENV=prod"
  "DRAGONFLY_READINESS_DATABASE_REQUIRED=true"
  "DRAGONFLY_MODERATION_PROVIDER=azure_content_safety"
  "DRAGONFLY_SERVICE_BUS_NAMESPACE=${SB_FQDN}"
  "DRAGONFLY_SERVICE_BUS_MODERATION_QUEUE=${SB_QUEUE_MODERATION}"
  "DRAGONFLY_SERVICE_BUS_INAT_QUEUE=${SB_QUEUE_INAT}"
  "DRAGONFLY_INAT_OAUTH_TOKEN=secretref:inat-oauth-token"
  "DRAGONFLY_CONTENT_SAFETY_ENDPOINT=secretref:content-safety-endpoint"
  "DRAGONFLY_CONTENT_SAFETY_KEY=secretref:content-safety-key"
)

ensure_event_job() {
  local job_name="$1"
  local command="$2"
  local queue_name="$3"

  echo "==> ensure event-driven Container Apps Job $job_name (SB queue $queue_name)"
  if az containerapp job show --name "$job_name" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    return
  fi
  az containerapp job create \
    --name "$job_name" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --environment "dragonfly-cae-dev" \
    --trigger-type Event \
    --replica-completion-count 1 \
    --parallelism 1 \
    --replica-retry-limit 0 \
    --replica-timeout 1800 \
    --polling-interval 30 \
    --min-executions 0 \
    --max-executions 3 \
    --scale-rule-name "${queue_name}-scaler" \
    --scale-rule-type azure-servicebus \
    --scale-rule-metadata \
      "namespace=${SB_NAMESPACE}" \
      "queueName=${queue_name}" \
      "messageCount=5" \
    --scale-rule-identity "$UAMI_ID" \
    --user-assigned "$UAMI_ID" \
    --registry-server "${ACR_NAME}.azurecr.io" \
    --registry-identity "$UAMI_ID" \
    --image "$IMAGE" \
    --cpu 0.5 \
    --memory 1.0Gi \
    --command "/bin/sh" \
    --args "-c" "$command" \
    --env-vars "${JOB_ENV_VARS[@]}" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
}

ensure_cron_job() {
  local job_name="$1"
  local command="$2"
  local cron="$3"

  echo "==> ensure scheduled Container Apps Job $job_name (cron '$cron')"
  if az containerapp job show --name "$job_name" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    return
  fi
  az containerapp job create \
    --name "$job_name" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --environment "dragonfly-cae-dev" \
    --trigger-type Schedule \
    --cron-expression "$cron" \
    --replica-completion-count 1 \
    --parallelism 1 \
    --replica-retry-limit 0 \
    --replica-timeout 1800 \
    --user-assigned "$UAMI_ID" \
    --registry-server "${ACR_NAME}.azurecr.io" \
    --registry-identity "$UAMI_ID" \
    --image "$IMAGE" \
    --cpu 0.5 \
    --memory 1.0Gi \
    --command "/bin/sh" \
    --args "-c" "$command" \
    --env-vars "${JOB_ENV_VARS[@]}" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
}

ensure_event_job "dragonfly-moderation-worker"  "python -m admin.moderation_consumer"  "$SB_QUEUE_MODERATION"
ensure_event_job "dragonfly-inat-submit-worker" "python -m admin.inat_submit_consumer" "$SB_QUEUE_INAT"

ensure_cron_job "dragonfly-rarity-refresh"      "python -m admin.rarity_refresh"      "0 3 * * *"
ensure_cron_job "dragonfly-sweep-stale-reviews" "python -m admin.sweep_stale_reviews" "0 4 * * *"
ensure_cron_job "dragonfly-inat-outbox-replay"  "python -m admin.inat_outbox_replay"  "*/15 * * * *"
ensure_cron_job "dragonfly-dispatcher-replay"   "python -m admin.dispatcher_replay"   "*/15 * * * *"

# ---------------------------------------------------------------------------
# 9. Done
# ---------------------------------------------------------------------------

echo
echo "done."
echo "  Service Bus FQDN:    $SB_FQDN"
echo "  Moderation queue:    $SB_QUEUE_MODERATION"
echo "  iNat-submit queue:   $SB_QUEUE_INAT"
echo "  Event Grid topic:    $EG_TOPIC_NAME"
echo "  UAMI principal:      $UAMI_PRINCIPAL"
echo "  Container App:       $APP_NAME (env vars updated)"
echo
echo "Next steps:"
echo "  1. Populate the 3 KV secrets with real values out of band:"
echo "     - inat-oauth-token         (from inaturalist.org/users/api_token after project approval)"
echo "     - content-safety-endpoint  (Azure AI Content Safety resource endpoint)"
echo "     - content-safety-key       (same resource's key)"
echo "  2. Run phase-9-monitoring.sh to wire the Azure Monitor alerts."
echo "  3. Smoke: 'az containerapp job start --name dragonfly-rarity-refresh' should exit 0."
echo
