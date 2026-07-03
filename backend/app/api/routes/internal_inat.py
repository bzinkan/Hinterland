"""Internal iNat-submit endpoint -- manual / admin retry path only.

The production path under ADR 0010 (Azure) does NOT call this HTTP
route. iNat submission rides on a transactional outbox + Service Bus
worker:

    moderation worker sets `observations.moderation_status='clean'` and
    writes an `inat_submit_outbox` row in the same transaction
        -> after commit, enqueue `{ observation_id }` to Service Bus
           queue `inat-submit`
        -> `dragonfly-inat-submit-worker` Container App (KEDA-scaled)
           dequeues and calls
           `app.inat.submit.submit_observation_to_inat(...)` directly
           under managed identity.
        -> Failures leave the outbox row `pending`; a 15-min replay
           cron (`admin.inat_outbox_replay`) re-enqueues it.

This endpoint is retained for **manual / admin retries and smoke
testing**. It is not on the production trust boundary.

Transitional auth: the router still carries the GCP-era
`require_internal_oidc` dependency. That seam is being moved to an
Azure HMAC signature (Key Vault secret) in a follow-up alongside the
Service Bus consumer; until then the dependency is a soft no-op on
local dev and the route's only callers are operator-driven.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import Settings, get_request_settings
from app.core.internal_auth import require_internal_oidc
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep
from app.inat.client import InatClientDep, InatUnavailable
from app.inat.submit import submit_observation_to_inat

router = APIRouter(
    prefix="/internal/inat",
    tags=["internal"],
    dependencies=[Depends(require_internal_oidc)],
)

log = structlog.get_logger()


class SubmitRequest(BaseModel):
    observation_id: str = Field(..., min_length=1, max_length=26)


class SubmitResponse(BaseModel):
    observation_id: str
    inat_observation_id: int | None
    skipped: bool


@router.post("/submit", response_model=SubmitResponse)
async def submit(
    payload: SubmitRequest,
    session: DbSessionDep,
    inat_client: InatClientDep,
    storage: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> SubmitResponse:
    # COPPA posture (Option B): while the flag is off, NO path may push
    # kid observations to iNat -- including this manual/admin retry
    # route, which previously bypassed the gate entirely.
    if not settings.inat_submit_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="iNat submission is disabled (inat_submit_enabled=false)",
        )

    obs = (
        await session.execute(
            select(models.Observation).where(models.Observation.id == payload.observation_id)
        )
    ).scalar_one_or_none()
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observation not found",
        )

    # Mirror the Service Bus consumer's gate: only observations whose
    # moderation outcome is `clean` may ever reach iNat. The photo.status
    # check below is necessary but not sufficient (an operator retry on a
    # quarantined observation with an approved photo must still skip).
    if obs.moderation_status != "clean":
        log.info(
            "inat.submit.skipped_non_clean_observation",
            observation_id=obs.id,
            moderation_status=obs.moderation_status,
        )
        return SubmitResponse(
            observation_id=obs.id,
            inat_observation_id=None,
            skipped=True,
        )

    # Idempotency: already submitted -> short-circuit. Cloud Tasks
    # redelivery is the common cause; the iNat uuid would also reject
    # a duplicate, but we save the round trip.
    if obs.inat_observation_id is not None:
        return SubmitResponse(
            observation_id=obs.id,
            inat_observation_id=obs.inat_observation_id,
            skipped=True,
        )

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == obs.photo_id))
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if photo.status != "clean":
        # Quarantined / deleted / still-pending -- skip submit; reject /
        # approve flows handle the lifecycle on their own paths.
        log.info(
            "inat.submit.skipped_non_clean_photo",
            observation_id=obs.id,
            photo_status=photo.status,
        )
        return SubmitResponse(
            observation_id=obs.id,
            inat_observation_id=None,
            skipped=True,
        )

    image_bytes = storage.fetch_object_bytes(bucket=photo.bucket, object_name=photo.object_name)

    try:
        result = await submit_observation_to_inat(
            inat_client,
            dragonfly_observation_id=obs.id,
            photo_bytes=image_bytes,
            latitude=obs.latitude,
            longitude=obs.longitude,
            observed_on=obs.created_at,
            taxon_id=obs.taxon_id,
            species_guess=obs.species_name,
        )
    except InatUnavailable as exc:
        log.warning("inat.submit.unavailable", observation_id=obs.id, reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="iNat unavailable",
        ) from exc

    obs.inat_observation_id = result.inat_observation_id
    obs.submitted_to_inat_at = datetime.now(UTC)
    await session.commit()

    log.info(
        "inat.submit.committed",
        observation_id=obs.id,
        inat_observation_id=result.inat_observation_id,
    )
    return SubmitResponse(
        observation_id=obs.id,
        inat_observation_id=result.inat_observation_id,
        skipped=False,
    )
