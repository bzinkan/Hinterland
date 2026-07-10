#!/usr/bin/env bash
# Azure Monitor rules for the isolated Hinterland Observation pipeline.

set -euo pipefail

# Normalize Azure CLI TSV output under Windows Git Bash (CRLF otherwise leaves
# a trailing carriage return in command substitutions and mapfile entries).
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HINTERLAND_ENV_FILE:-${1:-${SCRIPT_DIR}/environments/hinterland-dev.env}}"
[[ -f "$ENV_FILE" ]] || { echo "FATAL: missing $ENV_FILE" >&2; exit 1; }
# shellcheck source=environments/hinterland-dev.env
source "$ENV_FILE"

RG="${HINTERLAND_RESOURCE_GROUP:?missing HINTERLAND_RESOURCE_GROUP}"
SUBSCRIPTION="${AZURE_SUBSCRIPTION_ID:?missing AZURE_SUBSCRIPTION_ID}"
TENANT="${AZURE_TENANT_ID:?missing AZURE_TENANT_ID}"
LAW_NAME="${HINTERLAND_LOG_ANALYTICS_WORKSPACE:?missing HINTERLAND_LOG_ANALYTICS_WORKSPACE}"
SB_NAMESPACE="${HINTERLAND_SERVICE_BUS_NAMESPACE:?missing HINTERLAND_SERVICE_BUS_NAMESPACE}"
MODERATION_QUEUE="${HINTERLAND_MODERATION_QUEUE:?missing HINTERLAND_MODERATION_QUEUE}"
ACTION_GROUP="${HINTERLAND_OPS_ACTION_GROUP:?missing HINTERLAND_OPS_ACTION_GROUP}"
PREFIX="${PROJECT_SLUG:-hinterland}"
ALERT_EMAIL="${HINTERLAND_ALERT_EMAIL:-}"

[[ "$RG" == "hinterland-dev-rg" && "$RG" != "gordi-pilot-rg" ]] || {
  echo "FATAL: refusing non-isolated resource group $RG" >&2; exit 1;
}
[[ -n "$ALERT_EMAIL" ]] || {
  echo "FATAL: set HINTERLAND_ALERT_EMAIL to a monitored address" >&2; exit 1;
}

az account set --subscription "$SUBSCRIPTION"
[[ "$(az account show --query id -o tsv)" == "$SUBSCRIPTION" ]] || exit 1
[[ "$(az account show --query tenantId -o tsv)" == "$TENANT" ]] || exit 1
if ! az extension show --name scheduled-query >/dev/null 2>&1; then
  az extension add --name scheduled-query --yes --output none
fi

LAW_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.OperationalInsights/workspaces/${LAW_NAME}"
SB_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}"

if ! az monitor action-group show --name "$ACTION_GROUP" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then
  az monitor action-group create --name "$ACTION_GROUP" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --short-name hlandops \
    --action email primary "$ALERT_EMAIL" \
    --tags project=hinterland env=dev managed-by=cli --output none
fi
ACTION_GROUP_ID="$(az monitor action-group show --name "$ACTION_GROUP" --resource-group "$RG" --subscription "$SUBSCRIPTION" --query id -o tsv)"

ensure_metric_alert() {
  local name="$1" condition="$2" description="$3"
  if az monitor metrics alert show --name "$name" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then return; fi
  az monitor metrics alert create --name "$name" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --scopes "$SB_ID" \
    --condition "$condition" --description "$description" \
    --evaluation-frequency 5m --window-size 5m --severity 2 \
    --action "$ACTION_GROUP_ID" --output none
}

ensure_log_alert() {
  local name="$1" description="$2" query="$3"
  if az monitor scheduled-query show --name "$name" --resource-group "$RG" --subscription "$SUBSCRIPTION" >/dev/null 2>&1; then return; fi
  az monitor scheduled-query create --name "$name" --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" --scopes "$LAW_ID" \
    --condition "count 'Signal' > 0" --condition-query "Signal=${query}" \
    --description "$description" --evaluation-frequency 5m --window-size 10m \
    --severity 2 --action-groups "$ACTION_GROUP_ID" --output none
}

ensure_metric_alert "${PREFIX}-moderation-queue-depth" \
  "avg ActiveMessages > 25 where EntityName includes ${MODERATION_QUEUE}" \
  "Observation moderation queue depth exceeds 25"
