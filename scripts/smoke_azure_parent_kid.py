#!/usr/bin/env python3
"""Azure-era parent -> group -> kid handoff smoke test.

This replaces the legacy Firebase-only ``smoke_phase4.py`` for the active
ADR 0010 runtime. The parent token is supplied by the operator or GitHub
Actions secret because Entra CIAM interactive sign-in is intentionally not
automated in repo code.

Environment:
    DRAGONFLY_API_BASE_URL        default: https://api.thehinterlandguide.app
    DRAGONFLY_SMOKE_ENTRA_BEARER  required: Entra access token for a parent
    DRAGONFLY_SMOKE_PARENT_NAME   default: Smoke Test Parent
    DRAGONFLY_SMOKE_KID_NAME      default: Sparrow
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API_BASE = os.environ.get(
    "DRAGONFLY_API_BASE_URL", "https://api.thehinterlandguide.app"
).rstrip("/")
PARENT_BEARER = os.environ.get("DRAGONFLY_SMOKE_ENTRA_BEARER", "").strip()
PARENT_NAME = os.environ.get("DRAGONFLY_SMOKE_PARENT_NAME", "Smoke Test Parent")
KID_NAME = os.environ.get("DRAGONFLY_SMOKE_KID_NAME", "Sparrow")


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


def _smoke_email() -> str:
    claims = _decode_jwt_payload(PARENT_BEARER)
    email = claims.get("preferred_username") or claims.get("email")
    if isinstance(email, str) and "@" in email:
        return email
    return f"smoke+{int(time.time())}@dragonfly-test.invalid"


def request(
    method: str,
    path_or_url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
    req = urllib.request.Request(url, data=encoded, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - internal smoke
            raw = resp.read()
            return resp.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def expect(
    label: str,
    status: int,
    payload: Any,
    *,
    expected_status: int | tuple[int, ...] = 200,
) -> None:
    expected = expected_status if isinstance(expected_status, tuple) else (expected_status,)
    if status in expected:
        return
    print(f"\n[FAIL] {label}: got HTTP {status}, expected {expected}")
    print(f"  body: {json.dumps(payload, indent=2)[:1200]}")
    sys.exit(2)


def main() -> int:
    if not PARENT_BEARER:
        print("DRAGONFLY_SMOKE_ENTRA_BEARER is required.", file=sys.stderr)
        return 2

    parent_email = _smoke_email()
    print(f"API base: {API_BASE}")
    print(f"Parent:   {parent_email}")

    print("[1/7] GET /health...")
    status, payload = request("GET", "/health")
    expect("/health", status, payload)

    print("[2/7] POST /v1/auth/consent...")
    status, payload = request(
        "POST",
        "/v1/auth/consent",
        body={"email": parent_email, "kid_display_name": KID_NAME},
    )
    expect("/v1/auth/consent", status, payload)
    consent_id = payload["id"]
    print(f"      consent_id={consent_id}")

    print("[3/7] POST /v1/auth/parent-signup...")
    status, payload = request(
        "POST",
        "/v1/auth/parent-signup",
        token=PARENT_BEARER,
        body={"display_name": PARENT_NAME},
    )
    expect("/v1/auth/parent-signup", status, payload)
    parent_user_id = payload["id"]
    assert payload["role"] == "parent", payload
    print(f"      parent_user_id={parent_user_id}")

    print("[4/7] POST /v1/groups...")
    status, payload = request(
        "POST",
        "/v1/groups",
        token=PARENT_BEARER,
        body={"name": f"Smoke Test Family {int(time.time())}"},
    )
    expect("/v1/groups", status, payload, expected_status=201)
    group_id = payload["id"]
    print(f"      group_id={group_id} join_code={payload['join_code']}")

    print("[5/7] POST /v1/groups/{group_id}/kids...")
    status, payload = request(
        "POST",
        f"/v1/groups/{group_id}/kids",
        token=PARENT_BEARER,
        body={"display_name": KID_NAME, "age_band": "9-10"},
    )
    expect("/v1/groups/{group_id}/kids", status, payload, expected_status=201)
    kid_user_id = payload["id"]
    handoff_token = payload["handoff_token"]
    print(f"      kid_user_id={kid_user_id}")

    print("[6/7] POST /v1/auth/kid-exchange...")
    status, payload = request(
        "POST",
        "/v1/auth/kid-exchange",
        body={"handoff_token": handoff_token},
    )
    expect("/v1/auth/kid-exchange", status, payload)
    kid_session_token = payload["session_token"]

    print("[7/7] GET /v1/me as kid...")
    status, payload = request("GET", "/v1/me", token=kid_session_token)
    expect("/v1/me", status, payload)
    assert payload["uid"] == kid_user_id, payload
    assert payload["role"] == "kid", payload
    assert payload["group_id"] == group_id, payload

    print("\nALL CHECKS PASSED -- Azure parent/kid handoff flow works end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
