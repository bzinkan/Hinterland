"""Observation create endpoint.

`POST /v1/observations` finalizes an observation after the photo has landed
in `pending/`. It is the second leg of the kid's submission flow:

1. Mobile calls `POST /v1/photos/presign` -> gets `photo_id` + signed URL
2. Mobile PUTs the photo bytes to the signed URL (lands in `pending/`)
3. Mobile calls THIS endpoint with `photo_id` + lat/lng + optional taxon
4. The Eventarc-triggered moderation worker (Phase 8) runs out of band
   on the `pending/` finalize event and moves the photo to
   `observations/` or `quarantine/`

The kid sees the celebration on this endpoint's 201, BEFORE moderation.
That's the documented trade-off in `docs/moderation.md` -- we never block
the hot path on the moderation provider.
"""

from __future__ import annotations

from typing import Annotated

import geohash
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from ulid import ULID

from app.core.auth import CurrentUserDep
from app.core.config import Settings, get_request_settings
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1/observations", tags=["observations"])

log = structlog.get_logger()


class ObservationCreateRequest(BaseModel):
    photo_id: str = Field(..., min_length=1, max_length=26)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    taxon_id: int | None = Field(default=None, ge=1)
    species_name: str | None = Field(default=None, max_length=200)
    place_name: str | None = Field(default=None, max_length=200)


class ObservationResponse(BaseModel):
    id: str
    user_id: str
    group_id: str
    photo_id: str
    latitude: float
    longitude: float
    geohash4: str | None
    taxon_id: int | None
    species_name: str | None
    place_name: str | None

    @classmethod
    def from_model(cls, obs: models.Observation) -> ObservationResponse:
        return cls(
            id=obs.id,
            user_id=obs.user_id,
            group_id=obs.group_id,
            photo_id=obs.photo_id,
            latitude=obs.latitude,
            longitude=obs.longitude,
            geohash4=obs.geohash4,
            taxon_id=obs.taxon_id,
            species_name=obs.species_name,
            place_name=obs.place_name,
        )


@router.post(
    "",
    response_model=ObservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_observation(
    payload: ObservationCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ObservationResponse:
    user_row = (
        await session.execute(
            select(models.User).where(models.User.firebase_uid == current_user.uid)
        )
    ).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No Postgres user for this Firebase identity",
        )

    if not current_user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is missing group_id claim",
        )
    group_id = current_user.group_id

    # Photo must exist, belong to this user, and still be pending. The
    # ownership check is in the WHERE clause so a wrong-owner photo_id
    # returns 404 like a missing one (no information leak about IDs).
    photo = (
        await session.execute(
            select(models.Photo).where(
                models.Photo.id == payload.photo_id,
                models.Photo.user_id == user_row.id,
            )
        )
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if photo.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Photo is in status {photo.status}, not pending",
        )

    # Atomic counter bump on the membership row. If the user isn't in this
    # group, RETURNING comes back empty and we 403 before inserting the
    # observation row.
    membership_update = await session.execute(
        update(models.Membership)
        .where(
            models.Membership.user_id == user_row.id,
            models.Membership.group_id == group_id,
        )
        .values(
            observation_count=models.Membership.observation_count + 1,
            last_observed_at=func.now(),
        )
        .returning(models.Membership.id)
    )
    if membership_update.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of the group in their token",
        )

    obs_id = str(ULID())
    geohash4 = geohash.encode(payload.latitude, payload.longitude, precision=4)
    observation = models.Observation(
        id=obs_id,
        user_id=user_row.id,
        group_id=group_id,
        photo_id=payload.photo_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        geohash4=geohash4,
        taxon_id=payload.taxon_id,
        species_name=payload.species_name,
        place_name=payload.place_name,
    )
    session.add(observation)
    await session.commit()
    await session.refresh(observation)

    log.info(
        "observations.created",
        observation_id=obs_id,
        user_id=user_row.id,
        group_id=group_id,
        photo_id=payload.photo_id,
        taxon_id=payload.taxon_id,
        geohash4=geohash4,
    )

    return ObservationResponse.from_model(observation)
