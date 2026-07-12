#!/usr/bin/env python3
"""Strict W1 canary for the deployed Azure Observation path.

The parent/kid smoke imports :func:`run_canary` and passes its throwaway kid
session directly in memory. Operators may still run this file on its own with
``HINTERLAND_SMOKE_BEARER``. The optional runner-local evidence contains only
bounded operational identifiers and pass/fail facts; the promotion artifact
removes benchmark observation IDs before upload. Neither form contains the
bearer, SAS URL, image, child text, or location.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PIL import Image
from ulid import ULID

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FORBIDDEN_CHILD_FIELDS = {
    "latitude",
    "longitude",
    "moderation_status",
    "photo_object_name",
    "photo_status",
}


@dataclass(frozen=True)
class ObservationCanaryEvidence:
    result: str
    request_ids: list[str]
    block_blob_header: bool
    idempotent_replay: bool
    field_journal_exactly_once: bool
    child_presentation_status: str
    signed_photo_denied: bool
    child_dto_minimized: bool

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DispatcherBenchmarkSeed:
    """Bounded identifiers for exact-revision telemetry correlation."""

    result: str
    started_at: str
    finished_at: str
    sample_count: int
    observation_ids: list[str]
    create_request_ids: list[str]
    scenario_counts: dict[str, int]

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), color=(76, 116, 74)).save(output, format="JPEG", quality=80)
    return output.getvalue()


def _expect(response: httpx.Response, *statuses: int) -> None:
    if response.status_code not in statuses:
        request_id = next(
            (
                response.headers.get(header, "").strip()
                for header in (
                    "x-request-id",
                    "x-correlation-id",
                    "request-id",
                    "x-ms-request-id",
                )
                if _REQUEST_ID_PATTERN.fullmatch(response.headers.get(header, "").strip())
            ),
            "unavailable",
        )
        error_code = "unavailable"
        try:
            body = response.json()
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            candidate = detail.get("code") if isinstance(detail, dict) else None
            if isinstance(candidate, str) and _REQUEST_ID_PATTERN.fullmatch(candidate):
                error_code = candidate
        except ValueError:
            pass
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}; request_id={request_id}; error_code={error_code}"
        )


def _record_request_id(response: httpx.Response, request_ids: list[str]) -> None:
    for header in (
        "x-request-id",
        "x-correlation-id",
        "request-id",
        "x-ms-request-id",
    ):
        value = response.headers.get(header, "").strip()
        if value and _REQUEST_ID_PATTERN.fullmatch(value) and value not in request_ids:
            request_ids.append(value)
            return


def _request(
    client: httpx.Client,
    request_ids: list[str],
    method: str,
    url: str,
    **kwargs: object,
) -> httpx.Response:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        # In particular, never let an upload transport exception stringify the
        # SAS query from the raw URL into Actions logs.
        raise RuntimeError(
            f"{method} {httpx.URL(url).path} failed; request_id=unavailable; "
            "error_code=transport_error"
        ) from None
    _record_request_id(response, request_ids)
    return response


def _assert_child_dto_minimized(item: dict[str, object]) -> None:
    exposed = _FORBIDDEN_CHILD_FIELDS.intersection(item)
    if exposed:
        raise RuntimeError(
            "Field Journal exposed forbidden child fields: " + ", ".join(sorted(exposed))
        )


def _write_evidence(path: str | os.PathLike[str], evidence: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_canary(
    *,
    base_url: str,
    bearer: str,
    evidence_path: str | os.PathLike[str] | None = None,
) -> ObservationCanaryEvidence:
    """Run the W1 Observation canary using an already-minted kid session."""

    base_url = base_url.strip().rstrip("/")
    bearer = bearer.strip()
    if not base_url:
        raise RuntimeError("base_url is required")
    if not bearer:
        raise RuntimeError("kid bearer is required")

    key = str(ULID())
    auth = {"Authorization": f"Bearer {bearer}"}
    request_ids: list[str] = []

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        presign = _request(
            client,
            request_ids,
            "POST",
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

        # Do not record or persist the SAS URL or bytes in promotion evidence.
        upload = _request(
            client,
            request_ids,
            "PUT",
            reservation["upload_url"],
            headers=upload_headers,
            content=_jpeg(),
        )
        _expect(upload, 200, 201)

        payload = {
            "photo_id": reservation["photo_id"],
            "observed_at": datetime.now(UTC).isoformat(),
            "location_source": "none",
            "identification_source": "unknown",
        }
        created = _request(
            client,
            request_ids,
            "POST",
            f"{base_url}/v1/observations",
            headers={**auth, "Idempotency-Key": key},
            json=payload,
        )
        _expect(created, 201)
        observation = created.json()

        replay = _request(
            client,
            request_ids,
            "POST",
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

        conflict = _request(
            client,
            request_ids,
            "POST",
            f"{base_url}/v1/observations",
            headers={**auth, "Idempotency-Key": key},
            json={**payload, "observed_at": datetime.now(UTC).isoformat()},
        )
        _expect(conflict, 409)

        attached_presign = _request(
            client,
            request_ids,
            "POST",
            f"{base_url}/v1/photos/presign",
            headers={**auth, "Idempotency-Key": key},
            json={"content_type": "image/jpeg"},
        )
        _expect(attached_presign, 200, 201)
        attached = attached_presign.json()
        if attached.get("photo_id") != reservation["photo_id"]:
            raise RuntimeError("presign replay returned a different photo")
        if attached.get("observation_id") != observation["id"] or attached.get("upload_url"):
            raise RuntimeError("attached presign did not reconcile")

        listing = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/observations/me?order=observed&limit=50",
            headers=auth,
        )
        _expect(listing, 200)
        matches = [
            item for item in listing.json().get("items", []) if item.get("id") == observation["id"]
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Field Journal contained {len(matches)} copies")
        _assert_child_dto_minimized(matches[0])

        deadline = time.monotonic() + 180
        presentation_status = "pending"
        while time.monotonic() < deadline:
            detail = _request(
                client,
                request_ids,
                "GET",
                f"{base_url}/v1/observations/{observation['id']}",
                headers=auth,
            )
            _expect(detail, 200)
            detail_payload = detail.json()
            _assert_child_dto_minimized(detail_payload)
            presentation_status = detail_payload.get("child_presentation_status", "")
            if presentation_status == "pilot_private":
                break
            if presentation_status in {"clean", "adult_review", "failed"}:
                raise RuntimeError(f"W1 entered forbidden child state {presentation_status}")
            if presentation_status not in {"pending", "processing"}:
                raise RuntimeError(f"W1 returned unknown child state {presentation_status!r}")
            time.sleep(5)
        if presentation_status != "pilot_private":
            raise RuntimeError(f"NoOp did not reach pilot_private: {presentation_status}")

        final_listing = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/observations/me?order=observed&limit=50",
            headers=auth,
        )
        _expect(final_listing, 200)
        final_matches = [
            item
            for item in final_listing.json().get("items", [])
            if item.get("id") == observation["id"]
        ]
        if len(final_matches) != 1:
            raise RuntimeError("pilot-private Field Journal record was not stable")
        _assert_child_dto_minimized(final_matches[0])
        if final_matches[0].get("child_presentation_status") != "pilot_private":
            raise RuntimeError("Field Journal did not expose pilot_private presentation")

        photo_url = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/photos/{reservation['photo_id']}/url",
            headers=auth,
        )
        _expect(photo_url, 404)

    evidence = ObservationCanaryEvidence(
        result="passed",
        request_ids=request_ids,
        block_blob_header=True,
        idempotent_replay=True,
        field_journal_exactly_once=True,
        child_presentation_status="pilot_private",
        signed_photo_denied=True,
        child_dto_minimized=True,
    )
    if evidence_path:
        _write_evidence(evidence_path, evidence.to_public_dict())
    return evidence


def run_dispatcher_benchmark(
    *,
    base_url: str,
    bearer: str,
    sample_count: int,
) -> DispatcherBenchmarkSeed:
    """Create a representative, test-owned dispatcher workload.

    The protected workflow correlates only these observation IDs with the
    exact deployed revision's ``dispatcher.complete`` events. The workload is
    intentionally mixed: Unknown/no-location, catalog/no-location, and
    catalog/coarse-location submissions, with a real active starter Expedition.
    """

    if sample_count < 20 or sample_count > 100:
        raise RuntimeError("dispatcher benchmark sample_count must be between 20 and 100")
    base_url = base_url.strip().rstrip("/")
    bearer = bearer.strip()
    if not base_url or not bearer:
        raise RuntimeError("dispatcher benchmark requires base_url and kid bearer")

    auth = {"Authorization": f"Bearer {bearer}"}
    image_bytes = _jpeg()
    observation_ids: list[str] = []
    create_request_ids: list[str] = []
    scenario_counts: dict[str, int] = {
        "unknown_no_location": 0,
        "catalog_no_location": 0,
        "catalog_coarse_location": 0,
    }
    started_at = datetime.now(UTC)

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        expedition_request_ids: list[str] = []
        start = _request(
            client,
            expedition_request_ids,
            "POST",
            f"{base_url}/v1/expeditions/backyard_starter/start",
            headers=auth,
        )
        _expect(start, 201)

        for index in range(sample_count):
            key = str(ULID())
            request_ids: list[str] = []
            presign = _request(
                client,
                request_ids,
                "POST",
                f"{base_url}/v1/photos/presign",
                headers={**auth, "Idempotency-Key": key},
                json={"content_type": "image/jpeg"},
            )
            _expect(presign, 201)
            reservation = presign.json()
            upload_headers = reservation.get("upload_headers", {})
            if upload_headers.get("x-ms-blob-type") != "BlockBlob":
                raise RuntimeError("benchmark presign omitted BlockBlob header")
            upload_url = reservation.get("upload_url")
            photo_id = reservation.get("photo_id")
            if not isinstance(upload_url, str) or not isinstance(photo_id, str):
                raise RuntimeError("benchmark presign omitted upload resources")

            upload = _request(
                client,
                request_ids,
                "PUT",
                upload_url,
                headers=upload_headers,
                content=image_bytes,
            )
            _expect(upload, 200, 201)

            selector = index % 4
            payload: dict[str, object] = {
                "photo_id": photo_id,
                "observed_at": datetime.now(UTC).isoformat(),
                "location_source": "none",
            }
            if selector in (0, 3):
                scenario = "unknown_no_location"
                payload["identification_source"] = "unknown"
            elif selector == 1:
                scenario = "catalog_no_location"
                payload.update(
                    {
                        "taxon_id": 9083,
                        "identification_source": "catalog",
                    }
                )
            else:
                scenario = "catalog_coarse_location"
                payload.update(
                    {
                        "taxon_id": 12727,
                        "identification_source": "catalog",
                        "geohash4": "dnp1",
                        "location_source": "manual_coarse",
                    }
                )

            created = _request(
                client,
                request_ids,
                "POST",
                f"{base_url}/v1/observations",
                headers={**auth, "Idempotency-Key": key},
                json=payload,
            )
            _expect(created, 201)
            body = created.json()
            observation_id = body.get("id")
            if not isinstance(observation_id, str) or len(observation_id) != 26:
                raise RuntimeError("benchmark create omitted canonical observation id")
            if body.get("dispatch_status") != "complete":
                raise RuntimeError(f"benchmark dispatch did not complete for scenario {scenario}")
            create_id_list: list[str] = []
            _record_request_id(created, create_id_list)
            if len(create_id_list) != 1:
                raise RuntimeError("benchmark create omitted request id")
            observation_ids.append(observation_id)
            create_request_ids.append(create_id_list[0])
            scenario_counts[scenario] += 1

    return DispatcherBenchmarkSeed(
        result="seeded",
        started_at=started_at.isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        sample_count=sample_count,
        observation_ids=observation_ids,
        create_request_ids=create_request_ids,
        scenario_counts=scenario_counts,
    )


def main() -> None:
    evidence = run_canary(
        base_url=_required("HINTERLAND_API_BASE_URL"),
        bearer=_required("HINTERLAND_SMOKE_BEARER"),
        evidence_path=os.environ.get("HINTERLAND_SMOKE_EVIDENCE_PATH") or None,
    )
    print(
        "W1 canary passed: BlockBlob upload, conflict-safe exactly-one replay, "
        f"{evidence.child_presentation_status}, minimized child DTO, and no "
        "signed photo URL"
    )


if __name__ == "__main__":
    main()
