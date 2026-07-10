#!/usr/bin/env bash
# Provision and verify the Azure Monitor contract for Observation W1.

set -euo pipefail

APPLY=0
VERIFY=0
SYNTHETIC=0
ENV_FILE=""

usage() {
  cat <<'EOF'
Usage: observation-w1-monitoring.sh [--apply] [--verify] [--synthetic]
                                     [--env-file PATH]

With no mode flags, --apply and --verify are run. --synthetic sends a safe
Azure action-group test notification; it does not inject child or photo data.

Apply mode requires HINTERLAND_ALERT_EMAIL. Optional sanitized test evidence
is written to HINTERLAND_MONITORING_EVIDENCE_PATH.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --verify) VERIFY=1 ;;
    --synthetic) SYNTHETIC=1 ;;
    --env-file)
      shift
      ENV_FILE="${1:-}"
      [[ -n "$ENV_FILE" ]] || { echo "FATAL: --env-file needs a path" >&2; exit 2; }
      ;;
    --help|-h) usage; exit 0 ;;
    *) echo "FATAL: unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$APPLY" == 0 && "$VERIFY" == 0 && "$SYNTHETIC" == 0 ]]; then
  APPLY=1
  VERIFY=1
fi

az() {
  local argument normalize_tsv=0
  for argument in "$@"; do
    [[ "$argument" == "tsv" ]] && normalize_tsv=1
  done
  if [[ "$normalize_tsv" == 1 ]]; then
    command az "$@" | tr -d '\r'
  else
    command az "$@"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HINTERLAND_ENV_FILE:-${ENV_FILE:-${SCRIPT_DIR}/environments/hinterland-dev.env}}"
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
  echo "FATAL: refusing non-isolated resource group $RG" >&2
  exit 1
}

az account set --subscription "$SUBSCRIPTION"
[[ "$(az account show --query id --output tsv)" == "$SUBSCRIPTION" ]] || exit 1
[[ "$(az account show --query tenantId --output tsv)" == "$TENANT" ]] || exit 1

if ! az extension show --name scheduled-query >/dev/null 2>&1; then
  az extension add --name scheduled-query --yes --output none
fi

LAW_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.OperationalInsights/workspaces/${LAW_NAME}"
# Azure Monitor exposes Service Bus queue metrics from the namespace resource,
# with the individual queue selected through the EntityName metric dimension.
# A queue resource ID is not a supported platform-metric alert scope.
MODERATION_NAMESPACE_ID="/subscriptions/${SUBSCRIPTION}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}"

metric_alerts=(
  "${PREFIX}-moderation-queue-depth"
  "${PREFIX}-moderation-dlq"
)
log_alerts=(
  "${PREFIX}-observation-work-age"
  "${PREFIX}-rebuild-backlog-failure"
  "${PREFIX}-dispatcher-backlog"
  "${PREFIX}-dispatcher-p95"
  "${PREFIX}-observation-idempotency-conflicts"
  "${PREFIX}-observation-state-mismatch"
  "${PREFIX}-photo-revocation-failure"
  "${PREFIX}-observation-probe-missing"
  "${PREFIX}-observation-job-failures"
)

ensure_metric_alert() {
  local name="$1" condition="$2" description="$3"
  if az monitor metrics alert show --name "$name" --resource-group "$RG" >/dev/null 2>&1; then
    # These rules are owned by this artifact. Recreate them so an old scope,
    # threshold, or action-group reference can never survive an apply and
    # falsely satisfy the promotion verification.
    az monitor metrics alert delete \
      --name "$name" \
      --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" \
      --output none
  fi
  az monitor metrics alert create \
    --name "$name" \
    --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" \
    --scopes "$MODERATION_NAMESPACE_ID" \
    --condition "$condition" \
    --description "$description" \
    --evaluation-frequency 5m \
    --window-size 5m \
    --severity 2 \
    --action "$ACTION_GROUP_ID" \
    --tags project=hinterland env=dev managed-by=observation-w1 \
    --output none
}

