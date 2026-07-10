#!/usr/bin/env bash
# Provision/contain the isolated Hinterland Observation W1 pipeline.
# Run this once before the repaired deploy workflow, then whenever job or
# lifecycle configuration changes.

set -euo pipefail

# Azure CLI on Windows writes CRLF even inside Git Bash. Command substitution
# removes LF but preserves CR, which breaks exact IDs, role counts, and job
# names. Normalize TSV only; `pipefail` preserves Azure command failures.
az() {
  local argument normalize_tsv=0
  for argument in "$@"; do
    [[ "$argument" == "tsv" ]] && normalize_tsv=1
  done
  if [[ "$normalize_tsv" == "1" ]]; then
    command az "$@" | tr -d '\r'
  else
    command az "$@"
  fi
}

azure_file_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s\n' "$1"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HINTERLAND_ENV_FILE:-${1:-${SCRIPT_DIR}/environments/hinterland-dev.env}}"
[[ -f "$ENV_FILE" ]] || { echo "FATAL: missing $ENV_FILE" >&2; exit 1; }
# shellcheck source=environments/hinterland-dev.env
source "$ENV_FILE"

required=(
  ENVIRONMENT AZURE_SUBSCRIPTION_ID AZURE_TENANT_ID AZURE_LOCATION_PRIMARY
  HINTERLAND_RESOURCE_GROUP HINTERLAND_KEY_VAULT_NAME
  HINTERLAND_STORAGE_ACCOUNT HINTERLAND_PHOTOS_CONTAINER
  HINTERLAND_TAXONOMY_PACKS_CONTAINER HINTERLAND_ACR_NAME
  HINTERLAND_USER_ASSIGNED_IDENTITY HINTERLAND_CONTAINER_APPS_ENV
  HINTERLAND_CONTAINER_APP_NAME HINTERLAND_DATABASE_HOST
  HINTERLAND_DATABASE_USER HINTERLAND_DATABASE_NAME
  HINTERLAND_SERVICE_BUS_NAMESPACE
  HINTERLAND_MODERATION_QUEUE HINTERLAND_INAT_QUEUE
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "FATAL: $name is unset" >&2; exit 1; }
done

SUBSCRIPTION="$AZURE_SUBSCRIPTION_ID"
TENANT="$AZURE_TENANT_ID"
LOCATION="$AZURE_LOCATION_PRIMARY"
RG="$HINTERLAND_RESOURCE_GROUP"
KV_NAME="$HINTERLAND_KEY_VAULT_NAME"
SA_NAME="$HINTERLAND_STORAGE_ACCOUNT"
PHOTOS_CONTAINER="$HINTERLAND_PHOTOS_CONTAINER"
PACKS_CONTAINER="$HINTERLAND_TAXONOMY_PACKS_CONTAINER"
ACR_NAME="$HINTERLAND_ACR_NAME"
UAMI_NAME="$HINTERLAND_USER_ASSIGNED_IDENTITY"
CAE_NAME="$HINTERLAND_CONTAINER_APPS_ENV"
APP_NAME="$HINTERLAND_CONTAINER_APP_NAME"
PG_HOST="$HINTERLAND_DATABASE_HOST"
PG_USER="$HINTERLAND_DATABASE_USER"
PG_DB="$HINTERLAND_DATABASE_NAME"
SB_NAMESPACE="$HINTERLAND_SERVICE_BUS_NAMESPACE"
MODERATION_QUEUE="$HINTERLAND_MODERATION_QUEUE"
INAT_QUEUE="$HINTERLAND_INAT_QUEUE"
PREFIX="${PROJECT_SLUG:-hinterland}"
IMAGE="${HINTERLAND_PHASE9_IMAGE:-}"
PREFLIGHT_ACK="${HINTERLAND_OBSERVATION_PREFLIGHT_ACK:-}"

if [[ "$RG" != "hinterland-dev-rg" || "$RG" == "gordi-pilot-rg" ]]; then
  echo "FATAL: refusing non-isolated resource group $RG" >&2
  exit 1
