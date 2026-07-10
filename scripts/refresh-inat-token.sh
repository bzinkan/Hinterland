#!/usr/bin/env bash
# Push a fresh iNat API JWT into Key Vault and roll the Container App.
#
# Why this script exists
# ----------------------
# iNat's `/users/api_token` endpoint returns a JWT that expires after
# ~24 h. We can't auto-fetch it from a service principal because iNat
# requires either:
#   1. A browser session (cookie-based, not automatable cleanly), OR
#   2. A registered OAuth app for the password grant (2-month account
#      age + 10 improving-IDs requirement, gated by iNat staff review).
#
# Until the OAuth app comes through (~early August), this script is
# the daily "refresh" path. You paste the JWT once a day; the script
# handles the Key Vault write + Container App roll + sanity check.
# After OAuth app approval, this script is retired in favor of a real
# Container Apps Job that uses the password grant.
#
# Usage
# -----
# Three input modes -- pick whichever is cleanest:
#
#   1. Paste from stdin (default):
#        bash scripts/refresh-inat-token.sh
#      (paste, then Ctrl+D on Unix / Ctrl+Z+Enter on Windows Git Bash)
#
#   2. From a file:
#        bash scripts/refresh-inat-token.sh --file /tmp/token.txt
#
#   3. From the Windows clipboard (Git Bash):
#        bash scripts/refresh-inat-token.sh --clipboard
#
# All three modes accept either the raw JWT or the full JSON response
# from `https://www.inaturalist.org/users/api_token`
# (`{"api_token":"<jwt>"}`). The script strips the JSON envelope if
# present.
#
# What the script does
# --------------------
# 1. Parse + validate the token (3-part JWT shape; expiry > now).
# 2. Write it to Key Vault via a temp file so the token never appears
#    in shell history or the process list. Defaults are
#    `hinterland-kv-dev/inat-oauth-token`; set INAT_REFRESH_VAULT for
#    other environments.
# 3. Roll the Container App revision so the new secret value picks up.
#    Defaults are `hinterland-api` in `hinterland-dev-rg`; set
#    INAT_REFRESH_APP / INAT_REFRESH_RG for other environments.
#    (Revision-suffix forces a fresh deploy.)
# 4. Print the next-rotation timestamp so the operator knows when
#    they'll need to run this again.
#
# What the script does NOT do
# ---------------------------
# - Fetch the token from iNat. You have to do that part in a browser.
# - Enable CV. W1 and closed beta keep enable/disclosure/benchmark gates false.
# - Authorize pre-save egress. Any future use is post-clean only.

set -euo pipefail

# Defaults can be overridden by env vars (e.g. when promoting to a
# different RG/vault per environment).
VAULT_NAME="${INAT_REFRESH_VAULT:-hinterland-kv-dev}"
SECRET_NAME="${INAT_REFRESH_SECRET:-inat-oauth-token}"
APP_NAME="${INAT_REFRESH_APP:-hinterland-api}"
RG="${INAT_REFRESH_RG:-hinterland-dev-rg}"

usage() {
  cat >&2 <<EOF
Usage:
  $(basename "$0")                    # paste from stdin
  $(basename "$0") --file <path>      # read from file
  $(basename "$0") --clipboard        # read from Windows clipboard
EOF
  exit 2
}

MODE="stdin"
SOURCE_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file)
      [[ $# -ge 2 ]] || usage
      MODE="file"
      SOURCE_FILE="$2"
      shift 2
      ;;
    -c|--clipboard)
      MODE="clipboard"
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Read the token.
# ---------------------------------------------------------------------------

case "$MODE" in
  stdin)
    echo "Paste the iNat JWT (or the full {\"api_token\":\"...\"} JSON from" >&2
    echo "https://www.inaturalist.org/users/api_token), then press Ctrl+D" >&2
    echo "(Ctrl+Z + Enter on Windows Git Bash):" >&2
    TOKEN_RAW=$(cat)
    ;;
  file)
    if [[ ! -r "$SOURCE_FILE" ]]; then
      echo "FATAL: cannot read $SOURCE_FILE" >&2
      exit 1
    fi
    TOKEN_RAW=$(cat "$SOURCE_FILE")
    ;;
  clipboard)
    if command -v powershell.exe >/dev/null 2>&1; then
      TOKEN_RAW=$(powershell.exe -NoProfile -Command "Get-Clipboard")
    elif command -v pbpaste >/dev/null 2>&1; then
      TOKEN_RAW=$(pbpaste)
    elif command -v xclip >/dev/null 2>&1; then
      TOKEN_RAW=$(xclip -selection clipboard -o)
    else
      echo "FATAL: no clipboard helper available" >&2
      exit 1
    fi
    ;;
