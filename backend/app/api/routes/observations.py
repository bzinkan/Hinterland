"""Observation routes.

`POST /v1/observations` finalizes an observation after the photo has landed
in `pending/`. It is the second leg of the kid's submission flow:

1. Mobile calls `POST /v1/photos/presign` -> gets `photo_id` + signed URL
2. Mobile PUTs the photo bytes to the signed URL (lands in `pending/`)
3. Mobile calls `POST /v1/observations` with `photo_id` + lat/lng + taxon
4. The async moderation worker runs out of band on `pending/` photos and
   moves them to `observations/` or `quarantine/`

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

from datetime import UTC, datetime
from typing import Annotated

import geohash
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep
from app.dispatcher.core import dispatch
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context, Reward
from app.inat.client import InatClientDep, InatUnavailable
from app.inat.cv import score_image
from app.services import species_cache

router = APIRouter(prefix="/v1/observations", tags=["observations"])

log = structlog.get_logger()

# WorldHandler's published error state key (bare string to avoid a
# route -> handler import; same convention world.py uses for the dex
# state key).
_WORLD_STATE_ERROR = "error"


class ObservationCreateRequest(BaseModel):
    photo_id: str = Field(..., min_length=1, max_length=26)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    taxon_id: int | None = Field(default=None, ge=1)
    species_name: str | None = Field(default=None, max_length=200)
    place_name: str | None = Field(default=None, max_length=200)


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
    rewards: list[RewardDTO] = Field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        obs: models.Observation,
        rewards: list[Reward] | None = None,
    ) -> ObservationResponse:
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
            rewards=[RewardDTO.from_reward(r) for r in (rewards or [])],
        )


async def _observation_for_photo(session: AsyncSession, photo_id: str) -> models.Observation | None:
    """The observation already attached to `photo_id`, if any.

    Used for idempotent create replay. Caller has already proven photo
    ownership, and an observation can only be created by its photo's
    owner, so no extra user filter is needed here.
    """
    return (
        await session.execute(
            select(models.Observation).where(models.Observation.photo_id == photo_id)
        )
    ).scalar_one_or_none()


async def _cached_species_display_name(session: AsyncSession, taxon_id: int) -> str | None:
    """Best-effort local display name for a taxon without calling iNat."""
    row = (
        await session.execute(
            select(models.SpeciesCache).where(models.SpeciesCache.taxon_id == taxon_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return row.common_name or row.scientific_name


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
    user_row = await resolve_current_user_row(session, current_user)

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
        # The photo may be non-pending because moderation already processed
        # the observation this client created but never heard back about
        # (lost 201). Replay idempotently instead of stranding the retry.
        existing = await _observation_for_photo(session, payload.photo_id)
        if existing is not None:
            log.info(
                "observations.create.idempotent_replay",
                observation_id=existing.id,
                photo_id=payload.photo_id,
                photo_status=photo.status,
            )
            return ObservationResponse.from_model(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Photo is in status {photo.status}, not pending",
        )

    species_name = payload.species_name
    if payload.taxon_id is not None and species_name is None:
        species_name = await _cached_species_display_name(session, payload.taxon_id)

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
        species_name=species_name,
        place_name=payload.place_name,
        # Created-with-taxon counts as the first assignment: the
        # create-time dispatch runs with this taxon, so a later
        # clear-and-repick must not dispatch again.
        taxon_first_assigned_at=(datetime.now(UTC) if payload.taxon_id is not None else None),
    )
    session.add(observation)
    try:
        await session.commit()
    except IntegrityError:
        # uq_observations_photo_id: this photo already has an observation.
        # Rollback also undoes the membership counter bump above (the
        # original create already counted it). The common cause is a retry
        # after a lost create response, so replay the existing row rather
        # than 409ing the client into a dead end.
        await session.rollback()
        existing = await _observation_for_photo(session, payload.photo_id)
        if existing is not None:
            log.info(
                "observations.create.idempotent_replay",
                observation_id=existing.id,
                photo_id=payload.photo_id,
                # NOT photo.status: the rollback expired the `photo`
                # instance and attribute access would raise MissingGreenlet
                # on AsyncSession. This branch only runs when the photo was
                # still pending at the check above.
                photo_status="pending",
            )
            return ObservationResponse.from_model(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Photo is already attached to an observation",
        ) from None
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

    # Dispatcher runs after the observation is persisted. Failure here
    # never surfaces to the client -- worst case is a missing celebration
    # which `admin/dispatcher_replay.py` recovers nightly. The kid still
    # sees their observation, just without the rewards.
    #
    # On success we stamp `observations.dispatched_at` so the replay
    # task knows to skip this row. A failure here leaves it NULL --
    # the replay picks it up on its next run.
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
        observation.dispatched_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        log.exception(
            "observations.dispatch_failed",
            observation_id=obs_id,
        )

    return ObservationResponse.from_model(observation, rewards=rewards)


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
    user_row = await resolve_current_user_row(session, current_user)

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
        no_matches=len(suggestions) == 0,
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
        no_matches=len(suggestions) == 0,
    )


# ---------------------------------------------------------------------------
# PATCH /v1/observations/{id}
# ---------------------------------------------------------------------------


class ObservationPatch(BaseModel):
    """Partial update. Fields not present are left untouched.

    `taxon_id=null` explicitly clears the taxon (kid changed their mind
    after picking one). To clear the species_name, send an empty string.
    """

    taxon_id: int | None = Field(default=None, ge=1)
    species_name: str | None = Field(default=None, max_length=200)
    place_name: str | None = Field(default=None, max_length=200)


@router.patch("/{observation_id}", response_model=ObservationResponse)
async def patch_observation(
    observation_id: str,
    payload: ObservationPatch,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    inat_client: InatClientDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
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
            select(models.Observation).where(
                models.Observation.id == observation_id,
                models.Observation.user_id == user_row.id,
            )
        )
    ).scalar_one_or_none()
    if obs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")

    # Detect the kid's FIRST-EVER species pick BEFORE applying fields --
    # this is what triggers the re-dispatch below. Only the None -> taxon
    # transition counts: corrections (A -> B) deliberately do NOT
    # re-dispatch, because DexHandler's first-find gate is per
    # (user, taxon), not per observation -- re-dispatching on every
    # transition would let one photo mint first_find / dex_count credit
    # for arbitrarily many species. Clearing the taxon (explicit null)
    # is not a transition either, and the write-once
    # taxon_first_assigned_at marker means a clear-and-repick (null,
    # then a new taxon -- raw-API only, the shipped UI has no such
    # affordance) can never dispatch a second time.
    taxon_assigned = (
        "taxon_id" in fields
        and fields["taxon_id"] is not None
        and obs.taxon_id is None
        and obs.taxon_first_assigned_at is None
    )

    # Belt-and-braces dex probe, run BEFORE any state is written: if this
    # observation already minted a DexEntry (pre-migration
    # assigned-then-cleared residual, or a concurrent first-assign racing
    # this request), the dispatch must not run -- and critically,
    # dispatched_at must NOT be cleared below, or the nightly replay
    # (which has no probe) would run the very dispatch the probe blocked.
    # One observation never mints two first finds.
    should_dispatch = False
    if taxon_assigned:
        already_minted = (
            await session.execute(
                select(models.DexEntry.id)
                .where(models.DexEntry.first_observation_id == obs.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if already_minted is None:
            should_dispatch = True
        else:
            log.info(
                "observations.patch.redispatch_skipped_already_minted",
                observation_id=observation_id,
            )

    # Warm the species cache whenever it's needed: for the species_name
    # autofill, and -- on a first taxon assignment -- for the re-dispatch
    # below, whose iconic/descendant matchers read the cached iNat
    # payload (ancestor_ids). Without the taxon_assigned arm, a PATCH
    # carrying BOTH taxon_id and species_name could re-dispatch against
    # a cold cache: those matchers would silently miss and the
    # dispatched_at stamp would prevent any later retry. Cache miss
    # falls through to iNat; an iNat outage just leaves species_name
    # as-is (and the matchers cold for this one dispatch).
    wants_autofill = (
        "taxon_id" in fields and fields["taxon_id"] is not None and "species_name" not in fields
    )
    if wants_autofill or taxon_assigned:
        try:
            cached = await species_cache.get_or_fill(session, inat_client, fields["taxon_id"])
        except InatUnavailable as exc:
            log.info(
                "observations.patch.species_lookup_unavailable",
                observation_id=observation_id,
                taxon_id=fields["taxon_id"],
                reason=str(exc),
            )
            cached = None
        # An explicit species_name from the caller is honored verbatim
        # -- the cache result only fills the gap.
        if cached is not None and wants_autofill:
            fields["species_name"] = cached.common_name or cached.scientific_name

    for key, value in fields.items():
        setattr(obs, key, value)

    if taxon_assigned:
        # Stamp the write-once marker in the SAME commit as the taxon
        # itself. Probe-blocked assignments stamp the marker too (the
        # observation had its dispatch; future repicks stay gated) but
        # keep their dispatched_at intact.
        obs.taxon_first_assigned_at = datetime.now(UTC)
    if should_dispatch:
        # Clear dispatched_at (still stamped by the create-time dispatch)
        # in the same commit: if the process dies before the re-dispatch
        # below runs, the nightly replay (dispatched_at IS NULL) recovers
        # the kid's credit instead of it being lost forever behind the
        # marker. A successful dispatch re-stamps it immediately.
        obs.dispatched_at = None

    await session.commit()
    await session.refresh(obs)

    log.info(
        "observations.patched",
        observation_id=observation_id,
        fields=list(fields.keys()),
    )

    # Re-dispatch when the kid's species pick first lands. The live mobile
    # flow sets the taxon AFTER create (CV identify -> PATCH), so the
    # create-time dispatch ran with taxon_id=None and taxon-based
    # expedition steps could never advance -- this second dispatch is what
    # makes them reachable. The warm-up above runs on every first taxon
    # assignment (explicit species_name included), so species_cache holds
    # the raw iNat payload with ancestor_ids by the time we get here;
    # only an iNat outage can leave the cache cold, in which case
    # iconic/descendant matches simply don't fire for this dispatch.
    #
    # Re-running the full handler list is safe: RarityHandler only does a
    # conditional monotonic counter bump, WorldHandler gates on its
    # per-observation contribution row, and ExpeditionHandler gates on the
    # observation_id recorded in completed_steps. DexHandler's first-find
    # insert is only ON CONFLICT-gated per (user, taxon), so on top of the
    # None -> taxon trigger above we skip the dispatch entirely when this
    # observation already minted a dex entry (kid cleared the taxon and
    # picked again) -- one observation never mints two first finds.
    #
    # The response is snapshotted before dispatching: a failure path below
    # rolls the session back, which expires `obs`.
    response = ObservationResponse.from_model(obs)
    if should_dispatch:
        try:
            photo = (
                await session.execute(select(models.Photo).where(models.Photo.id == obs.photo_id))
            ).scalar_one()
            group = (
                await session.execute(select(models.Group).where(models.Group.id == obs.group_id))
            ).scalar_one_or_none()
            ctx = Context(
                db=session,
                user=user_row,
                group=group,
                observation=obs,
                photo=photo,
            )
            rewards = await dispatch(ctx, HANDLERS)
            # This taxon-time dispatch is the ONLY chance the Sanctuary
            # contribution gets (WorldHandler skips taxonless creates),
            # and WorldHandler swallows its own errors rather than
            # raising. If it reported failure, leave dispatched_at NULL
            # so the replay job retries the full (idempotent) dispatch
            # instead of the contribution being silently lost forever.
            world_result = ctx.results.get("world")
            world_failed = world_result is None or bool(world_result.state.get(_WORLD_STATE_ERROR))
            if world_failed:
                log.warning(
                    "observations.patch.world_failed_replay_pending",
                    observation_id=observation_id,
                )
            else:
                obs.dispatched_at = datetime.now(UTC)
            await session.commit()
            response.rewards = [RewardDTO.from_reward(r) for r in rewards]
        except Exception:
            log.exception(
                "observations.patch.dispatch_failed",
                observation_id=observation_id,
            )
            # The create-time dispatch already stamped dispatched_at, so
            # without a reset the nightly replay (dispatched_at IS NULL)
            # would never revisit this observation and the kid's species
            # pick would never earn its expedition / dex credit. Handlers
            # are per-observation idempotent, so clearing the stamp and
            # letting the replay re-run the full dispatch is safe. Direct
            # UPDATE rather than ORM mutation: the rollback expired `obs`.
            try:
                await session.rollback()
                await session.execute(
                    update(models.Observation)
                    .where(models.Observation.id == observation_id)
                    .values(dispatched_at=None)
                )
                await session.commit()
            except Exception:
                log.exception(
                    "observations.patch.dispatch_reset_failed",
                    observation_id=observation_id,
                )

    return response
