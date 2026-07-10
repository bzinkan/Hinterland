"""Observation routes.

`POST /v1/observations` finalizes an observation after the photo has landed
in `pending/uploads/`. It is the second leg of the kid's submission flow:

1. Mobile calls `POST /v1/photos/presign` with a submission ULID and receives
   `photo_id`, a signed URL, and provider-required upload headers.
2. Mobile PUTs the photo bytes to `pending/uploads/` using those headers.
3. Mobile calls `POST /v1/observations` with the same ULID, optional geohash-4,
   observed time, and catalog/manual/Unknown identification.
4. The API verifies and canonicalizes the JPEG under `pending/finalized/`,
   atomically attaches it, saves the observation/counter/work ledgers, then
   runs durable reward dispatch outside the base-save transaction.
5. The outbox-driven moderation worker later records `pilot_private`, moves a
   clean photo to `observations/`, or moves a flagged photo to `quarantine/`.

The kid sees the celebration on the create 201, BEFORE moderation. That's
the documented trade-off in `docs/moderation.md` -- we never block the
hot path on the moderation provider.

`GET /v1/observations/me` returns the current user's observations, newest
first, paginated by ULID cursor (`before=<id>`). Photo bytes themselves
are fetched through the separate lifecycle- and role-gated signed-GET endpoint;
this list returns metadata only.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import desc, func, select, text, update
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.errors import api_error_detail
from app.core.geospatial import encode_geohash, normalize_geohash4
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep
from app.derived_state.rebuild import acquire_user_lock, enqueue_rebuild
from app.dispatcher.core import dispatch
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context, Reward
from app.inat.client import InatClientDep, InatUnavailable
from app.inat.cv import score_image
from app.models.ecology_tags import normalize_ecology_tags
from app.observation.photo_finalize import (
    PhotoUploadMissing,
    PhotoValidationError,
    finalize_uploaded_photo,
)

router = APIRouter(prefix="/v1/observations", tags=["observations"])

log = structlog.get_logger()


def _idempotency_conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=api_error_detail("idempotency_conflict", message),
    )


class ObservationCreateRequest(BaseModel):
    photo_id: str = Field(..., min_length=1, max_length=26)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    geohash4: str | None = Field(default=None, min_length=4, max_length=4)
    observed_at: datetime | None = None
    location_source: Literal["device_coarse", "manual_coarse", "none"] | None = None
    taxon_id: int | None = Field(default=None, ge=1)
    species_name: str | None = Field(default=None, max_length=200)
    identification_source: Literal["catalog", "manual_text", "unknown"] | None = None
    place_name: str | None = Field(default=None, max_length=200)
    ecology_tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("ecology_tags", mode="before")
    @classmethod
    def ecology_tags_are_closed_choice(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return value
        return normalize_ecology_tags(value)

    @model_validator(mode="after")
    def validate_location(self) -> ObservationCreateRequest:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.geohash4 is not None:
            self.geohash4 = normalize_geohash4(self.geohash4)
        if self.geohash4 is not None and self.latitude is not None:
            raise ValueError("send geohash4 or legacy coordinates, not both")
        if self.location_source == "none" and (
            self.geohash4 is not None or self.latitude is not None
        ):
            raise ValueError("location_source=none cannot include a location")
        if self.location_source in {"device_coarse", "manual_coarse"} and (
            self.geohash4 is None and self.latitude is None
        ):
            raise ValueError("a coarse location source requires geohash4")
        manual_name = (self.species_name or "").strip()
        expected_source = (
            "catalog" if self.taxon_id is not None else "manual_text" if manual_name else "unknown"
        )
        if self.identification_source is not None and self.identification_source != expected_source:
            raise ValueError("identification_source does not match the selected identification")
        return self


class RewardDTO(BaseModel):
    """Public shape of a dispatcher Reward over the API.

    Mirrors `app.dispatcher.types.Reward`. The mobile celebration sequence
    sorts these by `weight` desc and renders one at a time.
    """

    type: str
    title: str
    detail: str
    icon: str
    weight: int
    payload: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_reward(cls, reward: Reward) -> RewardDTO:
        return cls(
            type=reward.type,
            title=reward.title,
            detail=reward.detail,
            icon=reward.icon,
            weight=reward.weight,
            payload=dict(reward.payload),
        )


ChildPresentationStatus = Literal[
    "clean",
    "pending",
    "processing",
    "pilot_private",
    "adult_review",
    "failed",
]


def derive_child_presentation_status(
    observation_status: str | None,
    photo_status: str | None,
    *,
    photo_attachment_status: str | None = None,
    revocation_active: bool = False,
) -> ChildPresentationStatus:
    """Return the only lifecycle state a child surface may interpret.

    A clean image requires matching clean database state and no active
    revocation. Every unknown or mismatched pair collapses to ``failed``, a
    metadata-only state. Rejected/deleted rows are filtered by the owning
    query; mapping them here still fails closed for callers that make a stale
    decision.
    """

    if revocation_active:
        return "failed"
    if photo_status is not None and photo_attachment_status != "attached":
        return "failed"
    if observation_status == "failed" and photo_status != "deleted":
        return "failed"
    if photo_status is None:
        # Create/replay responses already have an authoritative Observation
        # lifecycle but do not reload the joined Photo. Restrictive states may
        # be represented without granting image access; clean may not.
        restrictive_statuses: dict[str, ChildPresentationStatus] = {
            "pending": "pending",
            "processing": "processing",
            "pilot_private": "pilot_private",
            "quarantine": "adult_review",
        }
        return restrictive_statuses.get(observation_status or "", "failed")

    expected_pairs: dict[tuple[str, str], ChildPresentationStatus] = {
        ("pending", "pending"): "pending",
        ("processing", "pending"): "processing",
        ("pilot_private", "pending"): "pilot_private",
        ("quarantine", "quarantine"): "adult_review",
        ("clean", "clean"): "clean",
    }
    return expected_pairs.get((observation_status or "", photo_status or ""), "failed")


class ObservationResponse(BaseModel):
    id: str
    user_id: str
    group_id: str
    photo_id: str
    geohash4: str | None
    observed_at: datetime | None
    location_source: str
    taxon_id: int | None
    species_name: str | None
    identification_source: str
    identification_revision: int
    place_name: str | None
    ecology_tags: dict[str, str] = Field(default_factory=dict)
    child_presentation_status: ChildPresentationStatus
    dispatch_status: str
    rewards: list[RewardDTO] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        obs: models.Observation,
        rewards: list[Reward] | None = None,
        *,
        photo: models.Photo | None = None,
        revocation_active: bool = False,
    ) -> ObservationResponse:
        return cls(
            id=obs.id,
            user_id=obs.user_id,
            group_id=obs.group_id,
            photo_id=obs.photo_id,
            geohash4=obs.geohash4,
            observed_at=getattr(obs, "observed_at", None),
            location_source=getattr(obs, "location_source", None) or "legacy_coarsened",
            taxon_id=obs.taxon_id,
            species_name=obs.species_name,
            identification_source=getattr(obs, "identification_source", None) or "legacy",
            identification_revision=getattr(obs, "identification_revision", None) or 1,
            place_name=obs.place_name,
            ecology_tags=dict(obs.ecology_tags or {}),
            child_presentation_status=derive_child_presentation_status(
                getattr(obs, "moderation_status", None),
                getattr(photo, "status", None),
                photo_attachment_status=getattr(photo, "attachment_status", None),
                revocation_active=revocation_active,
            ),
            dispatch_status=getattr(obs, "dispatch_status", None) or "unverified",
            rewards=(
                [RewardDTO.from_reward(r) for r in rewards]
                if rewards is not None
                else [RewardDTO.model_validate(r) for r in (getattr(obs, "rewards", None) or [])]
            ),
        )


async def _persisted_observation_response(
    session: DbSessionDep,
    observation: models.Observation,
    rewards: list[Reward] | None = None,
) -> ObservationResponse:
    """Load lifecycle evidence before deriving a child presentation state."""

    row = (
        await session.execute(
            select(models.Photo, models.PhotoRevocation)
            .outerjoin(
                models.PhotoRevocation,
                models.PhotoRevocation.photo_id == models.Photo.id,
            )
            .where(models.Photo.id == observation.photo_id)
        )
    ).one_or_none()
    if row is None:
        return ObservationResponse.from_model(observation, rewards=rewards)
    photo, revocation = row
    return ObservationResponse.from_model(
        observation,
        rewards=rewards,
        photo=photo,
        revocation_active=revocation is not None,
    )


async def _catalog_species(session: DbSessionDep, taxon_id: int) -> models.SpeciesCache:
    """Resolve a canonical local taxon without a kid-request iNat fallback."""
    row = (
        await session.execute(
            select(models.SpeciesCache).where(
                models.SpeciesCache.taxon_id == taxon_id,
                models.SpeciesCache.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select a taxon from the current Hinterland catalog",
        )
    return row


IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=26,
        max_length=26,
        pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$",
    ),
]


def _normalized_location(payload: ObservationCreateRequest) -> tuple[str | None, str]:
    if payload.geohash4 is not None:
        return payload.geohash4, payload.location_source or "device_coarse"
    if payload.latitude is not None and payload.longitude is not None:
        return encode_geohash(payload.latitude, payload.longitude, precision=4), "legacy_coarsened"
    return None, "none"


def _normalized_observed_at(value: datetime | None) -> datetime:
    now = datetime.now(UTC)
    if value is None:
        return now
    if value.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="observed_at must include a timezone",
        )
    value = value.astimezone(UTC)
    if value > now + timedelta(minutes=5) or value < now - timedelta(days=30):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="observed_at must be within the last 30 days and at most 5 minutes ahead",
        )
    return value


def _create_request_hash(
    payload: ObservationCreateRequest,
    *,
    geohash4: str | None,
    location_source: str,
    observed_at: datetime | None = None,
) -> str:
    normalized = {
        "photo_id": payload.photo_id,
        "observed_at": observed_at.isoformat() if payload.observed_at and observed_at else None,
        "geohash4": geohash4,
        "location_source": location_source,
        "taxon_id": payload.taxon_id,
        "identification_source": (
            "catalog"
            if payload.taxon_id is not None
            else "manual_text"
            if (payload.species_name or "").strip()
            else "unknown"
        ),
        "species_name": (
            None if payload.taxon_id is not None else (payload.species_name or "").strip() or None
        ),
        "place_name": (payload.place_name or "").strip() or None,
        "ecology_tags": payload.ecology_tags,
    }
    return sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _create_replay(
    session: DbSessionDep,
    *,
    user_id: str,
    key: str,
    request_hash: str,
) -> models.Observation | None:
    record = (
        await session.execute(
            select(models.ObservationIdempotency).where(
                models.ObservationIdempotency.user_id == user_id,
                models.ObservationIdempotency.idempotency_key == key,
                models.ObservationIdempotency.operation == "observation_create",
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise _idempotency_conflict(
            "Idempotency-Key was already used with a different observation request"
        )
    observation = (
        await session.execute(
            select(models.Observation)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .where(
                models.Observation.id == record.resource_id,
                models.Observation.user_id == user_id,
                models.Observation.rejected_at.is_(None),
                models.Observation.moderation_status != "rejected",
                models.Photo.status != "deleted",
                models.Photo.attachment_status != "deleted",
            )
        )
    ).scalar_one_or_none()
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    return observation


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
    response: Response,
    storage: SignedUrlGeneratorDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ObservationResponse:
    user_row = await resolve_current_user_row(session, current_user)
    # Rollback expires ORM instances even when expire_on_commit is disabled.
    # Keep the stable identity scalar for idempotency recovery and logging.
    user_id = user_row.id

    if idempotency_key is None and settings.observation_idempotency_required:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Idempotency-Key is required for observation finalization",
        )

    if not current_user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is missing group_id claim",
        )
    group_id = current_user.group_id

    geohash4, location_source = _normalized_location(payload)
    observed_at = _normalized_observed_at(payload.observed_at)
    request_hash = _create_request_hash(
        payload,
        geohash4=geohash4,
        location_source=location_source,
        observed_at=observed_at,
    )
    if idempotency_key is not None:
        replay = await _create_replay(
            session,
            user_id=user_id,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotency-Replayed"] = "true"
            return await _persisted_observation_response(session, replay)

    photo = (
        await session.execute(
            select(models.Photo).where(
                models.Photo.id == payload.photo_id,
                models.Photo.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if photo.attachment_status != "reserved" or photo.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Photo is not an available reservation",
        )

    key = idempotency_key or photo.submission_key or str(ULID())
    if photo.submission_key is not None and photo.submission_key != key:
        raise _idempotency_conflict(
            "Observation Idempotency-Key does not match the photo reservation"
        )
    if idempotency_key is None:
        replay = await _create_replay(
            session,
            user_id=user_id,
            key=key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotency-Replayed"] = "true"
            return await _persisted_observation_response(session, replay)

    species_name = (payload.species_name or "").strip() or None
    identification_source = "manual_text" if species_name else "unknown"
    if payload.taxon_id is not None:
        catalog_row = await _catalog_species(session, payload.taxon_id)
        species_name = catalog_row.common_name or catalog_row.scientific_name
        identification_source = "catalog"

    # Storage verification deliberately happens before the database transaction:
    # decoding is bounded but still too slow to hold a per-user advisory lock.
    # The immutable canonical object is retry-safe; a failed transaction leaves
    # only an orphan that the 24-hour lifecycle/sweeper may remove.
    raw_object_name = photo.object_name
    try:
        canonical_photo = await finalize_uploaded_photo(
            storage,
            bucket=photo.bucket,
            raw_object_name=raw_object_name,
            photo_id=photo.id,
        )
    except PhotoUploadMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The photo upload has not finished",
        ) from exc
    except PhotoValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
        {"user_id": user_id},
    )

    # A concurrent finalize with the same key may have completed while this
    # request verified the Blob. Reconcile under the advisory lock before any
    # counters or derived state are changed.
    replay = await _create_replay(
        session,
        user_id=user_id,
        key=key,
        request_hash=request_hash,
    )
    if replay is not None:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotency-Replayed"] = "true"
        return await _persisted_observation_response(session, replay)

    locked_photo = (
        await session.execute(
            select(models.Photo)
            .where(
                models.Photo.id == payload.photo_id,
                models.Photo.user_id == user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked_photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    if locked_photo.attachment_status != "reserved" or locked_photo.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Photo is not an available reservation",
        )
    if locked_photo.submission_key is not None and locked_photo.submission_key != key:
        raise _idempotency_conflict(
            "Observation Idempotency-Key does not match the photo reservation"
        )
    photo = locked_photo

    membership_update = await session.execute(
        update(models.Membership)
        .where(
            models.Membership.user_id == user_id,
            models.Membership.group_id == group_id,
        )
        .values(
            observation_count=models.Membership.observation_count + 1,
            last_observed_at=func.greatest(
                models.Membership.last_observed_at,
                observed_at,
            ),
        )
        .returning(models.Membership.id)
    )
    if membership_update.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of the group in their token",
        )

    obs_id = str(ULID())
    observation = models.Observation(
        id=obs_id,
        user_id=user_id,
        group_id=group_id,
        photo_id=payload.photo_id,
        submission_key=key,
        latitude=None,
        longitude=None,
        geohash4=geohash4,
        observed_at=observed_at,
        location_source=location_source,
        taxon_id=payload.taxon_id,
        species_name=species_name,
        identification_source=identification_source,
        identification_revision=1,
        taxon_first_assigned_at=observed_at if payload.taxon_id is not None else None,
        place_name=(payload.place_name or "").strip() or None,
        ecology_tags=payload.ecology_tags,
        dispatch_status="pending",
        moderation_status="pending",
        moderation_source="none",
    )
    photo.attachment_status = "attached"
    photo.submission_key = key
    photo.object_name = canonical_photo.object_name
    photo.canonical_object_name = canonical_photo.object_name
    photo.content_type = "image/jpeg"
    photo.checksum = canonical_photo.sha256
    photo.byte_count = canonical_photo.byte_count
    photo.width_px = canonical_photo.width_px
    photo.height_px = canonical_photo.height_px
    photo.sha256 = canonical_photo.sha256
    photo.verified_at = canonical_photo.verified_at
    session.add(observation)
    try:
        # The dependent ledgers carry scalar foreign keys rather than ORM
        # relationships. Flush the parent Observation first so PostgreSQL can
        # enforce every dependent FK while preserving one atomic transaction.
        await session.flush()
        session.add(
            models.ObservationIdempotency(
                user_id=user_id,
                idempotency_key=key,
                operation="observation_create",
                request_hash=request_hash,
                resource_id=obs_id,
            )
        )
        session.add(
            models.ModerationOutbox(
                observation_id=obs_id,
                photo_id=photo.id,
                status="pending",
            )
        )
        for handler in HANDLERS:
            session.add(
                models.ObservationHandlerRun(
                    observation_id=obs_id,
                    handler_name=handler.name,
                    handler_version=str(getattr(handler, "version", "1")),
                    status="pending",
                    state={},
                    rewards=[],
                    attempt_count=0,
                )
            )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        replay = await _create_replay(
            session,
            user_id=user_id,
            key=key,
            request_hash=request_hash,
        )
        if replay is None:
            raise
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotency-Replayed"] = "true"
        return await _persisted_observation_response(session, replay)

    # Everything represented by this DTO is durable after the base commit.
    # Keep an immutable fallback before any dispatcher I/O so a broken
    # connection during dispatch *or recovery* can never turn a successfully
    # saved observation into a 500 or expose dirty, uncommitted ORM state.
    saved_response = ObservationResponse.from_model(observation, photo=photo)

    # The canonical object is now the sole database reference. Raw cleanup is
    # best-effort so a transient Blob failure can never roll back the save.
    if raw_object_name != canonical_photo.object_name:
        try:
            await asyncio.to_thread(
                storage.delete_object,
                bucket=photo.bucket,
                object_name=raw_object_name,
            )
        except Exception:
            log.warning(
                "observations.raw_upload_cleanup_failed",
                observation_id=obs_id,
                photo_id=photo.id,
            )

    log.info(
        "observations.created",
        observation_id=obs_id,
        user_id=user_id,
        group_id=group_id,
        photo_id=payload.photo_id,
        taxon_id=payload.taxon_id,
        geohash4=geohash4,
    )

    rewards: list[Reward] = []
    try:
        group = (
            await session.execute(select(models.Group).where(models.Group.id == group_id))
        ).scalar_one_or_none()
        ctx = Context(
            db=session,
            user=user_row,
            group=group,
            observation=observation,
            photo=photo,
        )
        rewards = await dispatch(ctx, HANDLERS)
    except Exception:
        log.exception(
            "observations.dispatch_failed",
            observation_id=obs_id,
        )
        # An infrastructure-level dispatcher failure may leave ORM objects
        # carrying savepoint/ledger mutations that were never committed. Try
        # to recover the persisted state, but a severed connection can make
        # rollback/refresh fail too. In that case return the pre-dispatch DTO
        # immediately, without another database call.
        try:
            await session.rollback()
            await session.refresh(observation)
        except Exception:
            log.exception(
                "observations.dispatch_recovery_failed",
                observation_id=obs_id,
            )
            return saved_response
        return ObservationResponse.from_model(observation, photo=photo)

    return ObservationResponse.from_model(observation, rewards=rewards, photo=photo)


# ---------------------------------------------------------------------------
# GET /v1/observations/me
# ---------------------------------------------------------------------------

# Page size bounds. 50 is the largest single batch we'll ever serve to the
# kid app -- the Dex tab uses FlashList virtualization (per docs/mobile.md)
# and pages by 20 by default. Higher caps invite accidental N+1 fetches.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_MAX_CURSOR_LENGTH = 512
_OBSERVED_CURSOR_VERSION = 1
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _encode_observed_cursor(observed_at: datetime, observation_id: str) -> str:
    payload = json.dumps(
        {
            "v": _OBSERVED_CURSOR_VERSION,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
            "id": observation_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_observed_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode the opaque observed-order cursor or raise a public 422."""

    invalid = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Invalid observed-order cursor",
    )
    if len(cursor) > _MAX_CURSOR_LENGTH or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise invalid
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        if len(raw) > 256:
            raise ValueError("decoded cursor is too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "observed_at", "id"}:
            raise ValueError("cursor shape")
        if payload["v"] != _OBSERVED_CURSOR_VERSION:
            raise ValueError("cursor version")
        observation_id = payload["id"]
        raw_observed_at = payload["observed_at"]
        if not isinstance(observation_id, str) or _ULID_RE.fullmatch(observation_id) is None:
            raise ValueError("cursor id")
        if not isinstance(raw_observed_at, str):
            raise ValueError("cursor timestamp")
        observed_at = datetime.fromisoformat(raw_observed_at.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("cursor timestamp timezone")
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise invalid from exc
    return observed_at.astimezone(UTC), observation_id


class ObservationListItem(BaseModel):
    """Minimal metadata required by a child Field Journal card."""

    id: str
    photo_id: str
    submission_ulid: str | None
    geohash4: str | None
    observed_at: datetime
    location_source: str
    taxon_id: int | None
    species_name: str | None
    identification_source: str
    place_name: str | None
    ecology_tags: dict[str, str] = Field(default_factory=dict)
    child_presentation_status: ChildPresentationStatus
    dispatch_status: str


class ObservationListResponse(BaseModel):
    items: list[ObservationListItem]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `cursor` for observed order or `before` for legacy saved order. "
            "Null when this is the last page."
        ),
    )