ensure_log_alert() {
  local name="$1" description="$2" query="$3"
  if az monitor scheduled-query show --name "$name" --resource-group "$RG" >/dev/null 2>&1; then
    az monitor scheduled-query delete \
      --name "$name" \
      --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" \
      --yes \
      --output none
  fi
  az monitor scheduled-query create \
    --name "$name" \
    --resource-group "$RG" \
    --subscription "$SUBSCRIPTION" \
    --scopes "$LAW_ID" \
    --condition "count 'Signal' > 0" \
    --condition-query "Signal=${query}" \
    --description "$description" \
    --evaluation-frequency 5m \
    --window-size 15m \
    --severity 2 \
    --action-groups "$ACTION_GROUP_ID" \
    --auto-mitigate true \
    --tags project=hinterland env=dev managed-by=observation-w1 \
    --output none
}

if [[ "$APPLY" == 1 ]]; then
  [[ -n "$ALERT_EMAIL" ]] || {
    echo "FATAL: HINTERLAND_ALERT_EMAIL is required by --apply" >&2
    exit 1
  }

  if ! az monitor action-group show \
    --name "$ACTION_GROUP" --resource-group "$RG" >/dev/null 2>&1; then
    az monitor action-group create \
      --name "$ACTION_GROUP" \
      --resource-group "$RG" \
      --subscription "$SUBSCRIPTION" \
      --short-name hlandops \
      --action email primary "$ALERT_EMAIL" usecommonalertschema \
      --tags project=hinterland env=dev managed-by=observation-w1 \
      --output none
  fi

  ACTION_GROUP_ID="$(az monitor action-group show \
    --name "$ACTION_GROUP" --resource-group "$RG" --query id --output tsv)"

  ensure_metric_alert \
    "${PREFIX}-moderation-queue-depth" \
    "avg ActiveMessages > 25 where EntityName includes ${MODERATION_QUEUE}" \
    "Observation moderation queue depth exceeds 25"
  ensure_metric_alert \
    "${PREFIX}-moderation-dlq" \
    "avg DeadletteredMessages > 0 where EntityName includes ${MODERATION_QUEUE}" \
    "Observation moderation DLQ contains work"

  WORK_AGE_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| extend stale_moderation_outbox = toint(extract(@'"stale_moderation_outbox"\s*:\s*([0-9]+)', 1, raw)),
         stale_pending_photos = toint(extract(@'"stale_pending_photos"\s*:\s*([0-9]+)', 1, raw))
| where stale_moderation_outbox > 0 or stale_pending_photos > 0
KQL
)
  ensure_log_alert \
    "${PREFIX}-observation-work-age" \
    "Moderation outbox or pending-photo work exceeded its age bound" \
    "$WORK_AGE_QUERY"

  REBUILD_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| extend stale_rebuilds = toint(extract(@'"stale_rebuilds"\s*:\s*([0-9]+)', 1, raw)),
         failed_rebuilds = toint(extract(@'"failed_rebuilds"\s*:\s*([0-9]+)', 1, raw))
| where stale_rebuilds > 0 or failed_rebuilds > 0
KQL
)
  ensure_log_alert \
    "${PREFIX}-rebuild-backlog-failure" \
    "Derived-state rebuild is stale or terminally failed" \
    "$REBUILD_QUERY"

  DISPATCH_BACKLOG_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| extend stale_dispatch_runs = toint(extract(@'"stale_dispatch_runs"\s*:\s*([0-9]+)', 1, raw))
| where stale_dispatch_runs > 0
KQL
)
  ensure_log_alert \
    "${PREFIX}-dispatcher-backlog" \
    "Observation dispatch pending, partial, or blocked work is stale" \
    "$DISPATCH_BACKLOG_QUERY"

  DISPATCHER_P95_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", ""))),
         properties = todynamic(column_ifexists("Properties", dynamic({})))
