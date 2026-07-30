#!/usr/bin/env bash
# Fail-closed cleanup for a completed or abandoned Adult QA CV qualification
# lease. This artifact is intentionally hard-bound to QA and can also be run by
# an operator/scheduler with --expired-only to sweep an expired lease.
set -uo pipefail

resource_group=hinterland-qa-rg
container_app=hinterland-api-qa
key_vault=hinterland-kv-qa
api_origin=https://qa-api.thehinterlandguide.app
expected_sha=
expected_digest=
qualification_secret_name=
expired_only=false

while (($#)); do
  case "$1" in
    --expected-sha) expected_sha="${2:?}"; shift 2 ;;
    --expected-digest) expected_digest="${2:?}"; shift 2 ;;
    --qualification-secret-name) qualification_secret_name="${2:?}"; shift 2 ;;
    --expired-only) expired_only=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "${resource_group}" != hinterland-qa-rg ||
      "${container_app}" != hinterland-api-qa ||
      "${key_vault}" != hinterland-kv-qa ]]; then
  echo "Adult QA cleanup target contract was changed." >&2
  exit 2
fi

read_app_json() {
  local output
  for _ in $(seq 1 6); do
    if output="$(az containerapp show --name "${container_app}" \
        --resource-group "${resource_group}" --output json 2>/dev/null)" &&
       jq -e '
         type == "object" and
         (.name == "hinterland-api-qa") and
         (.properties.template.containers | type == "array")
       ' <<<"${output}" >/dev/null 2>&1; then
      printf '%s' "${output}"
      return 0
    fi
    sleep 5
  done
  echo "Cannot read a valid Adult QA Container App document." >&2
  return 1
}

read_app_secrets() {
  local output
  for _ in $(seq 1 6); do
    if output="$(az containerapp secret list --name "${container_app}" \
        --resource-group "${resource_group}" --output json 2>/dev/null)" &&
       jq -e 'type == "array"' <<<"${output}" >/dev/null 2>&1; then
      printf '%s' "${output}"
      return 0
    fi
    sleep 5
  done
  echo "Cannot read the Adult QA Container App secret inventory." >&2
  return 1
}

read_key_vault_secrets() {
  local output
  for _ in $(seq 1 6); do
    if output="$(az keyvault secret list --vault-name "${key_vault}" \
        --output json 2>/dev/null)" &&
       jq -e 'type == "array"' <<<"${output}" >/dev/null 2>&1; then
      printf '%s' "${output}"
      return 0
    fi
    sleep 5
  done
  echo "Cannot read the Adult QA Key Vault secret inventory." >&2
  return 1
}