@router.get("/me", response_model=ObservationListResponse)
async def list_my_observations(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    before: Annotated[str | None, Query(min_length=26, max_length=26)] = None,
    order: Annotated[Literal["saved", "observed"], Query()] = "saved",
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=_MAX_CURSOR_LENGTH),
    ] = None,
) -> ObservationListResponse:
    user_row = await resolve_current_user_row(session, current_user)

    if before is not None and (order == "observed" or cursor is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="before cannot be combined with observed order or cursor",
        )
    if cursor is not None and order != "observed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cursor requires order=observed",
        )

    # ULIDs are lex-sortable AND time-sortable, so DESC on id gives newest
    # first without a separate created_at index. Cursor is just the last id
    # we returned; "give me rows older than this".
    stmt = (
        select(models.Observation, models.Photo, models.PhotoRevocation)
        .join(models.Photo, models.Observation.photo_id == models.Photo.id)
        .outerjoin(
            models.PhotoRevocation,
            models.PhotoRevocation.photo_id == models.Photo.id,
        )
        .where(
            models.Observation.user_id == user_row.id,
            models.Observation.rejected_at.is_(None),
            models.Observation.moderation_status != "rejected",
            models.Photo.status != "deleted",
            models.Photo.attachment_status != "deleted",
        )
    )
    if order == "observed":
        if cursor is not None:
            cursor_observed_at, cursor_id = _decode_observed_cursor(cursor)
            stmt = stmt.where(
                (models.Observation.observed_at < cursor_observed_at)
                | (
                    (models.Observation.observed_at == cursor_observed_at)
                    & (models.Observation.id < cursor_id)
                )
            )
        stmt = stmt.order_by(
            desc(models.Observation.observed_at),
            desc(models.Observation.id),
        )
    else:
        if before is not None:
            stmt = stmt.where(models.Observation.id < before)
        stmt = stmt.order_by(desc(models.Observation.id))
    stmt = stmt.limit(limit + 1)

    rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        ObservationListItem(
            id=obs.id,
            photo_id=obs.photo_id,
            submission_ulid=obs.submission_key,
            geohash4=obs.geohash4,
            observed_at=obs.observed_at,
            location_source=getattr(obs, "location_source", None) or "legacy_coarsened",
            taxon_id=obs.taxon_id,
            species_name=obs.species_name,
            identification_source=getattr(obs, "identification_source", None) or "legacy",
            place_name=obs.place_name,
            ecology_tags=dict(obs.ecology_tags or {}),
            child_presentation_status=derive_child_presentation_status(
                getattr(obs, "moderation_status", None),
                getattr(photo, "status", None),
                photo_attachment_status=getattr(photo, "attachment_status", None),
                revocation_active=revocation is not None,
            ),
            dispatch_status=getattr(obs, "dispatch_status", None) or "unverified",
        )
        for obs, photo, revocation in page
    ]

    return ObservationListResponse(
        items=items,
        next_cursor=(
            _encode_observed_cursor(items[-1].observed_at, items[-1].id)
            if has_more and items and order == "observed"
            else items[-1].id
            if has_more and items
            else None
        ),
    )