| where raw has "dispatcher.complete"
| extend duration_ms = coalesce(toreal(properties.duration_ms), todouble(extract(@'"duration_ms"\s*:\s*([0-9.]+)', 1, raw)))
| where isnotnull(duration_ms)
| summarize p95=percentile(duration_ms, 95) by bin(TimeGenerated, 5m)
| where p95 > 300
KQL
)
  ensure_log_alert \
    "${PREFIX}-dispatcher-p95" \
    "Observation dispatcher p95 exceeded 300 ms" \
    "$DISPATCHER_P95_QUERY"

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
  ensure_log_alert \
    "${PREFIX}-observation-idempotency-conflicts" \
    "More than five Observation idempotency conflicts occurred in five minutes" \
    "$IDEMPOTENCY_QUERY"

  STATE_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| extend state_mismatches = toint(extract(@'"state_mismatches"\s*:\s*([0-9]+)', 1, raw))
| where state_mismatches > 0
KQL
)
  ensure_log_alert \
    "${PREFIX}-observation-state-mismatch" \
    "Observation, photo, outbox, or attachment states disagree" \
    "$STATE_QUERY"

  REVOCATION_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| extend stale_photo_revocations = toint(extract(@'"stale_photo_revocations"\s*:\s*([0-9]+)', 1, raw)),
         failed_photo_revocations = toint(extract(@'"failed_photo_revocations"\s*:\s*([0-9]+)', 1, raw))
| where (raw has "observation.ops_probe" and (stale_photo_revocations > 0 or failed_photo_revocations > 0))
    or raw has_any ("photo_revocation.terminal_failure", "photo.revocation.terminal_failure", "photo_revocation.retry_exhausted")
KQL
)
  ensure_log_alert \
    "${PREFIX}-photo-revocation-failure" \
    "A clean-photo revocation is stale or exhausted its bounded retries" \
    "$REVOCATION_QUERY"

  PROBE_MISSING_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Message", "")), tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")))
| where raw has "observation.ops_probe"
| summarize probes=count()
| where probes == 0
KQL
)
  ensure_log_alert \
    "${PREFIX}-observation-probe-missing" \
    "No Observation health probe completed in the alert window" \
    "$PROBE_MISSING_QUERY"

  JOB_FAILURE_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true ContainerAppSystemLogs, ContainerAppSystemLogs_CL;
rows
| extend raw = coalesce(tostring(column_ifexists("Log", "")), tostring(column_ifexists("Log_s", "")), tostring(column_ifexists("Message", ""))),
         job = coalesce(tostring(column_ifexists("ContainerJobName", "")), tostring(column_ifexists("ContainerJobName_s", "")))
| where raw has_any ("Failed", "Canceled")
| where job startswith "hinterland-"
| summarize failures=count() by job, bin(TimeGenerated, 5m)
KQL
)
  ensure_log_alert \
    "${PREFIX}-observation-job-failures" \
    "A required Hinterland Container Apps job failed or was canceled" \
    "$JOB_FAILURE_QUERY"
fi

ACTION_GROUP_ID="$(az monitor action-group show \
  --name "$ACTION_GROUP" --resource-group "$RG" --query id --output tsv)"

