#!/usr/bin/env python3
"""Strict Adult QA canary for clean moderation and private photo access.

The caller supplies a throwaway synthetic kid session in memory. Evidence is
sanitized: it contains bounded request IDs and pass/fail facts, never tokens,
SAS URLs, photos, labels, observation IDs, child text, or locations.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from smoke_observation_w1 import (
    _assert_child_dto_minimized,
    _expect,
    _jpeg,
    _request,
)
from ulid import ULID

_EXPECTED_ORIGIN = "https://qa-api.thehinterlandguide.app"
_EXPECTED_LANE = "adult_qa"


@dataclass(frozen=True)
class AdultQaObservationCanaryEvidence:
    result: str
    request_ids: list[str]
    release_lane_verified: bool
    content_safety_enabled: bool
    public_inaturalist_submission_disabled: bool
    block_blob_header: bool
    idempotent_replay: bool
    field_journal_exactly_once: bool
    child_presentation_status: str
    signed_photo_allowed: bool
    child_dto_minimized: bool
    photo_helper_checked: bool

    def to_public_dict(self) -> dict[str, object]:
        return asdict(self)


def _write_evidence(
    path: str | os.PathLike[str],
    evidence: AdultQaObservationCanaryEvidence,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(evidence.to_public_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _require_lane(response: httpx.Response) -> None:
    if response.headers.get("X-Hinterland-Release-Lane") != _EXPECTED_LANE:
        raise RuntimeError("Adult QA response lane did not match")


def run_adult_qa_canary(
    *,
    base_url: str,
    bearer: str,
    evidence_path: str | os.PathLike[str] | None = None,
) -> AdultQaObservationCanaryEvidence:
    base_url = base_url.strip().rstrip("/")
    bearer = bearer.strip()
    if base_url != _EXPECTED_ORIGIN:
        raise RuntimeError(f"Adult QA canary requires {_EXPECTED_ORIGIN}")
    if not bearer:
        raise RuntimeError("kid bearer is required")

    key = str(ULID())
    auth = {"Authorization": f"Bearer {bearer}"}
    request_ids: list[str] = []
    photo_helper_enabled = False

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        meta = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/meta",
        )
        _expect(meta, 200)
        _require_lane(meta)
        meta_body = meta.json()
        if meta_body.get("release_lane") != _EXPECTED_LANE:
            raise RuntimeError("Adult QA metadata lane did not match")
        capabilities = meta_body.get("capabilities", {})
        integrations = capabilities.get("integrations", {})
        observation_capabilities = capabilities.get("observation", {})
        if integrations.get("content_safety_enabled") is not True:
            raise RuntimeError("Adult QA Content Safety is not configured")
        if integrations.get("public_inat_submission_enabled") is not False:
            raise RuntimeError("public iNaturalist submission is not disabled")
        photo_helper_enabled = observation_capabilities.get("photo_helper_enabled") is True

        presign = _request(
            client,
            request_ids,
            "POST",
            f"{base_url}/v1/photos/presign",
            headers={**auth, "Idempotency-Key": key},
            json={"content_type": "image/jpeg"},
        )
        _expect(presign, 201)
        _require_lane(presign)
        reservation = presign.json()
        upload_headers = reservation.get("upload_headers", {})
        if upload_headers.get("x-ms-blob-type") != "BlockBlob":
            raise RuntimeError("presign omitted x-ms-blob-type: BlockBlob")
        upload_url = reservation.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
            raise RuntimeError("new reservation omitted a secure upload URL")

        upload = _request(
            client,
            request_ids,
            "PUT",
            upload_url,
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
        _require_lane(created)
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
        _require_lane(replay)
        if replay.json().get("id") != observation.get("id"):
            raise RuntimeError("replay returned a different observation")
        if replay.headers.get("Idempotency-Replayed", "").lower() != "true":
            raise RuntimeError("replay omitted Idempotency-Replayed: true")
        if replay.json().get("rewards") != observation.get("rewards"):
            raise RuntimeError("replay returned different persisted rewards")

        listing = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/observations/me?order=observed&limit=50",
            headers=auth,
        )
        _expect(listing, 200)
        _require_lane(listing)
        matches = [
            item
            for item in listing.json().get("items", [])
            if item.get("id") == observation.get("id")
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Field Journal contained {len(matches)} copies")
        _assert_child_dto_minimized(matches[0])

        deadline = time.monotonic() + 300
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
            _require_lane(detail)
            detail_payload = detail.json()
            _assert_child_dto_minimized(detail_payload)
            presentation_status = detail_payload.get("child_presentation_status", "")
            if presentation_status == "clean":
                break
            if presentation_status in {
                "adult_review",
                "failed",
                "pilot_private",
            }:
                raise RuntimeError(f"safe Adult QA fixture entered {presentation_status}")
            if presentation_status not in {"pending", "processing"}:
                raise RuntimeError(f"Adult QA returned unknown child state {presentation_status!r}")
            time.sleep(5)
        if presentation_status != "clean":
            raise RuntimeError(f"Content Safety did not reach clean: {presentation_status}")

        photo_url = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/photos/{reservation['photo_id']}/url",
            headers=auth,
        )
        _expect(photo_url, 200)
        _require_lane(photo_url)
        signed_url = photo_url.json().get("url")
        if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
            raise RuntimeError("clean photo response omitted a secure signed URL")
        photo_bytes = _request(
            client,
            request_ids,
            "GET",
            signed_url,
        )
        _expect(photo_bytes, 200)

        photo_helper_checked = False
        if photo_helper_enabled:
            identify = _request(
                client,
                request_ids,
                "POST",
                f"{base_url}/v1/observations/{observation['id']}/identify",
                headers=auth,
            )
            _expect(identify, 200)
            _require_lane(identify)
            identify_body = identify.json()
            if identify_body.get("cv_unavailable") is not False:
                raise RuntimeError("enabled photo helper was unavailable")
            if not isinstance(identify_body.get("suggestions"), list):
                raise RuntimeError("photo helper returned malformed suggestions")
            photo_helper_checked = True

        final_listing = _request(
            client,
            request_ids,
            "GET",
            f"{base_url}/v1/observations/me?order=observed&limit=50",
            headers=auth,
        )
        _expect(final_listing, 200)
        _require_lane(final_listing)
        final_matches = [
            item
            for item in final_listing.json().get("items", [])
            if item.get("id") == observation.get("id")
        ]
        if len(final_matches) != 1:
            raise RuntimeError("clean Field Journal record was not stable")
        _assert_child_dto_minimized(final_matches[0])
        if final_matches[0].get("child_presentation_status") != "clean":
            raise RuntimeError("Field Journal did not expose clean presentation")

    evidence = AdultQaObservationCanaryEvidence(
        result="passed",
        request_ids=request_ids,
        release_lane_verified=True,
        content_safety_enabled=True,
        public_inaturalist_submission_disabled=True,
        block_blob_header=True,
        idempotent_replay=True,
        field_journal_exactly_once=True,
        child_presentation_status="clean",
        signed_photo_allowed=True,
        child_dto_minimized=True,
        photo_helper_checked=photo_helper_checked,
    )
    if evidence_path:
        _write_evidence(evidence_path, evidence)
    return evidence


def main() -> int:
    try:
        run_adult_qa_canary(
            base_url=os.environ.get("HINTERLAND_API_BASE_URL", _EXPECTED_ORIGIN),
            bearer=os.environ.get("HINTERLAND_SMOKE_BEARER", ""),
            evidence_path=os.environ.get("HINTERLAND_SMOKE_EVIDENCE_PATH") or None,
        )
    except Exception as exc:
        print(f"Adult QA Observation canary failed: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
