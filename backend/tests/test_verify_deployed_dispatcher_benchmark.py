from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure import identity as azure_identity
from ulid import ULID

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts/verify_deployed_dispatcher_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("verify_deployed_dispatcher_benchmark", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = verifier
_SPEC.loader.exec_module(verifier)


def _seed(count: int = 50) -> dict[str, object]:
    return {
        "result": "seeded",
        "started_at": "2026-07-12T12:00:00+00:00",
        "finished_at": "2026-07-12T12:05:00+00:00",
        "sample_count": count,
        "observation_ids": [str(ULID()) for _ in range(count)],
        "scenario_counts": {"unknown_no_location": count},
    }


def _rows(seed: dict[str, object], *, duration_ms: float = 150.0) -> list[dict[str, object]]:
    observation_ids = seed["observation_ids"]
    assert isinstance(observation_ids, list)
    return [
        {
            "observation_id": observation_id,
            "revision": "hinterland-api--0000045",
            "image": "hinterlandacrdev.azurecr.io/hinterland-api@sha256:" + "a" * 64,
            "method": "POST",
            "path": "/v1/observations",
            "duration_ms": duration_ms,
            "dispatch_status": "complete",
            "handler_durations_ms": {
                "dex": 1.0,
                "rarity": 2.0,
                "world": 3.0,
                "expedition": 4.0,
            },
        }
        for observation_id in observation_ids
    ]


def _evaluate(seed: dict[str, object], rows: list[dict[str, object]]) -> dict[str, object]:
    return verifier.evaluate_rows(
        seed=seed,
        rows=rows,
        expected_revision="hinterland-api--0000045",
        expected_image="hinterlandacrdev.azurecr.io/hinterland-api@sha256:" + "a" * 64,
        threshold_ms=300.0,
    )


def test_evaluate_rows_passes_exact_complete_sample_set() -> None:
    seed = _seed()
    evidence = _evaluate(seed, _rows(seed))

    assert evidence["result"] == "passed"
    assert evidence["observed_samples"] == 50
    assert evidence["p50_ms"] == 150.0
    assert evidence["p95_ms"] == 150.0
    assert evidence["handler_stats"]["expedition"] == {
        "samples": 50,
        "p50_ms": 4.0,
        "p95_ms": 4.0,
    }
    assert "observation_ids" not in evidence


def test_evaluate_rows_fails_closed_on_missing_duplicate_or_incomplete() -> None:
    seed = _seed(20)
    rows = _rows(seed)
    rows.pop()
    rows.append(dict(rows[0]))
    rows[0]["dispatch_status"] = "partial"

    evidence = _evaluate(seed, rows)

    assert evidence["result"] == "failed"
    assert set(evidence["failures"]) >= {
        "missing_dispatch_events",
        "duplicate_dispatch_events",
        "incomplete_dispatches",
    }


def test_evaluate_rows_uses_nearest_rank_and_fails_at_budget() -> None:
    seed = _seed(50)
    rows = _rows(seed, duration_ms=100.0)
    for row in rows[-3:]:
        row["duration_ms"] = 300.0

    evidence = _evaluate(seed, rows)

    assert evidence["p95_ms"] == 300.0
    assert evidence["threshold_exceed_count"] == 3
    assert "p95_budget_exceeded" in evidence["failures"]


def test_evaluate_rows_accepts_log_analytics_numeric_strings() -> None:
    seed = _seed(1)
    rows = _rows(seed)
    rows[0]["duration_ms"] = "664.72"
    rows[0]["handler_durations_ms"] = (
        '{"dex":55.92,"rarity":82.38,"world":82.72,"expedition":221.09}'
    )

    evidence = _evaluate(seed, rows)

    assert evidence["observed_samples"] == 1
    assert evidence["p95_ms"] == 664.72
    assert evidence["handler_stats"]["expedition"]["p95_ms"] == 221.09
    assert evidence["failures"] == ["p95_budget_exceeded"]


def test_evaluate_rows_rejects_invalid_seed_ids() -> None:
    seed = _seed(20)
    seed["observation_ids"] = ["not-an-ulid"] * 20

    with pytest.raises(ValueError, match="invalid or duplicate"):
        _evaluate(seed, [])


def test_query_rows_uses_direct_http_bearer_without_nested_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed(1)
    expected_rows = _rows(seed)
    columns = [{"name": name, "type": "string"} for name in expected_rows[0]]
    response_payload = {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": columns,
                "rows": [[row[name] for name in expected_rows[0]] for row in expected_rows],
            }
        ]
    }
    captured: dict[str, object] = {}

    def fake_urlopen(request: object, *, timeout: int) -> io.BytesIO:
        captured["request"] = request
        captured["timeout"] = timeout
        return io.BytesIO(json.dumps(response_payload).encode("utf-8"))

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        verifier.shutil,
        "which",
        lambda _name: pytest.fail("Azure CLI fallback must not run with a bearer token"),
    )
    observation_ids = seed["observation_ids"]
    assert isinstance(observation_ids, list)

    rows = verifier._query_rows(
        workspace_id="0c73db66-d049-4ddd-940e-502e1cb75cf1",
        revision="hinterland-api--0000045",
        image="hinterlandacrdev.azurecr.io/hinterland-api@sha256:" + "a" * 64,
        observation_ids=observation_ids,
        started_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 12, 12, 5, tzinfo=UTC),
        bearer_token="test.jwt.token",
    )

    assert rows == expected_rows
    request = captured["request"]
    assert request.full_url == (
        "https://api.loganalytics.azure.com/v1/workspaces/"
        "0c73db66-d049-4ddd-940e-502e1cb75cf1/query"
    )
    assert request.get_header("Authorization") == "Bearer test.jwt.token"
    assert captured["timeout"] == 90
    request_body = json.loads(request.data)
    assert observation_ids[0] in request_body["query"]
    assert "ContainerImage_s" in request_body["query"]


