#!/usr/bin/env python3
"""Azure parent -> group -> kid handoff and Observation W1 smoke.

The parent access token is supplied by the operator or the protected GitHub
Actions environment. This script creates a throwaway kid, keeps that kid's
session token in process memory, and passes it directly to the Observation W1
canary. No persistent kid-token secret is required.

Environment:
    HINTERLAND_API_BASE_URL         default: https://api.thehinterlandguide.app
    HINTERLAND_SMOKE_ENTRA_BEARER   required: Entra access token for a parent
    HINTERLAND_SMOKE_PARENT_NAME    default: Smoke Test Parent
    HINTERLAND_SMOKE_KID_NAME       default: Sparrow
    HINTERLAND_SMOKE_EVIDENCE_PATH optional sanitized JSON result path
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smoke_observation_w1 import (
    DispatcherBenchmarkSeed,
    ObservationCanaryEvidence,
    run_canary,
    run_dispatcher_benchmark,
)

API_BASE = os.environ.get("HINTERLAND_API_BASE_URL", "https://api.thehinterlandguide.app").rstrip(
    "/"
)
PARENT_BEARER = os.environ.get("HINTERLAND_SMOKE_ENTRA_BEARER", "").strip()
PARENT_NAME = os.environ.get("HINTERLAND_SMOKE_PARENT_NAME", "Smoke Test Parent")
KID_NAME = os.environ.get("HINTERLAND_SMOKE_KID_NAME", "Sparrow")
DISPATCHER_BENCHMARK_SAMPLES = int(os.environ.get("HINTERLAND_DISPATCHER_BENCHMARK_SAMPLES", "0"))
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verification, for smoke metadata only."""
    try:
        _header, payload, _signature = token.split(".", 2)
        padded = payload + "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(raw)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _smoke_email(token: str) -> str:
    claims = _decode_jwt_payload(token)
    email = claims.get("preferred_username") or claims.get("email")
    if isinstance(email, str) and "@" in email:
        return email
    return f"smoke+{int(time.time())}@hinterland-test.invalid"


def request(
    base_url: str,
    method: str,
    path_or_url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any, Mapping[str, str]]:
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = path_or_url if path_or_url.startswith("http") else f"{base_url}{path_or_url}"
    req = urllib.request.Request(url, data=encoded, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw or b"{}"), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload, dict(exc.headers.items())


def expect(
    label: str,
    status: int,
    payload: Any,
    *,
    headers: Mapping[str, str] | None = None,
    expected_status: int | tuple[int, ...] = 200,
) -> None:
    expected = expected_status if isinstance(expected_status, tuple) else (expected_status,)
    if status in expected:
        return
    normalized = {key.lower(): value for key, value in (headers or {}).items()}
    request_id = next(
        (
            normalized.get(name, "").strip()
            for name in ("x-request-id", "x-correlation-id", "request-id")
            if _REQUEST_ID_PATTERN.fullmatch(normalized.get(name, "").strip())
        ),
        "unavailable",
    )
    error_code = "unavailable"
    detail = payload.get("detail", {}) if isinstance(payload, dict) else {}
    candidate = detail.get("code") if isinstance(detail, dict) else None
    if isinstance(candidate, str) and _REQUEST_ID_PATTERN.fullmatch(candidate):
        error_code = candidate
    raise RuntimeError(
        f"{label} returned HTTP {status}, expected {expected}; "
        f"request_id={request_id}; error_code={error_code}"
    )


def _record_request_id(headers: Mapping[str, str], request_ids: list[str]) -> None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for name in ("x-request-id", "x-correlation-id", "request-id"):
        value = normalized.get(name, "").strip()
        if value and _REQUEST_ID_PATTERN.fullmatch(value) and value not in request_ids:
            request_ids.append(value)
            return


