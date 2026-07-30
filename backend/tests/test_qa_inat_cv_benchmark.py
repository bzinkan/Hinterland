from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from ulid import ULID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.qa_inat_cv_benchmark import (
    BenchmarkConfigurationError,
    load_manifest,
    require_fresh_bearer,
    run_benchmark,
)


def _fresh_bearer() -> str:
    expiry = int(datetime.now(UTC).timestamp()) + 7_200
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode())
    return f"header.{payload.decode().rstrip('=')}.signature"


def _manifest(tmp_path: Path, *, count: int = 50) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "consent_policy_version": "2026-07-30-ADULT-QA-EGRESS-V1",
                "entries": [
                    {
                        "observation_id": str(ULID()),
                        "accepted_taxon_ids": [12345],
                    }
                    for _ in range(count)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_manifest_requires_exactly_50_distinct_qa_observations(
    tmp_path: Path,
) -> None:
    assert len(load_manifest(_manifest(tmp_path))) == 50

    with pytest.raises(BenchmarkConfigurationError, match="exactly 50"):
        load_manifest(_manifest(tmp_path, count=49))


def test_benchmark_bearer_freshness_fails_closed() -> None:
    require_fresh_bearer(_fresh_bearer())
    with pytest.raises(BenchmarkConfigurationError):
        require_fresh_bearer("not-a-token")


async def test_runtime_benchmark_requires_safe_qa_meta_and_measures_top_three(
    tmp_path: Path,
) -> None:
    cases = load_manifest(_manifest(tmp_path))

    async def handler(request: httpx.Request) -> httpx.Response:
        headers = {"X-Hinterland-Release-Lane": "adult_qa"}
        if request.url.path == "/v1/meta":
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "release_lane": "adult_qa",
                    "capabilities": {
                        "observation": {"photo_helper_enabled": True},
                        "integrations": {
                            "content_safety_enabled": True,
                            "public_inat_submission_enabled": False,
                        },
                    },
                },
            )
        return httpx.Response(
            200,
            headers=headers,
            json={
                "cv_unavailable": False,
                "suggestions": [{"taxon_id": 12345, "common_name": "Cardinal"}],
            },
        )

    result = await run_benchmark(
        cases,
        api_origin="https://qa-api.thehinterlandguide.app",
        bearer=_fresh_bearer(),
        interval_seconds=0,
        transport=httpx.MockTransport(handler),
    )

    assert result["result"] == "success"
    assert result["top_three_passed"] == 50
    assert result["top_three_accuracy"] == 1.0
    assert not any(
        case.observation_id in json.dumps(result) for case in cases
    )


async def test_runtime_benchmark_rejects_public_submission_enabled(
    tmp_path: Path,
) -> None:
    cases = load_manifest(_manifest(tmp_path))

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Hinterland-Release-Lane": "adult_qa"},
            json={
                "release_lane": "adult_qa",
                "capabilities": {
                    "observation": {"photo_helper_enabled": True},
                    "integrations": {
                        "content_safety_enabled": True,
                        "public_inat_submission_enabled": True,
                    },
                },
            },
        )

    with pytest.raises(
        BenchmarkConfigurationError,
        match="public iNaturalist submission",
    ):
        await run_benchmark(
            cases,
            api_origin="https://qa-api.thehinterlandguide.app",
            bearer=_fresh_bearer(),
            interval_seconds=0,
            transport=httpx.MockTransport(handler),
        )