fi
if [[ "$IMAGE" != *@sha256:* ]] || [[ ! "$IMAGE" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "FATAL: HINTERLAND_PHASE9_IMAGE must be an immutable ACR digest" >&2
  exit 1
fi

az account set --subscription "$SUBSCRIPTION"
[[ "$(az account show --query id -o tsv)" == "$SUBSCRIPTION" ]] || exit 1
[[ "$(az account show --query tenantId -o tsv)" == "$TENANT" ]] || exit 1
az group show --name "$RG" --subscription "$SUBSCRIPTION" --output none

UAMI_ID="$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query id -o tsv)"
UAMI_PRINCIPAL="$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query principalId -o tsv)"
UAMI_CLIENT_ID="$(az identity show --name "$UAMI_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query clientId -o tsv)"
CAE_ID="$(az containerapp env show --name "$CAE_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query id -o tsv)"
STORAGE_ACCOUNT_ID="$(az storage account show --name "$SA_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query id -o tsv)"
BLOB_ENDPOINT="https://${SA_NAME}.blob.core.windows.net"

role_count() {
  local scope="$1" role="$2" include_inherited="${3:-0}"
  local args=(
    az role assignment list
    --assignee-object-id "$UAMI_PRINCIPAL"
    --scope "$scope"
    --subscription "$SUBSCRIPTION"
  )
  [[ "$include_inherited" == "1" ]] && args+=(--include-inherited)
  "${args[@]}" --query "[?roleDefinitionName=='${role}'] | length(@)" -o tsv
}

wait_for_role_absent() {
  local scope="$1" role="$2" include_inherited="${3:-0}" remaining
  for attempt in $(seq 1 12); do
    remaining="$(role_count "$scope" "$role" "$include_inherited")"
    [[ "$remaining" == "0" ]] && return 0
    sleep 10
  done
  echo "FATAL: role remains after RBAC propagation window: $role at $scope" >&2
  return 1
}

wait_for_direct_role_present() {
  local scope="$1" role="$2" count
  for attempt in $(seq 1 12); do
    count="$(role_count "$scope" "$role")"
    [[ "$count" != "0" ]] && return 0
    sleep 10
  done
  echo "FATAL: queue role did not propagate: $role at $scope" >&2
  return 1
}

echo "==> contain Observation egress"
# The pre-W1 API has no CV feature flag: a configured token alone enables the
# photo-egress route. Remove both aliases first so containment is effective on
# the old revision, not only after the repaired image is deployed.
az containerapp update \
  --name "$APP_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --remove-env-vars HINTERLAND_INAT_OAUTH_TOKEN DRAGONFLY_INAT_OAUTH_TOKEN \
  --output none
az containerapp update \
  --name "$APP_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --set-env-vars \
    HINTERLAND_MODERATION_PROVIDER=noop DRAGONFLY_MODERATION_PROVIDER=noop \
    HINTERLAND_INAT_CV_ENABLED=false DRAGONFLY_INAT_CV_ENABLED=false \
    HINTERLAND_INAT_CV_DISCLOSURE_APPROVED=false DRAGONFLY_INAT_CV_DISCLOSURE_APPROVED=false \
    HINTERLAND_INAT_CV_BENCHMARK_APPROVED=false DRAGONFLY_INAT_CV_BENCHMARK_APPROVED=false \
    HINTERLAND_INAT_SUBMIT_ENABLED=false DRAGONFLY_INAT_SUBMIT_ENABLED=false \
  --output none
token_env_count="$(az containerapp show --name "$APP_NAME" --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --query "length(properties.template.containers[0].env[?name=='HINTERLAND_INAT_OAUTH_TOKEN' || name=='DRAGONFLY_INAT_OAUTH_TOKEN'])" -o tsv)"
[[ "$token_env_count" == "0" ]] || { echo "FATAL: iNaturalist token remains exposed to API" >&2; exit 1; }

for forbidden in \
  "${PREFIX}-inat-job" \
  "${PREFIX}-inat-submit-worker" \
  "${PREFIX}-inat-outbox-replay"; do
  az containerapp job delete --name "$forbidden" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --yes --output none 2>/dev/null || true
  if az containerapp job show --name "$forbidden" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
    echo "FATAL: forbidden iNaturalist job remains: $forbidden" >&2
    exit 1
  fi