def _write_evidence(
    path: str | os.PathLike[str],
    *,
    request_ids: list[str],
    observation: ObservationCanaryEvidence,
    dispatcher_benchmark: DispatcherBenchmarkSeed | None,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "result": "passed",
                "parent_kid_handoff": "passed",
                "existing_kid_handoff_reissue": "passed",
                "current_parent_consent_enforced": True,
                "starter_expedition_visible": True,
                "request_ids": request_ids,
                "observation_canary": observation.to_public_dict(),
                "dispatcher_benchmark": (
                    dispatcher_benchmark.to_public_dict()
                    if dispatcher_benchmark is not None
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_parent_kid_smoke(
    *,
    base_url: str,
    parent_bearer: str,
    parent_name: str = PARENT_NAME,
    kid_name: str = KID_NAME,
    evidence_path: str | os.PathLike[str] | None = None,
    dispatcher_benchmark_samples: int = DISPATCHER_BENCHMARK_SAMPLES,
) -> ObservationCanaryEvidence:
    """Create a throwaway kid and pass its session directly to the W1 canary."""

    base_url = base_url.strip().rstrip("/")
    parent_bearer = parent_bearer.strip()
    if not parent_bearer:
        raise RuntimeError("HINTERLAND_SMOKE_ENTRA_BEARER is required")

    parent_email = _smoke_email(parent_bearer)
    consent_nonce = secrets.token_hex(32)
    request_ids: list[str] = []
    print(f"API base: {base_url}")

    print("[1/12] GET /health...")
    status, payload, headers = request(base_url, "GET", "/health")
    _record_request_id(headers, request_ids)
    expect("/health", status, payload, headers=headers)

    print("[2/12] POST /v1/auth/consent...")
    status, payload, headers = request(
        base_url,
        "POST",
        "/v1/auth/consent",
        body={
            "email": parent_email,
            "kid_display_name": kid_name,
            "policy_version": "2026-07-11-W1-INTERNAL",
            "consent_nonce": consent_nonce,
        },
    )
    _record_request_id(headers, request_ids)
    expect("/v1/auth/consent", status, payload, headers=headers)
    consent_id = payload.get("id")
    if not isinstance(consent_id, str) or len(consent_id) != 26:
        raise RuntimeError("consent response omitted the receipt id")

    print("[3/12] POST /v1/auth/parent-signup...")
    status, payload, headers = request(
        base_url,
        "POST",
        "/v1/auth/parent-signup",
        token=parent_bearer,
        body={
            "display_name": parent_name,
            "consent_id": consent_id,
            "consent_nonce": consent_nonce,
        },
    )
    _record_request_id(headers, request_ids)
    expect("/v1/auth/parent-signup", status, payload, headers=headers)
    if payload.get("role") != "parent":
        raise RuntimeError("parent signup returned an unexpected role")
    parent_user_id = payload["id"]

    print("[4/12] GET /v1/me as parent...")
    status, payload, headers = request(base_url, "GET", "/v1/me", token=parent_bearer)
    _record_request_id(headers, request_ids)
    expect("/v1/me", status, payload, headers=headers)
    if (
        payload.get("uid") != parent_user_id
        or payload.get("id") != parent_user_id
        or payload.get("display_name") != parent_name
        or payload.get("role") != "parent"
    ):
        raise RuntimeError("parent /v1/me omitted the canonical mobile identity")

    print("[5/12] POST /v1/groups...")
    status, payload, headers = request(
        base_url,
        "POST",
        "/v1/groups",
        token=parent_bearer,
        body={"name": f"Smoke Test Family {int(time.time())}"},
    )
    _record_request_id(headers, request_ids)
    expect("/v1/groups", status, payload, headers=headers, expected_status=201)
    # Group creation is server-gated on a receipt linked to this canonical
    # parent for the exact active W1 policy.  A 201 therefore proves the
    # consent recorded in step 2 was linked, not merely stored.
    group_id = payload["id"]

    print("[6/12] POST /v1/groups/{group_id}/kids...")
    status, payload, headers = request(
        base_url,
        "POST",
        f"/v1/groups/{group_id}/kids",
        token=parent_bearer,
        body={"display_name": kid_name, "age_band": "9-10"},
    )
    _record_request_id(headers, request_ids)
    expect(
        "/v1/groups/{group_id}/kids",
        status,
        payload,
        headers=headers,
        expected_status=201,
    )
    kid_user_id = payload["id"]
    initial_handoff_token = payload.get("handoff_token")
    if not isinstance(initial_handoff_token, str) or not initial_handoff_token:
        raise RuntimeError("kid create response omitted the one-time handoff")

    print("[7/12] POST /v1/auth/kid-exchange with initial handoff...")
    status, payload, headers = request(
        base_url,
        "POST",
        "/v1/auth/kid-exchange",
        body={"handoff_token": initial_handoff_token},
    )
    _record_request_id(headers, request_ids)
    expect("/v1/auth/kid-exchange", status, payload, headers=headers)
    initial_kid_session_token = payload["session_token"]

    print("[8/12] GET /v1/me with initial kid session...")
    status, payload, headers = request(
        base_url,
        "GET",
        "/v1/me",
        token=initial_kid_session_token,
    )
    _record_request_id(headers, request_ids)
    expect("/v1/me", status, payload, headers=headers)
    if (
        payload.get("uid") != kid_user_id
        or payload.get("id") != kid_user_id
        or payload.get("display_name") != kid_name
        or payload.get("role") != "kid"
        or payload.get("group_id") != group_id
    ):
        raise RuntimeError("initial kid /v1/me did not match the throwaway handoff")

    print("[9/12] POST existing-kid handoff reissue...")
    status, payload, headers = request(
        base_url,
        "POST",
        f"/v1/groups/{group_id}/kids/{kid_user_id}/handoff",
        token=parent_bearer,
    )
    _record_request_id(headers, request_ids)
    expect(
        "/v1/groups/{group_id}/kids/{kid_user_id}/handoff",
        status,
        payload,
        headers=headers,
    )
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    if "no-store" not in normalized_headers.get("cache-control", "").lower():
        raise RuntimeError("kid handoff reissue response was cacheable")
    if payload.get("id") != kid_user_id or payload.get("display_name") != kid_name:
        raise RuntimeError("kid handoff reissue did not return the canonical kid")
    handoff_token = payload.get("handoff_token")
    if not isinstance(handoff_token, str) or not handoff_token:
        raise RuntimeError("kid handoff reissue omitted the one-time handoff")
    if handoff_token == initial_handoff_token:
        raise RuntimeError("kid handoff reissue reused the prior credential")
    if not isinstance(payload.get("expires_at"), str):
        raise RuntimeError("kid handoff reissue omitted its expiry")

    # Reissuing a sign-in QR must not claim to revoke a session that was
    # already issued to the same canonical kid.
    status, old_session_payload, headers = request(
        base_url,
        "GET",
        "/v1/me",
        token=initial_kid_session_token,
    )
    _record_request_id(headers, request_ids)
    expect("/v1/me after handoff reissue", status, old_session_payload, headers=headers)
    if old_session_payload.get("id") != kid_user_id:
        raise RuntimeError("handoff reissue unexpectedly invalidated the existing kid session")

    print("[10/12] Exchange reissued handoff for the same canonical kid...")
    status, payload, headers = request(
        base_url,
        "POST",
        "/v1/auth/kid-exchange",
        body={"handoff_token": handoff_token},
    )
    _record_request_id(headers, request_ids)
    expect("/v1/auth/kid-exchange", status, payload, headers=headers)
    kid_session_token = payload["session_token"]

    status, payload, headers = request(base_url, "GET", "/v1/me", token=kid_session_token)
    _record_request_id(headers, request_ids)
    expect("/v1/me", status, payload, headers=headers)
    if (
        payload.get("uid") != kid_user_id
        or payload.get("id") != kid_user_id
        or payload.get("display_name") != kid_name
        or payload.get("role") != "kid"
        or payload.get("group_id") != group_id
    ):
        raise RuntimeError("reissued kid /v1/me did not match the existing canonical kid")

    print("[11/12] GET /v1/expeditions/available as kid...")
    deadline = time.time() + 90
    last_status: int | None = None
    last_payload: Any = None
    while time.time() < deadline:
        last_status, last_payload, headers = request(
            base_url,
            "GET",
            "/v1/expeditions/available",
            token=kid_session_token,
        )
        _record_request_id(headers, request_ids)
        if last_status == 200:
            items = last_payload.get("items") if isinstance(last_payload, dict) else None
            if isinstance(items, list) and any(
                isinstance(item, dict) and item.get("id") == "backyard_starter" for item in items
            ):
                print("      starter expedition visible")
                break
        time.sleep(5)
    else:
        expect(
            "/v1/expeditions/available",
            last_status or 0,
            last_payload,
            headers=headers,
            expected_status=200,
        )
        raise RuntimeError("backyard_starter not visible")

    print("[12/12] Observation W1 canary with in-memory kid session...")
    observation = run_canary(base_url=base_url, bearer=kid_session_token)
    dispatcher_benchmark: DispatcherBenchmarkSeed | None = None
    if dispatcher_benchmark_samples:
        print(
            "[benchmark] Seeding exact-revision dispatcher workload "
            f"({dispatcher_benchmark_samples} observations)..."
        )
        dispatcher_benchmark = run_dispatcher_benchmark(
            base_url=base_url,
            bearer=kid_session_token,
            sample_count=dispatcher_benchmark_samples,
        )
    if evidence_path:
        _write_evidence(
            evidence_path,
            request_ids=request_ids,
            observation=observation,
            dispatcher_benchmark=dispatcher_benchmark,
        )

    print(
        "\nALL CHECKS PASSED -- Azure parent/kid handoff, Expedition content, "
        "and Observation W1 canary work."
    )
    return observation


def main() -> int:
    try:
        run_parent_kid_smoke(
            base_url=API_BASE,
            parent_bearer=PARENT_BEARER,
            evidence_path=os.environ.get("HINTERLAND_SMOKE_EVIDENCE_PATH") or None,
            dispatcher_benchmark_samples=DISPATCHER_BENCHMARK_SAMPLES,
        )
    except Exception as exc:
        print(f"Authenticated smoke failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