def test_github_actions_oidc_mints_log_analytics_token_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_secret = "github-request-secret"
    assertion = "header.payload.signature"
    access_secret = "log.analytics.token"
    client_id = "11111111-1111-4111-8111-111111111111"
    tenant_id = "22222222-2222-4222-8222-222222222222"
    captured: dict[str, object] = {}
    monkeypatch.setenv("AZURE_CLIENT_ID", client_id)
    monkeypatch.setenv("AZURE_TENANT_ID", tenant_id)
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/example/oidc?api-version=2.0",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", request_secret)
    monkeypatch.setattr(verifier.time, "time", lambda: 1_000.0)

    def fake_urlopen(request: object, *, timeout: int) -> io.BytesIO:
        captured["oidc_request"] = request
        captured["oidc_timeout"] = timeout
        return io.BytesIO(json.dumps({"value": assertion}).encode("utf-8"))

    class FakeCredential:
        def __init__(self, *, tenant_id: str, client_id: str, func: object) -> None:
            captured["tenant_id"] = tenant_id
            captured["client_id"] = client_id
            captured["assertion_func"] = func

        def get_token(self, scope: str) -> SimpleNamespace:
            captured["scope"] = scope
            assertion_func = captured["assertion_func"]
            captured["assertion"] = assertion_func()
            return SimpleNamespace(token=access_secret, expires_on=5_000)

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(azure_identity, "ClientAssertionCredential", FakeCredential)
    monkeypatch.setattr(
        verifier.shutil,
        "which",
        lambda _name: pytest.fail("GitHub OIDC must not invoke the Azure CLI"),
    )

    token = verifier._github_actions_log_analytics_token(minimum_validity_seconds=900)

    assert token == access_secret
    assert captured["tenant_id"] == tenant_id
    assert captured["client_id"] == client_id
    assert captured["scope"] == "https://api.loganalytics.io/.default"
    assert captured["assertion"] == assertion
    assert captured["closed"] is True
    request = captured["oidc_request"]
    assert request.get_header("Authorization") == f"Bearer {request_secret}"
    query = verifier.urllib.parse.parse_qs(verifier.urllib.parse.urlsplit(request.full_url).query)
    assert query["audience"] == ["api://AzureADTokenExchange"]
    assert captured["oidc_timeout"] == 30


def test_github_actions_missing_oidc_configuration_fails_without_cli_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for name in (
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        verifier.shutil,
        "which",
        lambda _name: pytest.fail("GitHub Actions must not fall back to Azure CLI"),
    )

    with pytest.raises(RuntimeError, match="omitted AZURE_CLIENT_ID"):
        verifier._resolve_bearer_token(log_token_stdin=False, timeout_seconds=900)


def test_github_oidc_failure_does_not_expose_request_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_secret = "never-print-github-request-token"
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/example/oidc?api-version=2.0",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", request_secret)

    def fake_urlopen(request: object, *, timeout: int) -> io.BytesIO:
        del timeout
        raise verifier.urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="status=403") as raised:
        verifier._github_oidc_assertion()

    assert request_secret not in str(raised.value)
    assert raised.value.__cause__ is None


def test_github_actions_rejects_stdin_token_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(sys, "stdin", io.StringIO("must-not-be-read"))

    with pytest.raises(RuntimeError, match="must use environment-scoped OIDC"):
        verifier._resolve_bearer_token(log_token_stdin=True, timeout_seconds=900)


def test_local_operator_can_still_supply_token_over_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("local.direct.token"))

    token = verifier._resolve_bearer_token(log_token_stdin=True, timeout_seconds=900)

    assert token == "local.direct.token"


def test_direct_http_failure_does_not_expose_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "never-print-this-token"
    seed = _seed(1)
    observation_ids = seed["observation_ids"]
    assert isinstance(observation_ids, list)

    def fake_urlopen(request: object, *, timeout: int) -> io.BytesIO:
        del timeout
        raise verifier.urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":"Unauthorized"}}'),
        )

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="status=401") as raised:
        verifier._query_rows(
            workspace_id="0c73db66-d049-4ddd-940e-502e1cb75cf1",
            revision="hinterland-api--0000045",
            image="hinterlandacrdev.azurecr.io/hinterland-api@sha256:" + "a" * 64,
            observation_ids=observation_ids,
            started_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 12, 12, 5, tzinfo=UTC),
            bearer_token=secret,
        )

    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None


def test_cli_invocation_preserves_windows_and_posix_paths() -> None:
    arguments = ["monitor", "log-analytics", "query", "--analytics-query", "Table | take 1"]

    windows_command, windows_shell, windows_executable = verifier._azure_cli_invocation(
        r"C:\Program Files\Azure CLI\az.cmd",
        arguments,
        windows=True,
        comspec=r"C:\Windows\System32\cmd.exe",
    )
    posix_command, posix_shell, posix_executable = verifier._azure_cli_invocation(
        "/usr/bin/az",
        arguments,
        windows=False,
        comspec=None,
    )

    assert isinstance(windows_command, str)
    assert '"Table | take 1"' in windows_command
    assert windows_shell is True
    assert windows_executable == r"C:\Windows\System32\cmd.exe"
    assert posix_command == ["/usr/bin/az", *arguments]
    assert posix_shell is False
    assert posix_executable is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tables": []},
        {"tables": [{"columns": [{"name": "only"}], "rows": [[1, 2]]}]},
    ],
)
def test_http_result_shape_fails_closed(payload: object) -> None:
    with pytest.raises(RuntimeError, match="Log Analytics HTTP API"):
        verifier._rows_from_http_response(payload)