done
inat_jobs_tsv="$(az containerapp job list --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --query "[?starts_with(name, '${PREFIX}-') && (contains(name, 'inat') || contains(name, 'inaturalist'))].name" -o tsv)"
mapfile -t inat_jobs <<< "$inat_jobs_tsv"
for forbidden in "${inat_jobs[@]}"; do
  [[ -n "$forbidden" ]] || continue
  az containerapp job delete --name "$forbidden" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --yes --output none
done
remaining_inat_jobs="$(az containerapp job list --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" \
  --query "length([?starts_with(name, '${PREFIX}-') && (contains(name, 'inat') || contains(name, 'inaturalist'))])" -o tsv)"
[[ "$remaining_inat_jobs" == "0" ]] || { echo "FATAL: iNaturalist job remains" >&2; exit 1; }

# Prevent the pre-W1 replay implementation from dispatching users while the
# migration queues authoritative rebuilds. It is recreated from the new digest
# only after catalog/content sync and an initial rebuild pass.
az containerapp job delete --name "${PREFIX}-dispatcher-replay" \
  --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --yes --output none 2>/dev/null || true
if az containerapp job show --name "${PREFIX}-dispatcher-replay" \
  --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  echo "FATAL: legacy dispatcher replay job remains" >&2
  exit 1
fi

for provider in Microsoft.ServiceBus Microsoft.EventGrid; do
  if [[ "$(az provider show --namespace "$provider" --subscription "$SUBSCRIPTION" --query registrationState -o tsv 2>/dev/null || true)" != "Registered" ]]; then
    az provider register --namespace "$provider" --subscription "$SUBSCRIPTION" --wait
  fi
done

echo "==> ensure private taxonomy-pack container"
az storage container-rm create --name "$PACKS_CONTAINER" \
  --storage-account "$SA_NAME" --resource-group "$RG" \
  --public-access off --subscription "$SUBSCRIPTION" --output none

echo "==> ensure moderation Service Bus queue"
if ! az servicebus namespace show --name "$SB_NAMESPACE" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  az servicebus namespace create --name "$SB_NAMESPACE" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --location "$LOCATION" --sku Standard \
    --tags project=hinterland env=dev managed-by=cli --output none
fi
SB_NAMESPACE_ID="$(az servicebus namespace show --name "$SB_NAMESPACE" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query id -o tsv)"
if ! az servicebus queue show --namespace-name "$SB_NAMESPACE" --name "$MODERATION_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  az servicebus queue create --namespace-name "$SB_NAMESPACE" \
    --name "$MODERATION_QUEUE" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --max-delivery-count 5 \
    --enable-dead-lettering-on-message-expiration true --lock-duration PT5M \
    --output none
fi

MODERATION_QUEUE_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}/queues/${MODERATION_QUEUE}"
INAT_QUEUE_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}/queues/${INAT_QUEUE}"
# Namespace-level data roles grant inherited access to every queue, including
# `inat-submit`. Replace them with least-privilege moderation-queue grants.
for role in \
  "Azure Service Bus Data Sender" \
  "Azure Service Bus Data Receiver" \
  "Azure Service Bus Data Owner"; do
  az role assignment delete --assignee-object-id "$UAMI_PRINCIPAL" \
    --role "$role" --scope "$SB_NAMESPACE_ID" --subscription "$SUBSCRIPTION" \
    --output none 2>/dev/null || true
done
if az servicebus queue show --namespace-name "$SB_NAMESPACE" --name "$INAT_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  for role in \
    "Azure Service Bus Data Sender" \
    "Azure Service Bus Data Receiver" \
    "Azure Service Bus Data Owner"; do
    az role assignment delete --assignee-object-id "$UAMI_PRINCIPAL" \
      --role "$role" --scope "$INAT_QUEUE_ID" --subscription "$SUBSCRIPTION" \
      --output none 2>/dev/null || true
  done
fi
for role in \
  "Azure Service Bus Data Sender" \
  "Azure Service Bus Data Receiver" \
  "Azure Service Bus Data Owner"; do
  wait_for_role_absent "$SB_NAMESPACE_ID" "$role" 1
  if az servicebus queue show --namespace-name "$SB_NAMESPACE" --name "$INAT_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
    wait_for_role_absent "$INAT_QUEUE_ID" "$role" 1
  fi