@router.get("/{observation_id}", response_model=ObservationResponse)
async def get_observation(
    observation_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> ObservationResponse:
    """Return the canonical persisted result used for queue reconciliation."""
    user_row = await resolve_current_user_row(session, current_user)
    row = (
        await session.execute(
            select(models.Observation, models.Photo, models.PhotoRevocation)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .outerjoin(
                models.PhotoRevocation,
                models.PhotoRevocation.photo_id == models.Photo.id,
            )
            .where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
                models.Observation.rejected_at.is_(None),
                models.Observation.moderation_status != "rejected",
                models.Photo.status != "deleted",
                models.Photo.attachment_status != "deleted",
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    observation, photo, revocation = row
    return ObservationResponse.from_model(
        observation,
        photo=photo,
        revocation_active=revocation is not None,
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
    no_matches: bool = False


@router.post("/{observation_id}/identify", response_model=IdentifyResponse)
async def identify_observation(
    observation_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    inat_client: InatClientDep,
    storage: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> IdentifyResponse:
    user_row = await resolve_current_user_row(session, current_user)

    # Owner check is in the WHERE clause -- wrong owner returns 404 like
    # missing, no enumeration leak.
    obs_photo = (
        await session.execute(
            select(models.Observation, models.Photo, models.PhotoRevocation)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .outerjoin(
                models.PhotoRevocation,
                models.PhotoRevocation.photo_id == models.Photo.id,
            )
            .where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
                models.Observation.rejected_at.is_(None),
                models.Observation.moderation_status != "rejected",
                models.Photo.status != "deleted",
                models.Photo.attachment_status != "deleted",
            )
        )
    ).one_or_none()
    if obs_photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    obs, photo, revocation = obs_photo

    if (
        revocation is not None
        or derive_child_presentation_status(
            obs.moderation_status,
            photo.status,
            photo_attachment_status=photo.attachment_status,
        )
        != "clean"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Identification is available only after adult-approved moderation",
        )

    if not settings.inat_cv_egress_allowed:
        log.info("observations.identify.disabled", observation_id=observation_id)
        return IdentifyResponse(
            observation_id=observation_id,
            suggestions=[],
            cv_unavailable=True,
        )

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

    if photo.sha256 is None or len(photo.sha256) != 64:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Identification requires a verified canonical photo",
        )

    cached = (
        await session.execute(
            select(models.CvSuggestionCache).where(
                models.CvSuggestionCache.photo_sha256 == photo.sha256,
                models.CvSuggestionCache.model_version == settings.inat_cv_model_version,
            )
        )
    ).scalar_one_or_none()
    if cached is not None:
        return IdentifyResponse(
            observation_id=observation_id,
            suggestions=[CvSuggestionDTO.model_validate(item) for item in cached.suggestions],
            no_matches=len(cached.suggestions) == 0,
        )

    image_bytes = storage.fetch_object_bytes(bucket=photo.bucket, object_name=photo.object_name)

    try:
        suggestions = await score_image(
            inat_client,
            image_bytes=image_bytes,
            top_k=3,
            egress_enabled=settings.inat_cv_egress_allowed,
        )
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

    catalog_by_id: dict[int, models.SpeciesCache] = {}
    if suggestions:
        catalog_rows = (
            (
                await session.execute(
                    select(models.SpeciesCache).where(
                        models.SpeciesCache.taxon_id.in_([item.taxon_id for item in suggestions]),
                        models.SpeciesCache.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        catalog_by_id = {row.taxon_id: row for row in catalog_rows}
    suggestion_rows = [
        CvSuggestionDTO(
            taxon_id=s.taxon_id,
            common_name=catalog_by_id[s.taxon_id].common_name,
            scientific_name=catalog_by_id[s.taxon_id].scientific_name,
            score=s.score,
        )
        for s in suggestions
        if s.taxon_id in catalog_by_id
    ]
    session.add(
        models.CvSuggestionCache(
            photo_sha256=photo.sha256,
            model_version=settings.inat_cv_model_version,
            suggestions=[row.model_dump(mode="json") for row in suggestion_rows],
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Another request filled the same immutable hash/model cache. The
        # suggestions computed here are equivalent and safe to return.
        await session.rollback()

    log.info(
        "observations.identify.scored",
        observation_id=observation_id,
        suggestion_count=len(suggestion_rows),
        no_matches=len(suggestion_rows) == 0,
    )
    return IdentifyResponse(
        observation_id=observation_id,
        suggestions=suggestion_rows,
        no_matches=len(suggestion_rows) == 0,
    )


# ---------------------------------------------------------------------------
# PATCH /v1/observations/{id}
# ---------------------------------------------------------------------------


class ObservationPatch(BaseModel):
    """Non-derived display metadata only.

    Identification changes use the revisioned identification endpoint in the
    closed-beta slice so Dex/expedition/Sanctuary state can be rebuilt.
    """

    model_config = ConfigDict(extra="forbid")
    place_name: str | None = Field(default=None, max_length=200)


@router.patch("/{observation_id}", response_model=ObservationResponse)
async def patch_observation(
    observation_id: str,
    payload: ObservationPatch,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> ObservationResponse:
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one field must be provided",
        )

    user_row = await resolve_current_user_row(session, current_user)

    obs = (
        await session.execute(
            select(models.Observation)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
                models.Observation.rejected_at.is_(None),
                models.Observation.moderation_status != "rejected",
                models.Photo.status != "deleted",
                models.Photo.attachment_status != "deleted",
            )
        )
    ).scalar_one_or_none()
    if obs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")

    for key, value in fields.items():
        setattr(obs, key, value)

    await session.commit()
    await session.refresh(obs)

    log.info(
        "observations.patched",
        observation_id=observation_id,
        fields=list(fields.keys()),
    )
    return await _persisted_observation_response(session, obs)


# ---------------------------------------------------------------------------
# POST /v1/observations/{id}/identification
# ---------------------------------------------------------------------------


class IdentificationUpdate(BaseModel):
    """Optimistic correction that queues deterministic derived-state repair."""

    model_config = ConfigDict(extra="forbid")

    taxon_id: int | None = Field(default=None, ge=1)
    manual_text: str | None = Field(default=None, max_length=200)
    source: Literal["catalog", "cv", "manual_text", "unknown"]
    expected_revision: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_identification(self) -> IdentificationUpdate:
        text_value = (self.manual_text or "").strip()
        if self.source in {"catalog", "cv"}:
            if self.taxon_id is None or text_value:
                raise ValueError("catalog and CV identification require only taxon_id")
        elif self.source == "manual_text":
            if self.taxon_id is not None or not text_value:
                raise ValueError("manual identification requires text and no taxon_id")
            self.manual_text = text_value
        elif self.taxon_id is not None or text_value:
            raise ValueError("Unknown identification cannot include a taxon or text")
        return self


class IdentificationUpdateResponse(BaseModel):
    observation: ObservationResponse
    rebuild_id: str
    rebuild_status: str


@router.post(
    "/{observation_id}/identification",
    response_model=IdentificationUpdateResponse,
)
async def update_identification(
    observation_id: str,
    payload: IdentificationUpdate,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> IdentificationUpdateResponse:
    user_row = await resolve_current_user_row(session, current_user)
    # Rebuild and every derived-state writer take the user advisory lock
    # before row locks. Keep the same global order to prevent a correction
    # racing a rebuild from deadlocking observation-row -> user-lock.
    await acquire_user_lock(session, user_row.id)
    observation = (
        await session.execute(
            select(models.Observation)
            .where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    if observation.rejected_at is not None or observation.moderation_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rejected observation cannot be identified",
        )
    if observation.inat_observation_id is not None or observation.submitted_to_inat_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A publicly submitted observation cannot be changed",
        )
    if observation.identification_revision != payload.expected_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Identification has changed; refresh before trying again",
        )

    if payload.source == "cv":
        cv_photo_row = (
            await session.execute(
                select(models.Photo, models.PhotoRevocation)
                .outerjoin(
                    models.PhotoRevocation,
                    models.PhotoRevocation.photo_id == models.Photo.id,
                )
                .where(models.Photo.id == observation.photo_id)
            )
        ).one_or_none()
        if cv_photo_row is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="CV identification requires an approved photo",
            )
        cv_photo, cv_revocation = cv_photo_row
        if (
            derive_child_presentation_status(
                observation.moderation_status,
                cv_photo.status,
                photo_attachment_status=cv_photo.attachment_status,
                revocation_active=cv_revocation is not None,
            )
            != "clean"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="CV identification requires an approved photo",
            )

    species_name: str | None = None
    if payload.source in {"catalog", "cv"}:
        assert payload.taxon_id is not None  # guaranteed by the request validator
        catalog_row = await _catalog_species(session, payload.taxon_id)
        species_name = catalog_row.common_name or catalog_row.scientific_name
    elif payload.source == "manual_text":
        species_name = payload.manual_text

    observation.taxon_id = payload.taxon_id
    observation.species_name = species_name
    if payload.taxon_id is not None and observation.taxon_first_assigned_at is None:
        observation.taxon_first_assigned_at = datetime.now(UTC)
    observation.identification_source = payload.source
    observation.identification_revision += 1
    observation.dispatch_status = "unverified"

    rebuild = await enqueue_rebuild(
        session,
        user_id=user_row.id,
        trigger_observation_id=observation.id,
    )
    await session.commit()
    await session.refresh(observation)

    log.info(
        "observations.identification_changed",
        observation_id=observation.id,
        identification_revision=observation.identification_revision,
        source=payload.source,
        rebuild_id=rebuild.id,
    )
    return IdentificationUpdateResponse(
        observation=await _persisted_observation_response(session, observation),
        rebuild_id=rebuild.id,
        rebuild_status=rebuild.status,
    )
