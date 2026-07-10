"""Privacy-safe Azure photo reservation and signed-read endpoints.

Blob arrival is deliberately not a moderation trigger. A photo enters the
moderation outbox only when ``POST /v1/observations`` commits it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.errors import api_error_detail
from app.core.storage import SignedUrlGenerator, SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1/photos", tags=["photos"])
log = structlog.get_logger()

_PRESIGN_TTL = timedelta(minutes=15)
_PHOTO_GET_TTL = timedelta(minutes=5)

AllowedContentType = Literal["image/jpeg"]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=26,
        max_length=26,
        pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$",
    ),
]


def _idempotency_conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=api_error_detail("idempotency_conflict", message),
    )


class PhotoPresignRequest(BaseModel):
    content_type: AllowedContentType = Field(default="image/jpeg")


class PhotoPresignResponse(BaseModel):
    photo_id: str
    upload_url: str | None
    upload_headers: dict[str, str]
    # One-release compatibility alias for the pre-W1 mobile contract.
    required_headers: dict[str, str]
    object_name: str
    bucket: str
    content_type: str
    expires_at: datetime | None
    attachment_status: str
    observation_id: str | None = None


class PhotoUrlResponse(BaseModel):
    photo_id: str
    url: str
    expires_at: datetime


def _request_hash(payload: PhotoPresignRequest) -> str:
    return sha256(payload.content_type.encode("ascii")).hexdigest()


async def _find_replay(
    session: AsyncSession,
    *,
    user_id: str,
    key: str,
    request_hash: str,
) -> tuple[models.Photo, str | None] | None:
    record = (
        await session.execute(
            select(models.ObservationIdempotency).where(
                models.ObservationIdempotency.user_id == user_id,
                models.ObservationIdempotency.idempotency_key == key,
                models.ObservationIdempotency.operation == "photo_presign",
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise _idempotency_conflict(
            "Idempotency-Key was already used with a different presign request"
        )

    photo = (
        await session.execute(
            select(models.Photo).where(
                models.Photo.id == record.resource_id,
                models.Photo.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if photo is None:
        raise _idempotency_conflict("Idempotency record references a missing photo")

    observation_id = None
    if photo.attachment_status == "attached":
        observation_id = (
            await session.execute(
                select(models.Observation.id).where(models.Observation.photo_id == photo.id)
            )
        ).scalar_one_or_none()
    return photo, observation_id


def _presign_response(
    *,
    photo: models.Photo,
    signer: SignedUrlGenerator,
    upload_url: str | None,
    expires_at: datetime | None,
    observation_id: str | None = None,
) -> PhotoPresignResponse:
    content_type = photo.content_type or "image/jpeg"
    upload_headers = (
        signer.put_required_headers(content_type=content_type) if upload_url is not None else {}
    )
    return PhotoPresignResponse(
        photo_id=photo.id,
        upload_url=upload_url,
        upload_headers=upload_headers,
        required_headers=upload_headers,
        object_name=photo.object_name,
        bucket=photo.bucket,
        content_type=content_type,
        expires_at=expires_at,
        attachment_status=photo.attachment_status,
        observation_id=observation_id,
    )


def _fresh_url(
    photo: models.Photo,
    signer: object,
) -> tuple[str, datetime]:
    # Kept small so the collision/replay path cannot drift from first issue.
    return signer.generate_put_url(  # type: ignore[attr-defined,no-any-return]
        bucket=photo.bucket,
        object_name=photo.object_name,
        content_type=photo.content_type or "image/jpeg",
        expires_in=_PRESIGN_TTL,
    )


@router.post("/presign", response_model=PhotoPresignResponse, status_code=status.HTTP_201_CREATED)
async def presign_photo(
    payload: PhotoPresignRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    signer: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    idempotency_key: IdempotencyKeyHeader = None,
) -> PhotoPresignResponse:
    """Reserve one replay-safe raw upload and return provider-required headers."""
    user_row = await resolve_current_user_row(session, current_user)
    if idempotency_key is None and settings.observation_idempotency_required:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Idempotency-Key is required for photo reservation",
        )
    key = idempotency_key or str(ULID())  # compatibility release for old clients
    request_hash = _request_hash(payload)

    replay = await _find_replay(session, user_id=user_row.id, key=key, request_hash=request_hash)
    if replay is not None:
        photo, observation_id = replay
        if observation_id is not None or photo.attachment_status != "reserved":
            return _presign_response(
                photo=photo,
                signer=signer,
                upload_url=None,
                expires_at=None,
                observation_id=observation_id,
            )
        upload_url, expires_at = _fresh_url(photo, signer)
        return _presign_response(
            photo=photo, signer=signer, upload_url=upload_url, expires_at=expires_at
        )

    photo_id = str(ULID())
    photo = models.Photo(
        id=photo_id,
        user_id=user_row.id,
        bucket=settings.photos_bucket,
        object_name=f"pending/uploads/{photo_id}.jpg",
        status="pending",
        attachment_status="reserved",
        submission_key=key,
        content_type=payload.content_type,
    )
    session.add(photo)
    session.add(
        models.ObservationIdempotency(
            user_id=user_row.id,
            idempotency_key=key,
            operation="photo_presign",
            request_hash=request_hash,
            resource_id=photo_id,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent requests can race the pre-read. The unique ledger row is
        # the gate; after rollback the losing request resolves as a replay.
        await session.rollback()
        replay = await _find_replay(
            session, user_id=user_row.id, key=key, request_hash=request_hash
        )
        if replay is None:
            raise
        photo, observation_id = replay
        if observation_id is not None or photo.attachment_status != "reserved":
            return _presign_response(
                photo=photo,
                signer=signer,
                upload_url=None,
                expires_at=None,
                observation_id=observation_id,
            )

    upload_url, expires_at = _fresh_url(photo, signer)
    log.info(
        "photos.presign.issued",
        photo_id=photo.id,
        user_id=user_row.id,
        object_name=photo.object_name,
        replayed=replay is not None,
    )
    return _presign_response(
        photo=photo, signer=signer, upload_url=upload_url, expires_at=expires_at
    )


async def _adult_manages_group(
    session: AsyncSession,
    *,
    caller_user_id: str,
    observation_group_id: str,
) -> bool:
    """Require an adult-role membership in this observation's group.

    Sharing some unrelated group with the photo owner is not authorization for
    this observation; families and teachers may manage multiple isolated groups.
    """
    membership_id = (
        await session.execute(
            select(models.Membership.id).where(
                models.Membership.user_id == caller_user_id,
                models.Membership.group_id == observation_group_id,
                models.Membership.role.in_({"parent", "teacher"}),
            )
        )
    ).scalar_one_or_none()
    return membership_id is not None


@router.get("/{photo_id}/url", response_model=PhotoUrlResponse)
async def photo_get_url(
    photo_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    signer: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> PhotoUrlResponse:
    del settings  # dependency still validates environment-backed storage config
    user_row = await resolve_current_user_row(session, current_user)
    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == photo_id))
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    observation_access = (
        await session.execute(
            select(
                models.Observation.moderation_status,
                models.Observation.group_id,
                models.Observation.user_id,
            ).where(models.Observation.photo_id == photo.id)
        )
    ).one_or_none()
    if observation_access is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    moderation_status, observation_group_id, observation_user_id = observation_access
    if observation_user_id != photo.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    is_owner = user_row.id == photo.user_id
    is_adult = user_row.role in {"parent", "teacher"}
    adult_manager = is_adult and await _adult_manages_group(
        session,
        caller_user_id=user_row.id,
        observation_group_id=observation_group_id,
    )
    allowed = (
        photo.status == "clean" and moderation_status == "clean" and (is_owner or adult_manager)
    ) or (photo.status == "quarantine" and moderation_status == "quarantine" and adult_manager)
    if not allowed:
        # All lifecycle and authorization denials look absent to prevent ID
        # enumeration and avoid revealing that a peer submitted a photo.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")

    url, expires_at = signer.generate_get_url(
        bucket=photo.bucket,
        object_name=photo.object_name,
        expires_in=_PHOTO_GET_TTL,
    )
    return PhotoUrlResponse(photo_id=photo.id, url=url, expires_at=expires_at)


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_photo(
    photo_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: SignedUrlGeneratorDep,
) -> Response:
    """Idempotently abandon an unattached reservation owned by the caller."""
    user_row = await resolve_current_user_row(session, current_user)
    photo = (
        await session.execute(
            select(models.Photo).where(
                models.Photo.id == photo_id,
                models.Photo.user_id == user_row.id,
            )
        )
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if photo.attachment_status == "attached":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An attached photo cannot be abandoned",
        )
    if photo.attachment_status != "deleted":
        photo.attachment_status = "deleted"
        photo.status = "deleted"
        await session.commit()
    try:
        storage.delete_object(bucket=photo.bucket, object_name=photo.object_name)
    except Exception:
        # The raw blob may never have arrived. The database tombstone is
        # authoritative; the storage lifecycle policy remains a backstop.
        log.warning(
            "photos.abandon.delete_failed",
            photo_id=photo_id,
            object_name=photo.object_name,
            exc_info=True,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