done
for role in "Azure Service Bus Data Sender" "Azure Service Bus Data Receiver"; do
  count="$(role_count "$MODERATION_QUEUE_ID" "$role")"
  if [[ "$count" == "0" ]]; then
    az role assignment create --assignee-object-id "$UAMI_PRINCIPAL" \
      --assignee-principal-type ServicePrincipal --role "$role" \
      --scope "$MODERATION_QUEUE_ID" --subscription "$SUBSCRIPTION" --output none
  fi
  wait_for_direct_role_present "$MODERATION_QUEUE_ID" "$role"
done

echo "==> remove direct BlobCreated moderation delivery"
# Azure may auto-name a storage system topic, so the configured legacy name is
# not authoritative. Discover every topic whose source is this storage account.
storage_topics_tsv="$(az eventgrid system-topic list \
  --resource-group "$RG" --subscription "$SUBSCRIPTION" --query "[].name" -o tsv)"
mapfile -t storage_topics <<< "$storage_topics_tsv"
for system_topic in "${storage_topics[@]}"; do
  [[ -n "$system_topic" ]] || continue
  topic_source="$(az eventgrid system-topic show --name "$system_topic" \
    --resource-group "$RG" --subscription "$SUBSCRIPTION" --query source -o tsv)"
  [[ "${topic_source,,}" == "${STORAGE_ACCOUNT_ID,,}" ]] || continue
  direct_subscriptions_tsv="$(az eventgrid system-topic event-subscription list \
    --system-topic-name "$system_topic" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" \
    --query "[?(destination.resourceId && contains(destination.resourceId, '/queues/${MODERATION_QUEUE}')) || (filter.subjectBeginsWith && contains(filter.subjectBeginsWith, '/containers/${PHOTOS_CONTAINER}/blobs/pending/'))].name" -o tsv)"
  mapfile -t direct_subscriptions <<< "$direct_subscriptions_tsv"
  for event_subscription in "${direct_subscriptions[@]}"; do
    [[ -n "$event_subscription" ]] || continue
    az eventgrid system-topic event-subscription delete \
      --system-topic-name "$system_topic" --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" --name "$event_subscription" --yes --output none
  done
  remaining="$(az eventgrid system-topic event-subscription list \
    --system-topic-name "$system_topic" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" \
    --query "length([?(destination.resourceId && contains(destination.resourceId, '/queues/${MODERATION_QUEUE}')) || (filter.subjectBeginsWith && contains(filter.subjectBeginsWith, '/containers/${PHOTOS_CONTAINER}/blobs/pending/'))])" -o tsv)"
  [[ "$remaining" == "0" ]] || { echo "FATAL: direct moderation producer remains on $system_topic" >&2; exit 1; }
done

echo "==> apply/verify lifecycle policy"
POLICY_PATH="${SCRIPT_DIR}/policies/observation-w1-lifecycle.json"
APPLY_LIFECYCLE=1
if az storage account management-policy show --account-name "$SA_NAME" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  raw_count="$(az storage account management-policy show --account-name "$SA_NAME" \
    --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --query "length(policy.rules[?name=='observation-unattached-upload-24h'])" -o tsv)"
  held_count="$(az storage account management-policy show --account-name "$SA_NAME" \
    --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --query "length(policy.rules[?name=='observation-quarantine-rejected-90d'])" -o tsv)"
  pilot_count="$(az storage account management-policy show --account-name "$SA_NAME" \
    --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --query "length(policy.rules[?name=='observation-pilot-private-7d'])" -o tsv)"
  if [[ "$raw_count" == "1" && "$held_count" == "1" && "$pilot_count" == "1" ]]; then
    raw_days="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-unattached-upload-24h'].definition.actions.baseBlob.delete.daysAfterModificationGreaterThan | [0]" -o tsv)"
    raw_prefix="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-unattached-upload-24h'].definition.filters.prefixMatch | [0] | join(',', @)" -o tsv)"
    held_days="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-quarantine-rejected-90d'].definition.actions.baseBlob.delete.daysAfterModificationGreaterThan | [0]" -o tsv)"
    held_prefixes="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-quarantine-rejected-90d'].definition.filters.prefixMatch | [0] | join(',', @)" -o tsv)"
    pilot_days="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-pilot-private-7d'].definition.actions.baseBlob.delete.daysAfterModificationGreaterThan | [0]" -o tsv)"
    pilot_prefix="$(az storage account management-policy show --account-name "$SA_NAME" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --query "policy.rules[?name=='observation-pilot-private-7d'].definition.filters.prefixMatch | [0] | join(',', @)" -o tsv)"
    if [[ "$raw_days" == "1" && "$raw_prefix" == "photos/pending/uploads/" \
      && "$held_days" == "90" \
      && "$held_prefixes" == "photos/quarantine/,photos/rejected/" \
      && "$pilot_days" == "7" && "$pilot_prefix" == "photos/pilot-private/" ]]; then
      APPLY_LIFECYCLE=0
    fi
  fi
  if [[ "$APPLY_LIFECYCLE" == "1" && "${HINTERLAND_REPLACE_LIFECYCLE_POLICY:-0}" != "1" ]]; then
    echo "FATAL: merge $POLICY_PATH with the existing policy, or explicitly approve replacement" >&2
    exit 1
  fi
