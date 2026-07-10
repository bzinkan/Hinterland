"""Operator W1 canary for the deployed Azure Observation path."""

from __future__ import annotations

import io
import os
import time
from datetime import UTC, datetime

import httpx
from PIL import Image
from ulid import ULID


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), color=(76, 116, 74)).save(
        output, format="JPEG", quality=80
    )
    return output.getvalue()


def _expect(response: httpx.Response, *statuses: int) -> None:
    if response.status_code not in statuses:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text[:500]}"
        )


def main() -> None:
    base_url = _required("HINTERLAND_API_BASE_URL").rstrip("/")
    bearer = _required("HINTERLAND_SMOKE_BEARER")
    key = str(ULID())
    auth = {"Authorization": f"Bearer {bearer}"}

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        presign = client.post(
            f"{base_url}/v1/photos/presign",
            headers={**auth, "Idempotency-Key": key},
            json={"content_type": "image/jpeg"},
        )
        _expect(presign, 201)
        reservation = presign.json()
        upload_headers = reservation.get("upload_headers", {})
        if upload_headers.get("x-ms-blob-type") != "BlockBlob":
            raise RuntimeError("presign omitted x-ms-blob-type: BlockBlob")
        if not reservation.get("upload_url"):
            raise RuntimeError("new reservation omitted upload_url")

        upload = client.put(
            reservation["upload_url"], headers=upload_headers, content=_jpeg()
        )
        _expect(upload, 200, 201)

        payload = {
            "photo_id": reservation["photo_id"],
            "observed_at": datetime.now(UTC).isoformat(),
            "location_source": "none",
            "identification_source": "unknown",
        }
        created = client.post(
            f"{base_url}/v1/observations",
            headers={**auth, "Idempotency-Key": key},
            json=payload,
        )
        _expect(created, 201)
        observation = created.json()

        replay = client.post(
            f"{base_url}/v1/observations",
            headers={**auth, "Idempotency-Key": key},
            json=payload,
        )
        _expect(replay, 200)
        if replay.json().get("id") != observation.get("id"):
            raise RuntimeError("replay returned a different observation")
        if replay.headers.get("Idempotency-Replayed", "").lower() != "true":
            raise RuntimeError("replay omitted Idempotency-Replayed: true")
        if replay.json().get("rewards") != observation.get("rewards"):
            raise RuntimeError("replay returned different persisted rewards")

        conflict = client.post(
            f"{base_url}/v1/observations",
            headers={**auth, "Idempotency-Key": key},
            json={**payload, "observed_at": datetime.now(UTC).isoformat()},
        )
        _expect(conflict, 409)

        attached_presign = client.post(
            f"{base_url}/v1/photos/presign",
            headers={**auth, "Idempotency-Key": key},
            json={"content_type": "image/jpeg"},
        )
        _expect(attached_presign, 200, 201)
        attached = attached_presign.json()
        if attached.get("photo_id") != reservation["photo_id"]:
            raise RuntimeError("presign replay returned a different photo")
        if attached.get("observation_id") != observation["id"] or attached.get(
            "upload_url"
        ):
            raise RuntimeError("attached presign did not reconcile")

        listing = client.get(f"{base_url}/v1/observations/me?limit=50", headers=auth)
        _expect(listing, 200)
        matches = [
            item
            for item in listing.json().get("items", [])
            if item.get("id") == observation["id"]
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Field Journal contained {len(matches)} copies")

        deadline = time.monotonic() + 180
        moderation_status = "pending"
        while time.monotonic() < deadline:
            detail = client.get(
                f"{base_url}/v1/observations/{observation['id']}", headers=auth
            )
            _expect(detail, 200)
            moderation_status = detail.json().get("moderation_status", "")
            if moderation_status == "pilot_private":
                break
            if moderation_status in {"clean", "quarantine"}:
                raise RuntimeError(f"W1 entered forbidden state {moderation_status}")
            time.sleep(5)
        if moderation_status != "pilot_private":
            raise RuntimeError(f"NoOp did not reach pilot_private: {moderation_status}")

        photo_url = client.get(
            f"{base_url}/v1/photos/{reservation['photo_id']}/url", headers=auth
        )
        _expect(photo_url, 404)

    print(
        "W1 canary passed: BlockBlob upload, conflict-safe exactly-one replay, "
        "pilot_private, and no signed photo URL"
    )


if __name__ == "__main__":
    main()
