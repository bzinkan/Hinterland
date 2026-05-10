"""Observation routes.

`POST /v1/observations` finalizes an observation after the photo has landed
in `pending/`. It is the second leg of the kid's submission flow:

1. Mobile calls `POST /v1/photos/presign` -> gets `photo_id` + signed URL
2. Mobile PUTs the photo bytes to the signed URL (lands in `pending/`)
3. Mobile calls `POST /v1/observations` with `photo_id` + lat/lng + taxon
4. The Eventarc-triggered moderation worker (Phase 8) runs out of band on
   the `pending/` finalize event and moves the photo to `observations/`
   or `quarantine/`

The kid sees the celebration on the create 201, BEFORE moderation. That's
the documented trade-off in `docs/moderation.md` -- we never block the
hot path on the moderation provider.

`GET /v1/observations/me` returns the current user's observations, newest
first, paginated by ULID cursor (`before=<id>`). Photo bytes themselves
are fetched via a separate signed-GET endpoint (later slice); this list
returns only metadata + the underlying `photo` bucket key so the client
knows what to request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import geohash
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, update
from ulid import ULID

from app.core.auth import CurrentUserDep
from app.core.config import Settings, get_request_settings
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep
from app.inat.client import InatClientDep, InatUnavailable
from app.inat.cv import score_image

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


# ---------------------------------------------------------------------------
# GET /v1/observations/me
# ---------------------------------------------------------------------------

# Page size bounds. 50 is the largest single batch we'll ever serve to the
# kid app -- the Dex tab uses FlashList virtualization (per docs/mobile.md)
# and pages by 20 by default. Higher caps invite accidental N+1 fetches.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


class ObservationListItem(BaseModel):
    """Single observation in the list response.

    Includes enough photo metadata that the client can render a placeholder
    + request a signed GET URL on demand. The signed URL endpoint is a
    follow-up slice; we deliberately don't bake URLs into the list because
    they'd expire mid-scroll.
    """

    id: str
    user_id: str
    group_id: str
    photo_id: str
    photo_object_name: str
    photo_status: str
    latitude: float
    longitude: float
    geohash4: str | None
    taxon_id: int | None
    species_name: str | None
    place_name: str | None
    created_at: datetime


class ObservationListResponse(BaseModel):
    items: list[ObservationListItem]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `before` to fetch the next page. Null when this is the last page."
        ),
    )


@router.get("/me", response_model=ObservationListResponse)
async def list_my_observations(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    before: Annotated[str | None, Query(min_length=26, max_length=26)] = None,
) -> ObservationListResponse:
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

    # ULIDs are lex-sortable AND time-sortable, so DESC on id gives newest
    # first without a separate created_at index. Cursor is just the last id
    # we returned; "give me rows older than this".
    stmt = (
        select(models.Observation, models.Photo)
        .join(models.Photo, models.Observation.photo_id == models.Photo.id)
        .where(models.Observation.user_id == user_row.id)
    )
    if before is not None:
        stmt = stmt.where(models.Observation.id < before)
    stmt = stmt.order_by(desc(models.Observation.id)).limit(limit + 1)

    rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        ObservationListItem(
            id=obs.id,
            user_id=obs.user_id,
            group_id=obs.group_id,
            photo_id=obs.photo_id,
            photo_object_name=photo.object_name,
            photo_status=photo.status,
            latitude=obs.latitude,
            longitude=obs.longitude,
            geohash4=obs.geohash4,
            taxon_id=obs.taxon_id,
            species_name=obs.species_name,
            place_name=obs.place_name,
            created_at=obs.created_at,
        )
        for obs, photo in page
    ]

    return ObservationListResponse(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
    )


# ---------------------------------------------------------------------------
# POST /v1/observations/{id}/identify
# ---------------------------------------------------------------------------


class CvSuggestionDTO(BaseModel):
    taxon_id: int
    common_name: str | None
    scientific_name: str | None
    score: float


class IdentifyResponse(BaseModel):
    """Top-K iNat CV suggestions for the observation's photo.

    `cv_unavailable` is `true` when iNat couldn't be reached (network,
    5xx, no token configured). The kid still proceeds via manual species
    selection -- this is the documented graceful-degradation contract from
    `docs/architecture.md`.
    """

    observation_id: str
    suggestions: list[CvSuggestionDTO]
    cv_unavailable: bool = False


@router.post("/{observation_id}/identify", response_model=IdentifyResponse)
async def identify_observation(
    observation_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    inat_client: InatClientDep,
    storage: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> IdentifyResponse:
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

    # Owner check is in the WHERE clause -- wrong owner returns 404 like
    # missing, no enumeration leak.
    obs_photo = (
        await session.execute(
            select(models.Observation, models.Photo)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
            )
        )
    ).one_or_none()
    if obs_photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    _obs, photo = obs_photo

    # No iNat token configured (dev / CI) -> immediate cv_unavailable.
    # Avoids a guaranteed 401 round-trip + lets the mobile UI flip to
    # manual-pick mode without any latency.
    if not settings.inat_oauth_token:
        log.info(
            "observations.identify.cv_unavailable_no_token",
            observation_id=observation_id,
        )
        return IdentifyResponse(
            observation_id=observation_id,
            suggestions=[],
            cv_unavailable=True,
        )

    image_bytes = storage.fetch_object_bytes(bucket=photo.bucket, object_name=photo.object_name)

    try:
        suggestions = await score_image(inat_client, image_bytes=image_bytes, top_k=3)
    except InatUnavailable as exc:
        log.warning(
            "observations.identify.cv_unavailable",
            observation_id=observation_id,
            reason=str(exc),
        )
        return IdentifyResponse(
            observation_id=observation_id,
            suggestions=[],
            cv_unavailable=True,
        )

    log.info(
        "observations.identify.scored",
        observation_id=observation_id,
        suggestion_count=len(suggestions),
    )
    return IdentifyResponse(
        observation_id=observation_id,
        suggestions=[
            CvSuggestionDTO(
                taxon_id=s.taxon_id,
                common_name=s.common_name,
                scientific_name=s.scientific_name,
                score=s.score,
            )
            for s in suggestions
        ],
    )
