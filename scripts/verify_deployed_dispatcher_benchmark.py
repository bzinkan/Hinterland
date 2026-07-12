#!/usr/bin/env python3
"""Fail closed on the exact deployed dispatcher's representative p95.

The authenticated smoke seeds test-owned observations and writes only bounded
operational identifiers. This verifier polls Log Analytics for those exact
observations, exact Container Apps revision, and exact immutable image, then
publishes aggregate evidence without copying child text, images, locations, or
individual observation IDs into the final promotion summary.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_SAFE_AZURE_VALUE = re.compile(r"^[A-Za-z0-9._:@/+-]{1,512}$")
_HANDLERS = ("dex", "rarity", "world", "expedition")


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def _parse_handler_durations(value: object) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    parsed: dict[str, float] = {}
    for handler in _HANDLERS:
        duration = value.get(handler)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            parsed[handler] = float(duration)
    return parsed


def _parse_duration(value: object) -> float | None:
    """Normalize Log Analytics numbers, which Azure CLI emits as strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def evaluate_rows(
    *,
    seed: dict[str, Any],
    rows: list[dict[str, Any]],
    expected_revision: str,
    expected_image: str,
    threshold_ms: float,
) -> dict[str, Any]:
    observation_ids = seed.get("observation_ids")
    expected_count = seed.get("sample_count")
    if (
        not isinstance(observation_ids, list)
        or not observation_ids
        or not all(isinstance(value, str) and _ULID.fullmatch(value) for value in observation_ids)
        or len(set(observation_ids)) != len(observation_ids)
    ):
        raise ValueError("benchmark seed has invalid or duplicate observation IDs")
    if expected_count != len(observation_ids):
        raise ValueError("benchmark seed sample count does not match its observation IDs")
    scenario_counts = seed.get("scenario_counts")
    if (
        seed.get("result") != "seeded"
        or not isinstance(scenario_counts, dict)
        or any(
            not isinstance(name, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for name, count in scenario_counts.items()
        )
        or sum(scenario_counts.values()) != expected_count
    ):
        raise ValueError("benchmark seed scenario counts are invalid")

    expected = set(observation_ids)
    by_observation: dict[str, dict[str, Any]] = {}
    duplicate_event_count = 0
    unexpected_event_count = 0
    for row in rows:
        observation_id = row.get("observation_id")
        if not isinstance(observation_id, str) or observation_id not in expected:
            unexpected_event_count += 1
            continue
        if observation_id in by_observation:
            duplicate_event_count += 1
            continue
        by_observation[observation_id] = row

    durations: list[float] = []
    incomplete_count = 0
    malformed_count = 0
    scope_mismatch_count = 0
    handler_values: dict[str, list[float]] = {handler: [] for handler in _HANDLERS}
    for row in by_observation.values():
        if (
            row.get("revision") != expected_revision
            or row.get("image") != expected_image
            or row.get("method") != "POST"
            or row.get("path") != "/v1/observations"
        ):
            scope_mismatch_count += 1
        duration = _parse_duration(row.get("duration_ms"))
        if duration is None:
            malformed_count += 1
            continue
        durations.append(duration)
        if row.get("dispatch_status") != "complete":
            incomplete_count += 1
        handler_durations = _parse_handler_durations(row.get("handler_durations_ms"))
        for handler, value in handler_durations.items():
            handler_values[handler].append(value)

    missing_count = len(expected) - len(by_observation)
    p50_ms = _nearest_rank(durations, 0.50) if durations else None
    p95_ms = _nearest_rank(durations, 0.95) if durations else None
    max_ms = round(max(durations), 2) if durations else None
    threshold_exceed_count = sum(value >= threshold_ms for value in durations)
    failures: list[str] = []
    if missing_count:
        failures.append("missing_dispatch_events")
    if duplicate_event_count:
        failures.append("duplicate_dispatch_events")
    if unexpected_event_count:
        failures.append("unexpected_dispatch_events")
    if malformed_count:
        failures.append("malformed_dispatch_events")
    if scope_mismatch_count:
        failures.append("scope_mismatch")
    if incomplete_count:
        failures.append("incomplete_dispatches")
    if p95_ms is None or p95_ms >= threshold_ms:
        failures.append("p95_budget_exceeded")

    handler_stats: dict[str, dict[str, float | int | None]] = {}
    for handler, values in handler_values.items():
        handler_stats[handler] = {
            "samples": len(values),
            "p50_ms": _nearest_rank(values, 0.50) if values else None,
            "p95_ms": _nearest_rank(values, 0.95) if values else None,
        }

    return {
        "result": "passed" if not failures else "failed",
        "revision": expected_revision,
        "image": expected_image,
        "window_started_at": seed.get("started_at"),
        "window_finished_at": seed.get("finished_at"),
        "scenario_counts": seed.get("scenario_counts"),
        "expected_samples": len(expected),
        "observed_samples": len(durations),
        "missing_count": missing_count,
        "duplicate_event_count": duplicate_event_count,
        "unexpected_event_count": unexpected_event_count,
        "malformed_count": malformed_count,
        "scope_mismatch_count": scope_mismatch_count,
        "incomplete_count": incomplete_count,
        "threshold_ms": threshold_ms,
        "threshold_exceed_count": threshold_exceed_count,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "max_ms": max_ms,
        "handler_stats": handler_stats,
        "failures": failures,
    }


def _parse_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"benchmark seed omitted {field}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"benchmark seed {field} is not timezone-aware")
    return parsed.astimezone(UTC)


def _kql_string(value: str, *, field: str) -> str:
    if not _SAFE_AZURE_VALUE.fullmatch(value) or "'" in value:
        raise ValueError(f"unsafe {field}")
    return value


def _query_rows(
    *,
    workspace_id: str,
    revision: str,
    image: str,
    observation_ids: list[str],
    started_at: datetime,
    finished_at: datetime,
) -> list[dict[str, Any]]:
    ids_json = json.dumps(observation_ids, separators=(",", ":"))
    start = (started_at - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    end = (finished_at + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
    query = f"""
let benchmark_ids = dynamic({ids_json});
ContainerAppConsoleLogs_CL
| where TimeGenerated between(datetime({start}) .. datetime({end}))
| where ContainerAppName_s == '{revision.split("--", 1)[0]}'
| where RevisionName_s == '{revision}'
| where ContainerImage_s == '{image}'
| extend j=parse_json(Log_s)
| where tostring(j.event) == 'dispatcher.complete'
| where tostring(j.method) == 'POST' and tostring(j.path) == '/v1/observations'
| extend observation_id=tostring(j.observation_id)
| where set_has_element(benchmark_ids, observation_id)
| project observation_id, revision=RevisionName_s, image=ContainerImage_s,
          method=tostring(j.method), path=tostring(j.path),
          duration_ms=todouble(j.duration_ms),
          dispatch_status=tostring(j.dispatch_status),
          handler_durations_ms=j.handler_durations_ms
""".strip()
    az_cli = shutil.which("az") or shutil.which("az.cmd")
    if az_cli is None:
        raise RuntimeError("Azure CLI is not available")
    if os.name == "nt":
        # cmd.exe treats embedded newlines as command boundaries even when
        # list2cmdline quotes the KQL argument. Flatten formatting whitespace;
        # Kusto uses pipes/semicolons, not newlines, for this query's grammar.
        query = " ".join(line.strip() for line in query.splitlines())
    az_arguments = [
        "monitor",
        "log-analytics",
        "query",
        "--workspace",
        workspace_id,
        "--analytics-query",
        query,
        "--output",
        "json",
    ]
    command: str | list[str] = [az_cli, *az_arguments]
    run_with_shell = False
    shell_executable: str | None = None
    if os.name == "nt":
        # Azure CLI is installed as az.cmd on Windows; CreateProcess cannot
        # execute a command script directly. Let cmd.exe launch it, while
        # list2cmdline keeps the KQL (including its pipes) one quoted argument.
        command = subprocess.list2cmdline([az_cli, *az_arguments])
        run_with_shell = True
        shell_executable = os.environ.get("COMSPEC", "cmd.exe")
    completed = subprocess.run(
        command,
        shell=run_with_shell,
        executable=shell_executable,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()[-1:] or ["unknown Azure CLI error"]
        raise RuntimeError(f"Log Analytics query failed: {message[0][:300]}")
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError("Log Analytics returned an unexpected result shape")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-evidence", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--threshold-ms", type=float, default=300.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.seed_evidence.read_text(encoding="utf-8"))
    seed = payload.get("dispatcher_benchmark") if isinstance(payload, dict) else None
    if not isinstance(seed, dict):
        raise SystemExit("authenticated smoke evidence omitted dispatcher_benchmark")
    observation_ids = seed.get("observation_ids")
    if not isinstance(observation_ids, list):
        raise SystemExit("dispatcher benchmark omitted observation IDs")
    revision = _kql_string(args.expected_revision, field="revision")
    image = _kql_string(args.expected_image, field="image")
    workspace_id = _kql_string(args.workspace_id, field="workspace id")
    started_at = _parse_datetime(seed.get("started_at"), field="started_at")
    finished_at = _parse_datetime(seed.get("finished_at"), field="finished_at")

    deadline = time.monotonic() + args.timeout_seconds
    rows: list[dict[str, Any]] = []
    while True:
        rows = _query_rows(
            workspace_id=workspace_id,
            revision=revision,
            image=image,
            observation_ids=observation_ids,
            started_at=started_at,
            finished_at=finished_at,
        )
        observed_ids = {
            row.get("observation_id") for row in rows if isinstance(row.get("observation_id"), str)
        }
        if len(observed_ids) >= len(observation_ids) or time.monotonic() >= deadline:
            break
        time.sleep(15)

    evidence = evaluate_rows(
        seed=seed,
        rows=rows,
        expected_revision=revision,
        expected_image=image,
        threshold_ms=args.threshold_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "dispatcher benchmark "
        f"samples={evidence['observed_samples']} p50_ms={evidence['p50_ms']} "
        f"p95_ms={evidence['p95_ms']} max_ms={evidence['max_ms']} "
        f"result={evidence['result']}"
    )
    return 0 if evidence["result"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