fi
if [[ "$APPLY_LIFECYCLE" == "1" ]]; then
  POLICY_AZ_PATH="$(azure_file_path "$POLICY_PATH")"
  az storage account management-policy create --account-name "$SA_NAME" \
    --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --policy "@${POLICY_AZ_PATH}" --output none
fi

az containerapp update --name "$APP_NAME" --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" --set-env-vars \
    HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED=true \
    DRAGONFLY_OBSERVATION_IDEMPOTENCY_REQUIRED=true \
    "HINTERLAND_TAXONOMY_PACKS_BUCKET=${PACKS_CONTAINER}" \
    "DRAGONFLY_TAXONOMY_PACKS_BUCKET=${PACKS_CONTAINER}" \
    "HINTERLAND_SERVICE_BUS_NAMESPACE=${SB_NAMESPACE}.servicebus.windows.net" \
    "DRAGONFLY_SERVICE_BUS_NAMESPACE=${SB_NAMESPACE}.servicebus.windows.net" \
    "HINTERLAND_SERVICE_BUS_MODERATION_QUEUE=${MODERATION_QUEUE}" \
    "DRAGONFLY_SERVICE_BUS_MODERATION_QUEUE=${MODERATION_QUEUE}" --output none

yaml_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"; }

write_job_yaml() {
  local path="$1" name="$2" command="$3" schedule="$4"
  cat > "$path" <<EOF
name: $(yaml_quote "$name")
location: $(yaml_quote "$LOCATION")
identity:
  type: UserAssigned
  userAssignedIdentities:
    $(yaml_quote "$UAMI_ID"): {}
properties:
  environmentId: $(yaml_quote "$CAE_ID")
  configuration:
$(if [[ "$schedule" == "manual" ]]; then
    printf '%s\n' '    triggerType: Manual'
  else
    cat <<TRIGGER
    triggerType: Schedule
    scheduleTriggerConfig:
      cronExpression: $(yaml_quote "$schedule")
      parallelism: 1
      replicaCompletionCount: 1
TRIGGER
  fi)
    replicaTimeout: 1800
    replicaRetryLimit: 1
    registries:
    - server: $(yaml_quote "${ACR_NAME}.azurecr.io")
      identity: $(yaml_quote "$UAMI_ID")
    secrets:
    - name: pg-password
      keyVaultUrl: $(yaml_quote "https://${KV_NAME}.vault.azure.net/secrets/postgres-admin-password")
      identity: $(yaml_quote "$UAMI_ID")
  template:
    containers:
    - name: $(yaml_quote "$name")
      image: $(yaml_quote "$IMAGE")
      command: [/bin/sh, -c]
      args: [$(yaml_quote "$command")]
      resources: {cpu: 0.5, memory: 1Gi}
      env:
      - {name: DRAGONFLY_ENV, value: $(yaml_quote "$ENVIRONMENT")}
      - {name: AZURE_CLIENT_ID, value: $(yaml_quote "$UAMI_CLIENT_ID")}
      - {name: DRAGONFLY_DATABASE_HOST, value: $(yaml_quote "$PG_HOST")}
      - {name: DRAGONFLY_DATABASE_PORT, value: '5432'}
      - {name: DRAGONFLY_DATABASE_USER, value: $(yaml_quote "$PG_USER")}
      - {name: DRAGONFLY_DATABASE_PASSWORD, secretRef: pg-password}
      - {name: DRAGONFLY_DATABASE_NAME, value: $(yaml_quote "$PG_DB")}
      - {name: DRAGONFLY_STORAGE_PROVIDER, value: blob}
      - {name: DRAGONFLY_BLOB_ACCOUNT_ENDPOINT, value: $(yaml_quote "$BLOB_ENDPOINT")}
      - {name: DRAGONFLY_PHOTOS_BUCKET, value: $(yaml_quote "$PHOTOS_CONTAINER")}
      - {name: DRAGONFLY_TAXONOMY_PACKS_BUCKET, value: $(yaml_quote "$PACKS_CONTAINER")}
      - {name: DRAGONFLY_MODERATION_PROVIDER, value: noop}
      - {name: DRAGONFLY_INAT_CV_ENABLED, value: 'false'}
      - {name: DRAGONFLY_INAT_CV_DISCLOSURE_APPROVED, value: 'false'}
      - {name: DRAGONFLY_INAT_CV_BENCHMARK_APPROVED, value: 'false'}
      - {name: DRAGONFLY_INAT_SUBMIT_ENABLED, value: 'false'}
      - {name: DRAGONFLY_SERVICE_BUS_NAMESPACE, value: $(yaml_quote "${SB_NAMESPACE}.servicebus.windows.net")}
      - {name: DRAGONFLY_SERVICE_BUS_MODERATION_QUEUE, value: $(yaml_quote "$MODERATION_QUEUE")}
      - {name: OBSERVATION_PREFLIGHT_ACK, value: $(yaml_quote "$PREFLIGHT_ACK")}
EOF
}