key_vault_secret_is_retired() {
  local inventory="$1"
  local name="$2"
  jq -e --arg name "${name}" '
    ([.[] |
      select(((.id // "") | split("/")[-1]) == $name)] | length == 0) or
    ([.[] |
      select(((.id // "") | split("/")[-1]) == $name)] |
      all(.[]; (.attributes.enabled // true) == false))
  ' <<<"${inventory}" >/dev/null
}

read_meta() {
  local output
  for _ in $(seq 1 6); do
    if output="$(curl --fail --silent --show-error \
        "${api_origin}/v1/meta" 2>/dev/null)" &&
       jq -e 'type == "object"' <<<"${output}" >/dev/null 2>&1; then
      printf '%s' "${output}"
      return 0
    fi
    sleep 5
  done
  echo "Cannot read valid Adult QA capability metadata." >&2
  return 1
}

app_json="$(read_app_json)" || exit 1
env_value() {
  local name="$1"
  jq -r --arg name "${name}" '
    [.properties.template.containers[0].env[]? |
      select(.name == $name) | .value][0] // empty
  ' <<<"${app_json}"
}

qualification_enabled="$(env_value HINTERLAND_INAT_CV_QUALIFICATION_ENABLED)"
lease_expiry="$(env_value HINTERLAND_INAT_CV_QUALIFICATION_EXPIRES_AT)"
qualification_run_id="$(env_value HINTERLAND_INAT_CV_QUALIFICATION_RUN_ID)"
app_secrets="$(read_app_secrets)" || exit 1
env_names="$(jq -r \
  '.properties.template.containers[0].env[]?.name' <<<"${app_json}")"
qualification_residue=false
if [[ "${qualification_enabled}" = true ]] ||
   grep -Eq '^HINTERLAND_INAT_CV_QUALIFICATION_(KEY|RUN_ID|MANIFEST_SHA256|EXPIRES_AT|CASES)$' \
     <<<"${env_names}" ||
   jq -e 'any(.[]; .name == "inat-cv-qualification-key")' \
     <<<"${app_secrets}" >/dev/null; then
  qualification_residue=true
fi
if [[ "${expired_only}" = true && "${qualification_residue}" != true ]]; then
  # A normal promoted CV token is intentionally left alone when there is no
  # qualification residue.
  exit 0
fi
if [[ "${expired_only}" = true && "${qualification_enabled}" = true &&
      -n "${lease_expiry}" ]]; then
  expiry_epoch="$(date -u -d "${lease_expiry}" +%s 2>/dev/null || echo 0)"
  if (( expiry_epoch > $(date -u +%s) )); then
    exit 0
  fi
fi

if [[ -z "${qualification_secret_name}" ]]; then
  qualification_secret_name="$(jq -r '
    [.[] | select(.name == "inat-cv-qualification-key") |
      (.keyVaultUrl // "" | split("/")[-1])][0] // empty
  ' <<<"${app_secrets}")"
fi
if [[ -z "${qualification_secret_name}" &&
      "${qualification_run_id}" =~ ^[0-9A-HJKMNP-TV-Z]{26}$ ]]; then
  qualification_secret_name="inat-cv-qualification-${qualification_run_id,,}"
fi

# Every mutation is attempted independently. Individual failures are warnings;
# the final live-state checks below are the release authority.
az containerapp update --name "${container_app}" \
  --resource-group "${resource_group}" \
  --set-env-vars \
    HINTERLAND_INAT_CV_ENABLED=false \
    HINTERLAND_INAT_CV_DISCLOSURE_APPROVED=false \
    HINTERLAND_INAT_CV_BENCHMARK_APPROVED=false \
    HINTERLAND_INAT_CV_QUALIFICATION_ENABLED=false \
    HINTERLAND_INAT_SUBMIT_ENABLED=false \
  --remove-env-vars \
    HINTERLAND_INAT_CV_TOKEN \
    HINTERLAND_INAT_CV_QUALIFICATION_KEY \
    HINTERLAND_INAT_CV_QUALIFICATION_RUN_ID \
    HINTERLAND_INAT_CV_QUALIFICATION_MANIFEST_SHA256 \
    HINTERLAND_INAT_CV_QUALIFICATION_EXPIRES_AT \
    HINTERLAND_INAT_CV_QUALIFICATION_CASES \
  --output none ||
  echo "Warning: qualification environment cleanup command failed." >&2

clean_revision_ready=false
for _ in $(seq 1 18); do
  if app_json="$(read_app_json)"; then
    latest_revision="$(jq -r '.properties.latestRevisionName // empty' \
      <<<"${app_json}")"
    ready_revision="$(jq -r '.properties.latestReadyRevisionName // empty' \
      <<<"${app_json}")"
    if [[ -n "${latest_revision}" &&
          "${latest_revision}" = "${ready_revision}" ]] &&
       jq -e '
         ([.properties.template.containers[0].env[]? |
           select(.name == "HINTERLAND_INAT_CV_QUALIFICATION_ENABLED") |
           .value][0] // "false") == "false" and
         ([.properties.template.containers[0].env[]?.name] |
           all(.[];
             . != "HINTERLAND_INAT_CV_TOKEN" and
             . != "HINTERLAND_INAT_CV_QUALIFICATION_KEY" and
             . != "HINTERLAND_INAT_CV_QUALIFICATION_RUN_ID" and
             . != "HINTERLAND_INAT_CV_QUALIFICATION_MANIFEST_SHA256" and
             . != "HINTERLAND_INAT_CV_QUALIFICATION_EXPIRES_AT" and
             . != "HINTERLAND_INAT_CV_QUALIFICATION_CASES"))
       ' <<<"${app_json}" >/dev/null; then
      clean_revision_ready=true
      break
    fi
  fi
  sleep 5
done

if [[ "${clean_revision_ready}" = true ]]; then
  for secret in inat-cv-token inat-cv-qualification-key; do
    runtime_secret_removed=false
    for _ in $(seq 1 6); do
      if app_secrets="$(read_app_secrets)" &&
         ! jq -e --arg name "${secret}" \
           'any(.[]; .name == $name)' <<<"${app_secrets}" >/dev/null; then
        runtime_secret_removed=true
        break
      fi
      az containerapp secret remove --name "${container_app}" \
        --resource-group "${resource_group}" \
        --secret-names "${secret}" --output none 2>/dev/null ||
        echo "Warning: retrying runtime secret removal for ${secret}." >&2
      sleep 5
    done
    if [[ "${runtime_secret_removed}" != true ]] &&
       app_secrets="$(read_app_secrets)" &&
       ! jq -e --arg name "${secret}" \
         'any(.[]; .name == $name)' <<<"${app_secrets}" >/dev/null; then
      runtime_secret_removed=true
    fi
    if [[ "${runtime_secret_removed}" != true ]]; then
      echo "Warning: runtime secret ${secret} remains mapped." >&2
    fi
  done
else
  echo "Warning: no clean ready revision; runtime secret removal was withheld." >&2
fi

key_vault_secret_retired=false
if [[ -n "${qualification_secret_name}" ]]; then
  for _ in $(seq 1 8); do
    if key_vault_secrets="$(read_key_vault_secrets)" &&
       key_vault_secret_is_retired "${key_vault_secrets}" \
         "${qualification_secret_name}"; then
      key_vault_secret_retired=true
      break
    fi
    az keyvault secret delete --vault-name "${key_vault}" \
      --name "${qualification_secret_name}" --output none 2>/dev/null ||
      az keyvault secret set-attributes --vault-name "${key_vault}" \
        --name "${qualification_secret_name}" --enabled false \
        --output none 2>/dev/null ||
      echo "Warning: retrying qualification Key Vault secret retirement." >&2
    sleep 5
  done
  if [[ "${key_vault_secret_retired}" != true ]] &&
     key_vault_secrets="$(read_key_vault_secrets)" &&
     key_vault_secret_is_retired "${key_vault_secrets}" \
       "${qualification_secret_name}"; then
    key_vault_secret_retired=true
  fi
else
  echo "Warning: the qualification Key Vault secret name could not be derived." >&2
fi

safe=false
for _ in $(seq 1 90); do
  if ! app_json="$(read_app_json)" ||
     ! app_secrets="$(read_app_secrets)" ||
     ! key_vault_secrets="$(read_key_vault_secrets)" ||
     ! meta="$(read_meta)"; then
    sleep 5
    continue
  fi
  env_json="$(jq '.properties.template.containers[0].env // []' <<<"${app_json}")"
  latest_revision="$(jq -r '.properties.latestRevisionName // empty' \
    <<<"${app_json}")"
  ready_revision="$(jq -r '.properties.latestReadyRevisionName // empty' \
    <<<"${app_json}")"
  if [[ "${clean_revision_ready}" = true &&
        -n "${latest_revision}" &&
        "${latest_revision}" = "${ready_revision}" &&
        -n "${qualification_secret_name}" ]] &&
    key_vault_secret_is_retired "${key_vault_secrets}" \
      "${qualification_secret_name}" &&
    jq -e '
      ([.[] | select(
        .name == "HINTERLAND_INAT_CV_QUALIFICATION_ENABLED"
      ) | .value][0] // "false") == "false" and
      ([.[].name] | all(.[];
        . != "HINTERLAND_INAT_CV_TOKEN" and
        . != "HINTERLAND_INAT_CV_QUALIFICATION_KEY" and
        . != "HINTERLAND_INAT_CV_QUALIFICATION_RUN_ID" and
        . != "HINTERLAND_INAT_CV_QUALIFICATION_MANIFEST_SHA256" and
        . != "HINTERLAND_INAT_CV_QUALIFICATION_EXPIRES_AT" and
        . != "HINTERLAND_INAT_CV_QUALIFICATION_CASES"))
    ' <<<"${env_json}" >/dev/null &&
    jq -e 'all(.[];
      .name != "inat-cv-token" and
      .name != "inat-cv-qualification-key")' <<<"${app_secrets}" >/dev/null &&
    jq -e '
      .release_lane == "adult_qa" and
      .capabilities.observation.photo_helper_enabled == false and
      .capabilities.integrations.cv_qualification_enabled == false and
      .capabilities.integrations.public_inat_submission_enabled == false
    ' <<<"${meta}" >/dev/null 2>&1; then
    if [[ -n "${expected_sha}" ]]; then
      test "$(jq -r '.version // empty' <<<"${meta}")" = "${expected_sha}" ||
        { sleep 5; continue; }
    fi
    if [[ -n "${expected_digest}" ]]; then
      test "$(jq -r '.image_digest // empty' <<<"${meta}")" = \
        "${expected_digest}" || { sleep 5; continue; }
    fi
    safe=true
    break
  fi
  sleep 5
done

if [[ "${safe}" != true ]]; then
  echo "Adult QA CV qualification cleanup did not reach a fail-closed posture." >&2
  exit 1
fi