esac

# ---------------------------------------------------------------------------
# 2. Strip the optional JSON envelope + sanity-check the shape.
# ---------------------------------------------------------------------------

# `python3` is in PATH on every dev box we ship; using it for safe JSON
# + base64 parsing instead of brittle shell substitution.
TOKEN=$(python3 - "$TOKEN_RAW" <<'PY'
import json
import re
import sys

raw = sys.argv[1].strip()

try:
    obj = json.loads(raw)
    if isinstance(obj, dict) and isinstance(obj.get("api_token"), str):
        sys.stdout.write(obj["api_token"])
        sys.exit(0)
except json.JSONDecodeError:
    pass

# Already a JWT.
match = re.fullmatch(r"[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", raw)
if not match:
    print(
        "ERROR: input is not a JWT and not a {\"api_token\":\"...\"} object",
        file=sys.stderr,
    )
    sys.exit(1)
sys.stdout.write(raw)
PY
)

# Decode the payload and check expiry. Exits non-zero on past-expiry
# or malformed payload.
python3 - "$TOKEN" <<'PY'
import base64
import json
import sys
import time

token = sys.argv[1]
parts = token.split(".")
if len(parts) != 3:
    print("ERROR: token does not have 3 segments", file=sys.stderr)
    sys.exit(1)

# JWT payload is unpadded base64url.
payload_b64 = parts[1]
payload_b64 += "=" * (-len(payload_b64) % 4)
try:
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
except Exception as exc:
    print(f"ERROR: could not decode payload: {exc}", file=sys.stderr)
    sys.exit(1)

exp = payload.get("exp")
if not isinstance(exp, int):
    print("ERROR: payload has no `exp` claim", file=sys.stderr)
    sys.exit(1)

now = int(time.time())
if exp <= now:
    print(
        f"ERROR: token already expired at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(exp))}",
        file=sys.stderr,
    )
    sys.exit(1)

hours_left = (exp - now) / 3600
print(
    f"Token expires at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(exp))} "
    f"(in {hours_left:.1f} h)",
    file=sys.stderr,
)
PY

# ---------------------------------------------------------------------------
# 3. Write to Key Vault via temp file (don't put token on command line).
# ---------------------------------------------------------------------------

TMP=$(mktemp)
# shellcheck disable=SC2064  # we want the expansion at trap-set time
trap "rm -f '$TMP'" EXIT
printf '%s' "$TOKEN" > "$TMP"

echo "==> writing token to Key Vault $VAULT_NAME/$SECRET_NAME" >&2
az keyvault secret set \
  --vault-name "$VAULT_NAME" \
  --name "$SECRET_NAME" \
  --file "$TMP" \
  --output none

# ---------------------------------------------------------------------------
# 4. Roll the Container App so the new secret picks up.
# ---------------------------------------------------------------------------

# Container Apps doesn't reload secret references on the fly -- a fresh
# revision is the cleanest trigger. --revision-suffix is the lightest
# update we can make that still creates a new revision (vs --image which
# would re-pull the registry).
SUFFIX="rotate-$(date -u +%Y%m%d-%H%M%S)"
echo "==> rolling Container App $APP_NAME (suffix: $SUFFIX)" >&2
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --revision-suffix "$SUFFIX" \
  --output none

echo
echo "Done. The secret was rotated; this does NOT enable CV."
echo "Keep enable/disclosure/benchmark gates false outside an approved"
echo "post-clean staging benchmark. Public submission remains disabled."
echo
echo "If it doesn't, check the structured log:"
echo "  az containerapp logs show --name $APP_NAME --resource-group $RG \\"
echo "    --follow --tail 50 --type=console"
echo "and audit the bounded post-clean benchmark request IDs only."
