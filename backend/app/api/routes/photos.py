"""Photo upload endpoints.

`POST /v1/photos/presign` issues an Azure Blob user-delegation SAS PUT URL
for a single image landing at `pending/<photo_id>.jpg` in the photos
container. The mobile client PUTs the image bytes to that URL sending
exactly the `required_headers` from the response (Azure Put Blob rejects
the request without `x-ms-blob-type: BlockBlob`), then calls
`POST /v1/observations` with the returned `photo_id`. Moderation runs out
of band via the Service Bus pipeline (see `docs/moderation.md`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1/photos", tags=["photos"])

log = structlog.get_logger()

# 15 minutes is generous for the offline-then-upload path while keeping the
# signed-URL exposure window bounded.
_PRESIGN_TTL = timedelta(minutes=15)

AllowedContentType = Literal["image/jpeg"]


class PhotoPresignRequest(BaseModel):
    content_type: AllowedContentType = Field(default="image/jpeg")


class PhotoPresignResponse(BaseModel):
    photo_id: str
    upload_url: str
    object_name: str
    bucket: str
    content_type: str
    expires_at: datetime
    # Headers the client must send verbatim on the PUT. Storage-backend
    # specific (Azure requires x-ms-blob-type); clients should treat this
    # as opaque rather than hardcoding header names.
    required_headers: dict[str, str]


@router.post(
    "/presign",
    response_model=PhotoPresignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def presign_photo(
    payload: PhotoPresignRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    signer: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> PhotoPresignResponse:
    user_row = await resolve_current_user_row(session, current_user)

    photo_id = str(ULID())
    object_name = f"pending/{photo_id}.jpg"
    bucket = settings.photos_bucket

    upload_url, expires_at = signer.generate_put_url(
        bucket=bucket,
        object_name=object_name,
        content_type=payload.content_type,
        expires_in=_PRESIGN_TTL,
    )

    photo = models.Photo(
        id=photo_id,
        user_id=user_row.id,
        bucket=bucket,
        object_name=object_name,
        status="pending",
        content_type=payload.content_type,
    )
    session.add(photo)
    await session.commit()

    log.info(
        "photos.presign.issued",
        photo_id=photo_id,
        user_id=user_row.id,
        bucket=bucket,
        object_name=object_name,
        ttl_seconds=int(_PRESIGN_TTL.total_seconds()),
    )

    return PhotoPresignResponse(
        photo_id=photo_id,
        upload_url=upload_url,
        object_name=object_name,
        bucket=bucket,
        content_type=payload.content_type,
        expires_at=expires_at,
        required_headers=signer.put_required_headers(content_type=payload.content_type),
    )


# ---------------------------------------------------------------------------
# GET /v1/photos/{photo_id}/url -- short-lived signed GET for rendering
# ---------------------------------------------------------------------------

# 5 minutes is enough for the mobile client to render + cache the image.
# Shorter than presign because GET is read-only and can be re-issued
# cheaply.
_PHOTO_GET_TTL = timedelta(minutes=5)


class PhotoUrlResponse(BaseModel):
    photo_id: str
    url: str
    expires_at: datetime


# Same trust model as the review queue: parent/teacher memberships are
# the adult review boundary.
_ADULT_ROLES = frozenset({"parent", "teacher"})


async def _shares_group(
    session: AsyncSession,
    *,
    caller_user_id: str,
    photo_owner_id: str,
    caller_adult_membership_only: bool,
) -> bool:
    """True if caller and photo owner share a group.

    With ``caller_adult_membership_only`` the caller's side only counts
    groups where THEIR membership role is parent/teacher -- the
    review-queue trust model (a kid group-mate must never unlock another
    kid's unmoderated or quarantined photo)."""
    rows = (
        await session.execute(
            select(
                models.Membership.user_id,
                models.Membership.group_id,
                models.Membership.role,
            ).where(models.Membership.user_id.in_([caller_user_id, photo_owner_id]))
        )
    ).all()
    caller_groups: set[str] = set()
    owner_groups: set[str] = set()
    for uid, gid, role in rows:
        if uid == photo_owner_id:
            owner_groups.add(gid)
        if uid == caller_user_id:
            if caller_adult_membership_only and role not in _ADULT_ROLES:
                continue
            caller_groups.add(gid)
    return bool(caller_groups & owner_groups)


@router.get("/{photo_id}/url", response_model=PhotoUrlResponse)
async def photo_get_url(
    photo_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    signer: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> PhotoUrlResponse:
    user_row = await resolve_current_user_row(session, current_user)

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == photo_id))
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if photo.status == "deleted":
        # Rejected photos are gone from every legitimate surface (the kid
        # gallery renders "removed", teacher review drops resolved items)
        # and the blob may already be purged -- don't mint working URLs
        # for them. 404 like missing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    if user_row.id != photo.user_id:
        # Non-owner access. Moderation-passed (`clean`) photos are
        # group-shared; anything NOT clean (unmoderated `pending`,
        # flagged `quarantine`) is adult-review material: the caller
        # must be a parent/teacher AND hold an adult-role membership in
        # a group shared with the owner. Kids always see their OWN
        # photos (any non-deleted status) via the owner check above.
        # All failures 404 like missing -- no enumeration leak.
        adult_only = photo.status != "clean"
        if adult_only and user_row.role not in _ADULT_ROLES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
        if not await _shares_group(
            session,
            caller_user_id=user_row.id,
            photo_owner_id=photo.user_id,
            caller_adult_membership_only=adult_only,
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    url, expires_at = signer.generate_get_url(
        bucket=photo.bucket,
        object_name=photo.object_name,
        expires_in=_PHOTO_GET_TTL,
    )
    return PhotoUrlResponse(photo_id=photo.id, url=url, expires_at=expires_at)
