"""Run the bounded Adult QA iNaturalist CV qualification benchmark.

The photos are already stored as clean/adult-approved QA observations. This
program calls the real post-clean API route so the benchmark includes runtime
consent, moderation, catalog filtering, canonical naming, and cache behavior.
It never accepts or prints the provider credential.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

_EXPECTED_API_ORIGIN = "https://qa-api.thehinterlandguide.app"
_EXPECTED_LANE = "adult_qa"
_EXPECTED_CASES = 50
_MIN_ACCURACY = 0.70
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class BenchmarkConfigurationError(RuntimeError):
    """The benchmark input or QA safety posture is invalid."""


@dataclass(frozen=True)
class BenchmarkCase:
    observation_id: str
    accepted_taxon_ids: frozenset[int]


def load_manifest(path: Path) -> list[BenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise BenchmarkConfigurationError("manifest version must be 1")
    if (
        payload.get("consent_policy_version")
        != "2026-07-30-ADULT-QA-EGRESS-V1"
    ):
        raise BenchmarkConfigurationError(
            "manifest must name the current Adult QA disclosure"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != _EXPECTED_CASES:
        raise BenchmarkConfigurationError(
            f"manifest must contain exactly {_EXPECTED_CASES} cases"
        )

    cases: list[BenchmarkCase] = []
    seen_observations: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BenchmarkConfigurationError("each benchmark case must be an object")
        observation_id = entry.get("observation_id")
        accepted = entry.get("accepted_taxon_ids")
        if not isinstance(observation_id, str) or not _ULID.fullmatch(
            observation_id
        ):
            raise BenchmarkConfigurationError(
                "each case requires a canonical observation ULID"
            )
        if observation_id in seen_observations:
            raise BenchmarkConfigurationError("observation IDs must be unique")
        if (
            not isinstance(accepted, list)
            or not accepted
            or any(
                isinstance(taxon_id, bool)
                or not isinstance(taxon_id, int)
                or taxon_id <= 0
                for taxon_id in accepted
            )
        ):
            raise BenchmarkConfigurationError(
                "accepted_taxon_ids must contain positive integer catalog IDs"
            )
        seen_observations.add(observation_id)
        cases.append(
            BenchmarkCase(
                observation_id=observation_id,
                accepted_taxon_ids=frozenset(accepted),
            )
        )
    return cases


def validate_api_origin(value: str) -> str:
    parsed = urlparse(value.rstrip("/"))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if (
        origin != _EXPECTED_API_ORIGIN
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise BenchmarkConfigurationError(
            f"benchmark API must be {_EXPECTED_API_ORIGIN}"
        )
    return origin


def require_fresh_bearer(token: str, *, now: datetime | None = None) -> None:
    """Require enough lifetime for the run without logging the bearer."""

    try:
        parts = token.strip().split(".")
        if len(parts) != 3:
            raise ValueError
        payload = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
        expiry = payload["exp"]
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkConfigurationError(
            "QA benchmark bearer is missing a usable expiry"
        ) from exc
    if (
        isinstance(expiry, bool)
        or not isinstance(expiry, int | float)
        or not math.isfinite(float(expiry))
    ):
        raise BenchmarkConfigurationError(
            "QA benchmark bearer is missing a usable expiry"
        )
    current = (now or datetime.now(UTC)).timestamp()
    if float(expiry) <= current + 1_800:
        raise BenchmarkConfigurationError(
            "QA benchmark bearer must have at least 30 minutes remaining"
        )


async def run_benchmark(
    cases: list[BenchmarkCase],
    *,
    api_origin: str,
    bearer: str,
    interval_seconds: float = 1.1,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer}",
    }
    passed = 0
    unavailable = 0
    malformed = 0

    async with httpx.AsyncClient(
        base_url=api_origin,
        headers=headers,
        timeout=20,
        transport=transport,
    ) as client:
        meta_response = await client.get("/v1/meta")
        _verify_qa_meta(meta_response)

        for index, case in enumerate(cases):
            if index > 0 and interval_seconds > 0:
                await asyncio.sleep(interval_seconds)
            try:
                response = await client.post(
                    f"/v1/observations/{case.observation_id}/identify"
                )
            except httpx.HTTPError:
                unavailable += 1
                continue
            if response.status_code != 200:
                unavailable += 1
                continue
            try:
                body = response.json()
                suggestions = body["suggestions"]
                cv_unavailable = body["cv_unavailable"]
                suggested_ids = {
                    item["taxon_id"]
                    for item in suggestions[:3]
                    if isinstance(item, dict)
                    and isinstance(item.get("taxon_id"), int)
                    and not isinstance(item.get("taxon_id"), bool)
                }
            except (KeyError, TypeError, ValueError):
                malformed += 1
                continue
            if cv_unavailable is True:
                unavailable += 1
                continue
            if cv_unavailable is not False or not isinstance(suggestions, list):
                malformed += 1
                continue
            if suggested_ids & case.accepted_taxon_ids:
                passed += 1

    accuracy = passed / len(cases)
    return {
        "result": "success"
        if accuracy >= _MIN_ACCURACY and unavailable == 0 and malformed == 0
        else "failed",
        "release_lane": _EXPECTED_LANE,
        "case_count": len(cases),
        "top_three_passed": passed,
        "top_three_accuracy": round(accuracy, 4),
        "minimum_accuracy": _MIN_ACCURACY,
        "provider_unavailable_count": unavailable,
        "malformed_response_count": malformed,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _verify_qa_meta(response: httpx.Response) -> None:
    if response.status_code != 200:
        raise BenchmarkConfigurationError("QA metadata probe did not succeed")
    if response.headers.get("X-Hinterland-Release-Lane") != _EXPECTED_LANE:
        raise BenchmarkConfigurationError("QA response lane header did not match")
    try:
        body: Any = response.json()
        capabilities = body["capabilities"]
        integrations = capabilities["integrations"]
        observation = capabilities["observation"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkConfigurationError("QA metadata was malformed") from exc
    if body.get("release_lane") != _EXPECTED_LANE:
        raise BenchmarkConfigurationError("QA metadata lane did not match")
    if integrations.get("public_inat_submission_enabled") is not False:
        raise BenchmarkConfigurationError(
            "public iNaturalist submission must be disabled"
        )
    if integrations.get("content_safety_enabled") is not True:
        raise BenchmarkConfigurationError("real Content Safety must be enabled")
    if observation.get("photo_helper_enabled") is not True:
        raise BenchmarkConfigurationError("post-clean photo helper is not enabled")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-base-url", default=_EXPECTED_API_ORIGIN)
    parser.add_argument("--interval-seconds", type=float, default=1.1)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.output.is_absolute():
        raise BenchmarkConfigurationError("--output must be an absolute evidence path")
    if args.interval_seconds < 1.0:
        raise BenchmarkConfigurationError(
            "--interval-seconds must be at least 1.0 for provider courtesy"
        )
    bearer = os.environ.get("HINTERLAND_QA_BENCHMARK_BEARER", "").strip()
    if not bearer:
        raise BenchmarkConfigurationError(
            "HINTERLAND_QA_BENCHMARK_BEARER is required"
        )
    require_fresh_bearer(bearer)
    cases = load_manifest(args.manifest)
    result = asyncio.run(
        run_benchmark(
            cases,
            api_origin=validate_api_origin(args.api_base_url),
            bearer=bearer,
            interval_seconds=args.interval_seconds,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Adult QA CV benchmark "
        f"{result['result']}: {result['top_three_passed']}/{result['case_count']}"
    )
    return 0 if result["result"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
