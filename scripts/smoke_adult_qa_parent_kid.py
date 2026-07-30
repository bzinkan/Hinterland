#!/usr/bin/env python3
"""Authenticated Adult QA parent/kid and clean-Observation canary."""

from __future__ import annotations

import os
import sys

from smoke_azure_parent_kid import run_parent_kid_smoke
from smoke_observation_adult_qa import run_adult_qa_canary

_QA_ORIGIN = "https://qa-api.thehinterlandguide.app"
_QA_POLICY = "2026-07-30-ADULT-QA-EGRESS-V1"


def main() -> int:
    try:
        run_parent_kid_smoke(
            base_url=os.environ.get("HINTERLAND_API_BASE_URL", _QA_ORIGIN),
            parent_bearer=os.environ.get("HINTERLAND_QA_SMOKE_ENTRA_BEARER", ""),
            parent_name="Adult QA Smoke Parent",
            kid_name="Adult QA Synthetic Explorer",
            evidence_path=os.environ.get("HINTERLAND_SMOKE_EVIDENCE_PATH") or None,
            dispatcher_benchmark_samples=0,
            consent_policy_version=_QA_POLICY,
            observation_runner=run_adult_qa_canary,
            expected_release_lane="adult_qa",
        )
    except Exception as exc:
        print(f"Authenticated Adult QA smoke failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