if [[ "$VERIFY" == 1 ]]; then
  action_group_json="$(az monitor action-group show \
    --name "$ACTION_GROUP" --resource-group "$RG" --output json)"
  enabled_receivers="$(az monitor action-group show \
    --name "$ACTION_GROUP" --resource-group "$RG" \
    --query "length(emailReceivers[?status=='Enabled'])" --output tsv)"
  [[ "$enabled_receivers" -gt 0 ]] || {
    echo "FATAL: $ACTION_GROUP has no enabled email receiver" >&2
    exit 1
  }
  if [[ -n "$ALERT_EMAIL" ]]; then
    expected_receiver_count="$(jq \
      --arg expected "$ALERT_EMAIL" \
      '[.emailReceivers[]? | select(
        (.emailAddress | ascii_downcase) == ($expected | ascii_downcase)
        and .status == "Enabled"
      )] | length' <<< "$action_group_json")"
    [[ "$expected_receiver_count" -gt 0 ]] || {
      echo "FATAL: protected alert receiver is not enabled on $ACTION_GROUP" >&2
      exit 1
    }
  fi

  for name in "${metric_alerts[@]}"; do
    metric_json="$(az monitor metrics alert show \
      --name "$name" --resource-group "$RG" --output json)"
    enabled="$(jq -r '.enabled' <<< "$metric_json")"
    [[ "${enabled,,}" == true ]] || { echo "FATAL: metric alert $name disabled" >&2; exit 1; }
    actions="$(jq '[.actions[]?] | length' <<< "$metric_json")"
    [[ "$actions" -gt 0 ]] || { echo "FATAL: metric alert $name has no action" >&2; exit 1; }

    case "$name" in
      "${PREFIX}-moderation-queue-depth") expected_metric="ActiveMessages" ;;
      "${PREFIX}-moderation-dlq") expected_metric="DeadletteredMessages" ;;
      *) echo "FATAL: unexpected W1 metric alert $name" >&2; exit 1 ;;
    esac
    scope_count="$(jq --arg expected "$MODERATION_NAMESPACE_ID" \
      '[.scopes[]? | select(. == $expected)] | length' <<< "$metric_json")"
    [[ "$scope_count" -eq 1 ]] || {
      echo "FATAL: metric alert $name does not scope the Service Bus namespace" >&2
      exit 1
    }
    criterion_count="$(jq --arg metric "$expected_metric" --arg queue "$MODERATION_QUEUE" '
      [
        .criteria.allOf[]?
        | select(.metricName == $metric)
        | select(
            [
              .dimensions[]?
              | select(
                  .name == "EntityName"
                  and .operator == "Include"
                  and ([.values[]?] | index($queue) != null)
                )
            ] | length > 0
          )
      ] | length
    ' <<< "$metric_json")"
    [[ "$criterion_count" -eq 1 ]] || {
      echo "FATAL: metric alert $name lacks its expected EntityName filter" >&2
      exit 1
    }
  done
  for name in "${log_alerts[@]}"; do
    enabled="$(az monitor scheduled-query show \
      --name "$name" --resource-group "$RG" --query enabled --output tsv)"
    [[ "${enabled,,}" == true ]] || { echo "FATAL: log alert $name disabled" >&2; exit 1; }
    actions="$(az monitor scheduled-query show \
      --name "$name" --resource-group "$RG" \
      --query 'length(actions.actionGroups)' --output tsv)"
    [[ "$actions" -gt 0 ]] || { echo "FATAL: log alert $name has no action" >&2; exit 1; }
  done
  echo "verified: ${#metric_alerts[@]} metric and ${#log_alerts[@]} log alerts"
fi

if [[ "$SYNTHETIC" == 1 ]]; then
  az monitor action-group test-notifications create \
    --action-group "$ACTION_GROUP" \
    --resource-group "$RG" \
    --alert-type logalertv2 \
    --output none
  echo "synthetic action-group notification accepted"

  if [[ -n "${HINTERLAND_MONITORING_EVIDENCE_PATH:-}" ]]; then
    mkdir -p "$(dirname "$HINTERLAND_MONITORING_EVIDENCE_PATH")"
    jq -n \
      --arg tested_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --arg action_group "$ACTION_GROUP" \
      --argjson metric_alert_count "${#metric_alerts[@]}" \
      --argjson log_alert_count "${#log_alerts[@]}" \
      '{result:"accepted", tested_at:$tested_at, action_group:$action_group,
        metric_alert_count:$metric_alert_count, log_alert_count:$log_alert_count}' \
      > "$HINTERLAND_MONITORING_EVIDENCE_PATH"
  fi
fi
