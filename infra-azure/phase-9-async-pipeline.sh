#!/usr/bin/env bash
# Compatibility entry point for the Phase 9 async pipeline.
#
# The former implementation provisioned direct BlobCreated/Event Grid
# moderation and iNaturalist consumers. ADR 0015 forbids both. Keeping that
# implementation runnable would make rollback reintroduce a child-photo egress
# path, so this entry point delegates to the contained W1 provisioner.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "phase-9-async-pipeline.sh now delegates to outbox-only Observation W1." >&2
echo "Set HINTERLAND_PHASE9_IMAGE to an immutable @sha256 digest." >&2
exec "${SCRIPT_DIR}/phase-9-observation-w1.sh" "$@"