ensure_job() {
  local name="$1" command="$2" schedule="$3" job_yaml job_yaml_az
  job_yaml="$(mktemp)"
  write_job_yaml "$job_yaml" "$name" "$command" "$schedule"
  job_yaml_az="$(azure_file_path "$job_yaml")"
  if az containerapp job show --name "$name" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
    az containerapp job update --name "$name" --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" --yaml "$job_yaml_az" --output none
  else
    az containerapp job create --name "$name" --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" --yaml "$job_yaml_az" --output none
  fi
  rm -f "$job_yaml"
}

wait_for_job() {
  local name="$1" execution status
  execution="$(az containerapp job start --name "$name" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --query name -o tsv)"
  for attempt in $(seq 1 60); do
    status="$(az containerapp job execution show --name "$name" \
      --resource-group "$RG" --subscription "$SUBSCRIPTION" \
      --job-execution-name "$execution" --query properties.status -o tsv)"
    [[ "$status" == "Succeeded" ]] && return
    [[ "$status" == "Failed" || "$status" == "Canceled" ]] && break
    sleep 10
  done
  echo "FATAL: $name execution $execution ended as ${status:-timeout}" >&2
  exit 1
}

echo "==> run read-only Observation migration preflight"
ensure_job "${PREFIX}-obs-preflight" "python -m admin.observation_migration_preflight" manual
wait_for_job "${PREFIX}-obs-preflight"
echo "==> run additive migrations"
ensure_job "${PREFIX}-migrate" "/app/.venv/bin/alembic upgrade head" manual
wait_for_job "${PREFIX}-migrate"

