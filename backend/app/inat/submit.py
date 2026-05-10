"""Submit a clean Dragonfly observation to iNaturalist.

iNat's v1 API needs a two-call dance: create the observation, then attach
the photo. Both happen in this function so the caller (Cloud Tasks task
handler) treats the whole submit as one unit. Either succeed and return
the iNat observation id, or raise InatUnavailable so the task retries.

Idempotency: the iNat `uuid` field is set to our local observation id.
iNat treats two POSTs with the same uuid as a single observation; the
second returns the existing record. So a Cloud Tasks duplicate delivery
won't create a duplicate iNat observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

import httpx
import structlog

from app.inat.client import InatUnavailable

log = structlog.get_logger()


@dataclass(frozen=True)
class InatSubmitResult:
    inat_observation_id: int
    inat_uuid: str


async def submit_observation_to_inat(
    inat_client: httpx.AsyncClient,
    *,
    dragonfly_observation_id: str,
    photo_bytes: bytes,
    latitude: float,
    longitude: float,
    observed_on: datetime,
    taxon_id: int | None = None,
    species_guess: str | None = None,
) -> InatSubmitResult:
    """Push the observation + photo to iNaturalist."""
    # Step 1: create the observation. Use Dragonfly's id as the iNat
    # uuid -- gives idempotency for free under Cloud Tasks redelivery.
    obs_payload: dict[str, object] = {
        "observation": {
            "uuid": dragonfly_observation_id,
            "latitude": latitude,
            "longitude": longitude,
            "observed_on_string": observed_on.isoformat(),
        }
    }
    if taxon_id is not None:
        cast(dict[str, object], obs_payload["observation"])["taxon_id"] = taxon_id
    if species_guess is not None:
        cast(dict[str, object], obs_payload["observation"])["species_guess"] = species_guess

    try:
        obs_res = await inat_client.post("/observations", json=obs_payload)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        log.warning("inat.submit.observation_transport_error", error=str(exc))
        raise InatUnavailable("iNat observation create transport error") from exc

    if obs_res.status_code in (401, 403):
        raise InatUnavailable(f"iNat observation create unauthorized: {obs_res.status_code}")
    if obs_res.status_code >= 500:
        raise InatUnavailable(f"iNat observation create server error: {obs_res.status_code}")
    if obs_res.status_code >= 400:
        # 4xx (validation) -- treat as unavailable so Cloud Tasks retries.
        # If genuinely malformed, the retries exhaust and the task DLQs.
        log.warning(
            "inat.submit.observation_client_error",
            status=obs_res.status_code,
            body=obs_res.text[:200],
        )
        raise InatUnavailable(f"iNat observation create client error: {obs_res.status_code}")

    obs_body = cast(dict[str, object], obs_res.json())
    raw_id = obs_body.get("id")
    if not isinstance(raw_id, int):
        # iNat sometimes wraps response as {"results": [...]} -- handle that shape too.
        results = obs_body.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                raw_id = first.get("id")
    if not isinstance(raw_id, int):
        log.warning("inat.submit.observation_no_id", body=str(obs_body)[:200])
        raise InatUnavailable("iNat observation create returned no id")
    inat_observation_id: int = raw_id

    # Step 2: attach the photo via /v1/observation_photos.
    files = {"file": (f"{dragonfly_observation_id}.jpg", photo_bytes, "image/jpeg")}
    data = {"observation_photo[observation_id]": str(inat_observation_id)}
    try:
        photo_res = await inat_client.post(
            "/observation_photos",
            files=files,
            data=data,
        )
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        log.warning(
            "inat.submit.photo_transport_error",
            inat_observation_id=inat_observation_id,
            error=str(exc),
        )
        # Observation exists in iNat but photo upload failed; raising
        # makes Cloud Tasks retry. The uuid-keyed idempotency on the
        # observation create means the retry won't double-create the obs.
        raise InatUnavailable("iNat photo upload transport error") from exc

    if photo_res.status_code >= 500:
        raise InatUnavailable(f"iNat photo upload server error: {photo_res.status_code}")
    if photo_res.status_code >= 400:
        log.warning(
            "inat.submit.photo_client_error",
            status=photo_res.status_code,
            body=photo_res.text[:200],
        )
        raise InatUnavailable(f"iNat photo upload client error: {photo_res.status_code}")

    log.info(
        "inat.submit.complete",
        dragonfly_observation_id=dragonfly_observation_id,
        inat_observation_id=inat_observation_id,
    )
    return InatSubmitResult(
        inat_observation_id=inat_observation_id,
        inat_uuid=dragonfly_observation_id,
    )