ensure_metric_alert "${PREFIX}-moderation-dlq" \
  "avg DeadletteredMessages > 0 where EntityName includes ${MODERATION_QUEUE}" \
  "Observation moderation DLQ contains work"

HEALTH_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| extend stale_moderation_outbox = toint(extract(@'"stale_moderation_outbox"\s*:\s*([0-9]+)', 1, raw)),
         stale_pending_photos = toint(extract(@'"stale_pending_photos"\s*:\s*([0-9]+)', 1, raw)),
         stale_dispatch_runs = toint(extract(@'"stale_dispatch_runs"\s*:\s*([0-9]+)', 1, raw)),
         stale_rebuilds = toint(extract(@'"stale_rebuilds"\s*:\s*([0-9]+)', 1, raw)),
         failed_rebuilds = toint(extract(@'"failed_rebuilds"\s*:\s*([0-9]+)', 1, raw)),
         state_mismatches = toint(extract(@'"state_mismatches"\s*:\s*([0-9]+)', 1, raw))
| where stale_moderation_outbox > 0 or stale_pending_photos > 0 or stale_dispatch_runs > 0 or stale_rebuilds > 0 or failed_rebuilds > 0 or state_mismatches > 0
| summarize count() by bin(TimeGenerated, 5m)
KQL
)
ensure_log_alert "${PREFIX}-observation-state-health" \
  "Observation work is stale, rebuild failed, or lifecycle states disagree" \
  "$HEALTH_QUERY"

PROBE_MISSING_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| summarize probes=count()
| where probes == 0
KQL
)
ensure_log_alert "${PREFIX}-observation-probe-missing" \
  "No Observation health probe completed" "$PROBE_MISSING_QUERY"

DISPATCHER_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| extend properties = todynamic(column_ifexists("Properties", dynamic({})))
| where raw has "dispatcher.complete"
| extend duration_ms = coalesce(toreal(properties.duration_ms), todouble(extract(@'"duration_ms"\s*:\s*([0-9.]+)', 1, raw)))
| where isnotnull(duration_ms)
| summarize p95=percentile(duration_ms, 95) by bin(TimeGenerated, 5m)
| where p95 > 300
KQL
)
ensure_log_alert "${PREFIX}-dispatcher-p95" \
  "Observation dispatcher p95 exceeded 300 ms" "$DISPATCHER_QUERY"

RETENTION_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.retention.delete_failed"
| summarize count() by bin(TimeGenerated, 5m)
KQL
)
ensure_log_alert "${PREFIX}-observation-retention-failure" \
  "Observation retention could not delete expired private bytes" "$RETENTION_QUERY"

IDEMPOTENCY_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppRequests, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Url", "")), tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| extend status = coalesce(toint(column_ifexists("ResultCode", "0")), toint(extract(@'"status_code"\s*:\s*([0-9]+)', 1, raw)))
| where status == 409 and (raw has "/v1/photos/presign" or raw has "/v1/observations")
| summarize conflicts=count() by bin(TimeGenerated, 5m)
| where conflicts > 5
KQL
)
ensure_log_alert "${PREFIX}-observation-idempotency-conflicts" \
  "More than five idempotency conflicts occurred in five minutes" \
  "$IDEMPOTENCY_QUERY"

JOB_FAILURE_QUERY=$(cat <<KQL
let rows = union isfuzzy=true ContainerAppSystemLogs, ContainerAppSystemLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")), tostring(column_ifexists("Message", "")))
| extend job = coalesce(tostring(column_ifexists("ContainerJobName", "")), tostring(column_ifexists("ContainerJobName_s", "")))
| where raw has "Failed"
| where job in (
    "${PREFIX}-obs-preflight", "${PREFIX}-migrate",
    "${PREFIX}-legacy-reconcile", "${PREFIX}-moderation-job",
    "${PREFIX}-mod-outbox-relay", "${PREFIX}-dispatcher-replay",
    "${PREFIX}-state-rebuild", "${PREFIX}-obs-retention",
    "${PREFIX}-obs-health",
    "${PREFIX}-sweep-stale-reviews", "${PREFIX}-taxa-catalog-ingest")
| summarize count() by job, bin(TimeGenerated, 5m)
KQL
)
ensure_log_alert "${PREFIX}-observation-job-failures" \
  "An Observation worker or recovery job failed" "$JOB_FAILURE_QUERY"

echo "done: alerts target $ACTION_GROUP ($ALERT_EMAIL)"
echo "Synthetically trigger and verify every alert before closed beta."
