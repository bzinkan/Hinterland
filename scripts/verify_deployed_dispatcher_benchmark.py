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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_AZURE_VALUE = re.compile(r"^[A-Za-z0-9._:@/+-]{1,512}$")
_HANDLERS = ("dex", "rarity", "world", "expedition")
_GITHUB_OIDC_AUDIENCE = "api://AzureADTokenExchange"
_LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"


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
        or not all(
            isinstance(value, str) and _ULID.fullmatch(value)
            for value in observation_ids
        )
        or len(set(observation_ids)) != len(observation_ids)
    ):
        raise ValueError("benchmark seed has invalid or duplicate observation IDs")
    if expected_count != len(observation_ids):
        raise ValueError(
            "benchmark seed sample count does not match its observation IDs"
        )
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


def _required_environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"GitHub OIDC environment omitted {name}")
    return value


def _github_oidc_assertion() -> str:
    request_url = _required_environment_value("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = _required_environment_value("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    parsed = urllib.parse.urlsplit(request_url)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not hostname.endswith(".actions.githubusercontent.com")
    ):
        raise RuntimeError("GitHub OIDC request URL is not trusted")
    query_items = [
        (name, value)
        for name, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if name != "audience"
    ]
    query_items.append(("audience", _GITHUB_OIDC_AUDIENCE))
    oidc_url = urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query_items),
            parsed.fragment,
        )
    )
    request = urllib.request.Request(
        oidc_url,
        headers={"Authorization": f"Bearer {request_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub OIDC request failed: status={exc.code}") from None
    except (urllib.error.URLError, json.JSONDecodeError):
        raise RuntimeError("GitHub OIDC request failed") from None
    assertion = payload.get("value") if isinstance(payload, dict) else None
    if (
        not isinstance(assertion, str)
        or assertion.count(".") != 2
        or len(assertion) > 32768
        or any(character.isspace() for character in assertion)
    ):
        raise RuntimeError("GitHub OIDC response omitted a valid assertion")
    return assertion


def _github_actions_log_analytics_token(*, minimum_validity_seconds: int) -> str:
    """Exchange the protected environment's GitHub assertion in memory."""
    client_id = _required_environment_value("AZURE_CLIENT_ID")
    tenant_id = _required_environment_value("AZURE_TENANT_ID")
    if not _UUID.fullmatch(client_id) or not _UUID.fullmatch(tenant_id):
        raise RuntimeError("GitHub OIDC Azure client or tenant ID is invalid")

    # Imported lazily so local operators can keep using the standalone Azure
    # CLI path without installing the backend Python environment.
    from azure.core.exceptions import AzureError
    from azure.identity import ClientAssertionCredential

    credential = ClientAssertionCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        func=_github_oidc_assertion,
    )
    try:
        access_token = credential.get_token(_LOG_ANALYTICS_SCOPE)
    except AzureError:
        raise RuntimeError("Log Analytics OIDC token exchange failed") from None
    finally:
        credential.close()
    token = access_token.token
    if (
        not isinstance(token, str)
        or not token
        or any(character.isspace() for character in token)
        or access_token.expires_on <= time.time() + minimum_validity_seconds
    ):
        raise RuntimeError("Log Analytics OIDC token is invalid or expires too soon")
    return token


def _azure_cli_invocation(
    az_cli: str,
    arguments: list[str],
    *,
    windows: bool,
    comspec: str | None,
) -> tuple[str | list[str], bool, str | None]:
    if not windows:
        return [az_cli, *arguments], False, None
    return (
        subprocess.list2cmdline([az_cli, *arguments]),
        True,
        comspec or "cmd.exe",
    )


def _query_rows(
    *,
    workspace_id: str,
    revision: str,
    image: str,
    observation_ids: list[str],
    started_at: datetime,
    finished_at: datetime,
    bearer_token: str | None = None,
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
    if bearer_token is not None:
        if not bearer_token or any(character.isspace() for character in bearer_token):
            raise RuntimeError("Log Analytics bearer token is invalid")
        request = urllib.request.Request(
            f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/query",
            data=json.dumps({"query": query}, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            error_code = "unknown"
            try:
                error_payload = json.loads(
                    exc.read(4096).decode("utf-8", errors="replace")
                )
                candidate = error_payload.get("error", {}).get("code")
                if isinstance(candidate, str) and re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,128}", candidate
                ):
                    error_code = candidate
            except (AttributeError, json.JSONDecodeError):
                pass
            raise RuntimeError(
                f"Log Analytics HTTP query failed: status={exc.code} code={error_code}"
            ) from None
        except (urllib.error.URLError, json.JSONDecodeError):
            raise RuntimeError("Log Analytics HTTP query failed") from None
        return _rows_from_http_response(value)

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
    # Azure CLI is installed as az.cmd on Windows; CreateProcess cannot
    # execute a command script directly. Let cmd.exe launch it, while
    # list2cmdline keeps the KQL (including its pipes) one quoted argument.
    command, run_with_shell, shell_executable = _azure_cli_invocation(
        az_cli,
        az_arguments,
        windows=os.name == "nt",
        comspec=os.environ.get("COMSPEC"),
    )
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
        message = completed.stderr.strip().splitlines()[-1:] or [
            "unknown Azure CLI error"
        ]
        raise RuntimeError(f"Log Analytics query failed: {message[0][:300]}")
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise RuntimeError("Log Analytics returned an unexpected result shape")
    return value


def _rows_from_http_response(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise RuntimeError("Log Analytics HTTP API returned an unexpected result shape")
    tables = value.get("tables")
    if (
        not isinstance(tables, list)
        or len(tables) != 1
        or not isinstance(tables[0], dict)
    ):
        raise RuntimeError("Log Analytics HTTP API returned an unexpected table set")
    columns = tables[0].get("columns")
    raw_rows = tables[0].get("rows")
    if not isinstance(columns, list) or not isinstance(raw_rows, list):
        raise RuntimeError("Log Analytics HTTP API omitted columns or rows")
    names: list[str] = []
    for column in columns:
        name = column.get("name") if isinstance(column, dict) else None
        if not isinstance(name, str) or not name:
            raise RuntimeError("Log Analytics HTTP API returned invalid columns")
        names.append(name)
    if not names:
        raise RuntimeError("Log Analytics HTTP API returned invalid columns")
    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list) or len(raw_row) != len(names):
            raise RuntimeError("Log Analytics HTTP API returned an invalid row")
        rows.append(dict(zip(names, raw_row, strict=True)))
    return rows


def _resolve_bearer_token(*, log_token_stdin: bool, timeout_seconds: int) -> str | None:
    running_in_github = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if running_in_github and log_token_stdin:
        raise RuntimeError(
            "GitHub Actions must use environment-scoped OIDC, not token stdin"
        )
    if log_token_stdin:
        bearer_token = sys.stdin.read().strip()
        if not bearer_token:
            raise RuntimeError("Log Analytics bearer token stdin was empty")
        return bearer_token
    if running_in_github:
        return _github_actions_log_analytics_token(
            minimum_validity_seconds=timeout_seconds + 120
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-evidence", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--threshold-ms", type=float, default=300.0)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--log-token-stdin", action="store_true")
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
    bearer_token = _resolve_bearer_token(
        log_token_stdin=args.log_token_stdin,
        timeout_seconds=args.timeout_seconds,
    )

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
            bearer_token=bearer_token,
        )
        observed_ids = {
            row.get("observation_id")
            for row in rows
            if isinstance(row.get("observation_id"), str)
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
