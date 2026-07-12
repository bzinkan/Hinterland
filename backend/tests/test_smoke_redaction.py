from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from ulid import ULID

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import smoke_azure_parent_kid as parent_smoke  # noqa: E402
import smoke_observation_w1 as observation_smoke  # noqa: E402


def test_observation_failure_never_logs_sas_or_response_body() -> None:
    response = httpx.Response(
        500,
        request=httpx.Request(
            "PUT", "https://example.blob.core.windows.net/photos/photo.jpg?sig=SECRET"
        ),
        headers={"x-ms-request-id": "azure-request-1"},
        json={"handoff_token": "SECRET", "detail": "private"},
    )

    with pytest.raises(RuntimeError) as raised:
        observation_smoke._expect(response, 200)

    message = str(raised.value)
    assert "?sig=" not in message
    assert "SECRET" not in message
    assert "azure-request-1" in message
    assert "/photos/photo.jpg" in message


def test_parent_failure_never_logs_handoff_token_or_body() -> None:
    with pytest.raises(RuntimeError) as raised:
        parent_smoke.expect(
            "/v1/groups/group/kids",
            500,
            {"handoff_token": "SECRET", "detail": {"code": "safe_code"}},
            headers={"x-request-id": "request-1"},
            expected_status=201,
        )

    message = str(raised.value)
    assert "SECRET" not in message
    assert "request-1" in message
    assert "safe_code" in message


def test_child_dto_rejects_raw_moderation_status() -> None:
    with pytest.raises(RuntimeError, match="moderation_status"):
        observation_smoke._assert_child_dto_minimized(
            {"id": "observation", "moderation_status": "pending"}
        )


def test_dispatcher_benchmark_seeds_mixed_private_safe_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            self.photo_id = ""
            self.create_payloads: list[dict[str, object]] = []

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            path = httpx.URL(url).path
            request = httpx.Request(method, url)
            if path.endswith("/v1/expeditions/backyard_starter/start"):
                return httpx.Response(201, request=request, json={"id": "progress"})
            if path.endswith("/v1/photos/presign"):
                self.photo_id = str(ULID())
                return httpx.Response(
                    201,
                    request=request,
                    headers={"x-request-id": f"presign-{self.photo_id}"},
                    json={
                        "photo_id": self.photo_id,
                        "upload_url": "https://blob.example.test/pending.jpg?sig=SECRET",
                        "upload_headers": {"x-ms-blob-type": "BlockBlob"},
                    },
                )
            if method == "PUT":
                return httpx.Response(201, request=request)
            if path.endswith("/v1/observations"):
                payload = kwargs.get("json")
                assert isinstance(payload, dict)
                self.create_payloads.append(payload)
                observation_id = str(ULID())
                return httpx.Response(
                    201,
                    request=request,
                    headers={"x-request-id": f"create-{observation_id}"},
                    json={"id": observation_id, "dispatch_status": "complete", "rewards": []},
                )
            raise AssertionError(f"unexpected request: {method} {path}")

    client = FakeClient()
    monkeypatch.setattr(observation_smoke.httpx, "Client", lambda **_kwargs: client)

    evidence = observation_smoke.run_dispatcher_benchmark(
        base_url="https://api.example.test",
        bearer="kid-token",
        sample_count=20,
    )

    assert evidence.sample_count == 20
    assert len(evidence.observation_ids) == 20
    assert len(evidence.create_request_ids) == 20
    assert evidence.scenario_counts == {
        "unknown_no_location": 10,
        "catalog_no_location": 5,
        "catalog_coarse_location": 5,
    }
    assert all(
        "latitude" not in payload and "longitude" not in payload
        for payload in client.create_payloads
    )
    assert sum(payload.get("geohash4") == "dnp1" for payload in client.create_payloads) == 5
