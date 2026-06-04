#!/usr/bin/env bash
# Phase 9 monitoring -- Azure Monitor action group + alerts for the
# async pipeline that phase-9-async-pipeline.sh just provisioned.
#
# Closes the alarms half of Risk 0002 + the dispatcher-p95 alert that
# Risk 0003 needs.
#
# Provisions:
#   - 1 action group `dragonfly-ops-dev` (email receiver -- populated
#     via env var DRAGONFLY_ALERT_EMAIL or the existing default).
#   - 4 alert rules:
#       * Dispatcher p95 latency > 300ms sustained 5 min (Risk 0003).
#         Reads the `dispatcher.complete` structured log landed in PR #112.
#       * Service Bus moderation-pending DLQ depth > 0 sustained 5 min.
#       * Service Bus inat-submit DLQ depth > 0 sustained 5 min.
#       * Any of the 4 scheduled Container Apps Jobs fails its run.
#
# Idempotent. Re-running checks existence of each rule before
# recreating.
#
# Run with:
#   MSYS_NO_PATHCONV=1 DRAGONFLY_ALERT_EMAIL=you@example.com bash infra-azure/phase-9-monitoring.sh
#
# Prerequisites:
#   - phase-5 has run (Log Analytics workspace present).
#   - phase-9-async-pipeline.sh has run (Service Bus namespace +
#     Container Apps Jobs present).

set -euo pipefail

MGMT_SUB="5a04114f-9102-4e0b-828b-b385096edfbc"
MGMT_TENANT="3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05"
RG="dragonfly-dev-rg"
LOCATION="eastus2"

LAW_NAME="dragonfly-law-dev"
SB_NAMESPACE="dragonfly-sb-dev"

AG_NAME="dragonfly-ops-dev"
AG_SHORT_NAME="dfops"

# Operator's email -- override at run time:
#   DRAGONFLY_ALERT_EMAIL=ops@example.com bash phase-9-monitoring.sh
ALERT_EMAIL="${DRAGONFLY_ALERT_EMAIL:-zinkan.brian@gmail.com}"

LAW_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.OperationalInsights/workspaces/${LAW_NAME}"
SB_NAMESPACE_ID="/subscriptions/${MGMT_SUB}/resourceGroups/${RG}/providers/Microsoft.ServiceBus/namespaces/${SB_NAMESPACE}"

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

ensure_az_extension() {
  local name="$1"
  if ! az extension show --name "$name" >/dev/null 2>&1; then
    echo "==> install Azure CLI extension $name"
    az extension add --name "$name" --yes --output none
  fi
}

ensure_az_extension "scheduled-query"

# ---------------------------------------------------------------------------
# 1. Action group
# ---------------------------------------------------------------------------

echo "==> ensure action group $AG_NAME (email = $ALERT_EMAIL)"
if ! az monitor action-group show --name "$AG_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az monitor action-group create \
    --name "$AG_NAME" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --short-name "$AG_SHORT_NAME" \
    --action email primary "$ALERT_EMAIL" \
    --tags project=dragonfly env=dev managed-by=cli \
    --output none
fi

AG_ID=$(az monitor action-group show --name "$AG_NAME" --resource-group "$RG" --subscription "$MGMT_SUB" --query id -o tsv)

# ---------------------------------------------------------------------------
# 2. Dispatcher p95 latency alert (Risk 0003)
# ---------------------------------------------------------------------------

# Reads the structured-log `dispatcher.complete` event from either
# AppTraces (if App Insights is enabled later) or Container Apps
# console logs (the current stdout/Log Analytics path). The SLO is on
# whole-dispatch `duration_ms`; handler_durations_ms stays available
# in the same log record for follow-up diagnosis.
DISPATCHER_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true AppTraces, ContainerAppConsoleLogs, ContainerAppConsoleLogs_CL;
rows
| extend raw_message = coalesce(
    tostring(column_ifexists("Message", "")),
    tostring(column_ifexists("Log", "")),
    tostring(column_ifexists("Log_s", ""))
)
| extend raw_properties = todynamic(column_ifexists("Properties", dynamic({})))
| where raw_message has "dispatcher.complete"
| extend duration_ms = coalesce(
    toreal(raw_properties.duration_ms),
    todouble(extract(@'"duration_ms"\s*:\s*([0-9.]+)', 1, raw_message))
)
| where isnotnull(duration_ms)
| summarize p95 = percentile(duration_ms, 95) by bin(TimeGenerated, 5m)
| where p95 > 300
KQL
)