echo "==> create/update W1 jobs"
# Rebuilds use the current checked-in catalog and Expedition rules. Materialize
# both from this digest before adopting legacy rows or running the first rebuild
# pass.
ensure_job "${PREFIX}-taxa-catalog-ingest" "python -m admin.taxa_catalog_ingest" manual
wait_for_job "${PREFIX}-taxa-catalog-ingest"
ensure_job "${PREFIX}-sync-expeditions" "python -m admin.sync_expeditions" manual
wait_for_job "${PREFIX}-sync-expeditions"
# Adopt all legacy `pending/<id>.jpg` observations before a relay exists, then
# leave the reconciler scheduled through the one-release compatibility window.
ensure_job "${PREFIX}-legacy-reconcile" "python -m admin.observation_legacy_reconcile" "*/5 * * * *"
wait_for_job "${PREFIX}-legacy-reconcile"
ensure_job "${PREFIX}-state-rebuild" "python -m admin.derived_state_rebuild" "*/5 * * * *"
wait_for_job "${PREFIX}-state-rebuild"
ensure_job "${PREFIX}-moderation-job" "python -m admin.moderation_consumer --max-messages 8" "* * * * *"
# Event Grid is gone and the outbox relay does not exist yet, so any active
# messages at this point are stale storage events (or earlier committed work).
# The new consumer dead-letters storage-event payloads and validates every
# committed envelope against the DB; drain them before enabling the sole relay.
for attempt in $(seq 1 25); do
  active_messages="$(az servicebus queue show --namespace-name "$SB_NAMESPACE" \
    --name "$MODERATION_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" \
    --query countDetails.activeMessageCount -o tsv)"
  [[ "$active_messages" == "0" ]] && break
  wait_for_job "${PREFIX}-moderation-job"
done
active_messages="$(az servicebus queue show --namespace-name "$SB_NAMESPACE" \
  --name "$MODERATION_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --query countDetails.activeMessageCount -o tsv)"
[[ "$active_messages" == "0" ]] || { echo "FATAL: stale moderation queue did not drain" >&2; exit 1; }
dead_messages="$(az servicebus queue show --namespace-name "$SB_NAMESPACE" \
  --name "$MODERATION_QUEUE" --resource-group "$RG" --subscription "$SUBSCRIPTION" \
  --query countDetails.deadLetterMessageCount -o tsv)"
echo "  moderation DLQ after stale-event drain: $dead_messages"
ensure_job "${PREFIX}-mod-outbox-relay" "python -m admin.moderation_outbox_relay" "* * * * *"
ensure_job "${PREFIX}-dispatcher-replay" "python -m admin.dispatcher_replay" "*/15 * * * *"
ensure_job "${PREFIX}-obs-retention" "python -m admin.observation_retention" "0 * * * *"
ensure_job "${PREFIX}-obs-health" "python -m admin.observation_health_probe" "*/5 * * * *"
ensure_job "${PREFIX}-sweep-stale-reviews" "python -m admin.sweep_stale_reviews" "0 4 * * *"
ensure_job "${PREFIX}-rarity-refresh" "python -m admin.rarity_refresh" "0 3 * * *"
ensure_job "${PREFIX}-expedition-funnel" "python -m admin.expedition_funnel" manual

all_jobs_tsv="$(az containerapp job list --resource-group "$RG" \
  --subscription "$SUBSCRIPTION" --query "[?starts_with(name, '${PREFIX}-')].name" -o tsv)"
[[ -n "$all_jobs_tsv" ]] || { echo "FATAL: no Hinterland jobs found after provisioning" >&2; exit 1; }
mapfile -t all_jobs <<< "$all_jobs_tsv"
for job in "${all_jobs[@]}"; do
  [[ -n "$job" ]] || continue
  az containerapp job update --name "$job" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --remove-env-vars \
    HINTERLAND_INAT_OAUTH_TOKEN DRAGONFLY_INAT_OAUTH_TOKEN --output none
  az containerapp job update --name "$job" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --image "$IMAGE" --set-env-vars \
    HINTERLAND_MODERATION_PROVIDER=noop DRAGONFLY_MODERATION_PROVIDER=noop \
    HINTERLAND_INAT_CV_ENABLED=false DRAGONFLY_INAT_CV_ENABLED=false \
    HINTERLAND_INAT_CV_DISCLOSURE_APPROVED=false DRAGONFLY_INAT_CV_DISCLOSURE_APPROVED=false \
    HINTERLAND_INAT_CV_BENCHMARK_APPROVED=false DRAGONFLY_INAT_CV_BENCHMARK_APPROVED=false \
    HINTERLAND_INAT_SUBMIT_ENABLED=false DRAGONFLY_INAT_SUBMIT_ENABLED=false \
    --output none
done

echo "done: W1 Observation operations configured in $RG"
echo "  image: $IMAGE"
echo "  moderation: noop -> pilot_private"
echo "  iNaturalist CV/submit: disabled"
echo "  Event Grid moderation: removed"
