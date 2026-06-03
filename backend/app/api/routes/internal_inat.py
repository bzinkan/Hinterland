"""Internal iNat-submit endpoint, called by the Cloud Tasks worker.

Production path (when wired):
    moderation worker marks Photo.status='clean' and enqueues a task
        -> Cloud Tasks delivers POST /internal/inat/submit
        -> we load the observation + photo bytes, push to iNat,
           write inat_observation_id + submitted_to_inat_at on the
           Observation row.

Pre-Cloud-Tasks dev path: trigger manually via this endpoint.

Auth: the router carries a `require_internal_oidc` dependency that
verifies a Google-signed OIDC ID token, pins the audience, and gates
by an allowlist of service-account emails. Local dev opts out via
`Settings.require_internal_oidc` returning False; deployed envs fail
closed by default. See `app/core/internal_auth.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

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
) -> SubmitResponse:
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