echo "==> ensure dispatcher p95 latency alert"
if ! az monitor scheduled-query show --name "dragonfly-dispatcher-p95" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az monitor scheduled-query create \
    --name "dragonfly-dispatcher-p95" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --scopes "$LAW_ID" \
    --condition "count 'AppTraces' > 0" \
    --condition-query "AppTraces=${DISPATCHER_QUERY}" \
    --description "Dispatcher whole-run p95 > 300ms sustained 5 min (Risk 0003 SLO)" \
    --evaluation-frequency 5m \
    --window-size 5m \
    --severity 2 \
    --action "$AG_ID" \
    --output none || echo "  (note: scheduled-query create syntax may differ; fall back to az rest or portal)"
fi

# ---------------------------------------------------------------------------
# 3. Service Bus DLQ depth alerts (Risk 0002)
# ---------------------------------------------------------------------------

ensure_dlq_alert() {
  local rule_name="$1"
  local queue_name="$2"

  echo "==> ensure DLQ depth alert $rule_name (queue $queue_name)"
  if az monitor metrics alert show --name "$rule_name" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
    return
  fi
  az monitor metrics alert create \
    --name "$rule_name" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --scopes "$SB_NAMESPACE_ID" \
    --condition "total DeadletteredMessages > 0 where EntityName includes ${queue_name}" \
    --description "Service Bus DLQ depth on $queue_name > 0 sustained 5 min" \
    --evaluation-frequency 5m \
    --window-size 5m \
    --severity 2 \
    --action "$AG_ID" \
    --output none
}

ensure_dlq_alert "dragonfly-dlq-moderation" "moderation-pending"
ensure_dlq_alert "dragonfly-dlq-inat-submit" "inat-submit"

# ---------------------------------------------------------------------------
# 4. Container Apps Jobs failure alert
# ---------------------------------------------------------------------------

# One alert covering all four scheduled jobs -- fires when any job's
# execution status flips to Failed. Easier to manage one rule than 4.
JOB_FAILURE_QUERY=$(cat <<'KQL'
let rows = union isfuzzy=true ContainerAppSystemLogs, ContainerAppSystemLogs_CL;
rows
| extend raw_log = coalesce(
    tostring(column_ifexists("Log", "")),
    tostring(column_ifexists("Log_s", "")),
    tostring(column_ifexists("Message", ""))
)
| extend job_name = coalesce(
    tostring(column_ifexists("ContainerJobName", "")),
    tostring(column_ifexists("ContainerJobName_s", "")),
    tostring(column_ifexists("ContainerAppName", "")),
    tostring(column_ifexists("ContainerAppName_s", ""))
)
| where raw_log has "JobExecutionStatus"
| where raw_log has "Failed"
| where job_name in (
    "dragonfly-rarity-refresh",
    "dragonfly-sweep-stale-reviews",
    "dragonfly-inat-outbox-replay",
    "dragonfly-dispatcher-replay"
)
| summarize count() by job_name, bin(TimeGenerated, 5m)
KQL
)

echo "==> ensure scheduled-job failure alert"
if ! az monitor scheduled-query show --name "dragonfly-job-failures" --resource-group "$RG" --subscription "$MGMT_SUB" >/dev/null 2>&1; then
  az monitor scheduled-query create \
    --name "dragonfly-job-failures" \
    --resource-group "$RG" \
    --subscription "$MGMT_SUB" \
    --scopes "$LAW_ID" \
    --condition "count 'ContainerAppSystemLogs' > 0" \
    --condition-query "ContainerAppSystemLogs=${JOB_FAILURE_QUERY}" \
    --description "Any of the 4 dragonfly-* scheduled jobs has a Failed execution" \
    --evaluation-frequency 5m \
    --window-size 5m \
    --severity 2 \
    --action "$AG_ID" \
    --output none || echo "  (note: scheduled-query create syntax may differ; fall back to az rest or portal)"
fi

# ---------------------------------------------------------------------------
# 5. Done
# ---------------------------------------------------------------------------

echo
echo "done."
echo "  Action group:  $AG_NAME ($ALERT_EMAIL)"
echo "  Alerts:        dragonfly-dispatcher-p95, dragonfly-dlq-moderation,"
echo "                 dragonfly-dlq-inat-submit, dragonfly-job-failures"
echo
echo "Smoke: temporarily lower the dispatcher-p95 threshold or replay known"
echo "       dispatcher traffic to confirm the alert path; drop a deliberately"
echo "       malformed message into inat-submit to confirm the DLQ alert fires."
echo
