"""Submit a clean Hinterland observation to iNaturalist.

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


# Child-location privacy posture (owner decision 2026-07-03) -- hardcoded
# on purpose: a config knob whose misconfiguration leaks kid coordinates
# is pure downside, and `inat_submit_enabled` already gates whether
# submission happens at all.
#
# Two independent layers:
# - geoprivacy "obscured": iNat scrambles the PUBLIC display point within
#   a ~0.2-degree cell. Trusted users / curators can still see submitted
#   coordinates, which is why we ALSO...
# - round the submitted coordinates to 2 decimal places (~1.1 km): the
#   true point never leaves our system at full precision.
#
# Observation finalization supplies a canonical server-reencoded JPEG, so
# metadata is stripped before this separately gated egress path can run.
_GEOPRIVACY = "obscured"
_COORD_DECIMAL_PLACES = 2


async def submit_observation_to_inat(
    inat_client: httpx.AsyncClient,
    *,
    dragonfly_observation_id: str,
    photo_bytes: bytes,
    latitude: float | None,
    longitude: float | None,
    observed_on: datetime,
    taxon_id: int | None = None,
    species_guess: str | None = None,
    egress_enabled: bool = False,
) -> InatSubmitResult:
    """Push the observation + photo to iNaturalist."""
    if not egress_enabled:
        log.warning(
            "inat.submit.blocked_by_kill_switch",
            dragonfly_observation_id=dragonfly_observation_id,
        )
        raise InatUnavailable("iNat observation/photo egress is disabled")
    if (latitude is None) != (longitude is None):
        raise InatUnavailable("observation has an incomplete location pair")

    # Step 1: create the observation. Use Hinterland's id as the iNat
    # uuid -- gives idempotency for free under Cloud Tasks redelivery.
    observation_payload: dict[str, object] = {
        "uuid": dragonfly_observation_id,
        "observed_on_string": observed_on.isoformat(),
    }
    if latitude is not None and longitude is not None:
        observation_payload.update(
            latitude=round(latitude, _COORD_DECIMAL_PLACES),
            longitude=round(longitude, _COORD_DECIMAL_PLACES),
            geoprivacy=_GEOPRIVACY,
        )
    obs_payload: dict[str, object] = {"observation": observation_payload}
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
