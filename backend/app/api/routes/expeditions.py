"""Expedition listing + start endpoints.

`GET /v1/expeditions/available` -- expeditions whose prerequisites the
caller has met, that they haven't started or completed yet. With the
optional `geohash4` query param, region-aware ranking downranks (never
hides) expeditions whose iconic taxa nobody reports nearby; without it
the list keeps its (tier, id) order.

`GET /v1/expeditions/me` -- the caller's in-progress + completed
expeditions, newest-progress first, with per-step detail (description,
hint, completed_at) so the app can show what each step asks for.

`POST /v1/expeditions/{id}/start` -- create an empty
`expedition_progress` row. Fails 409 if a row already exists.

`POST /v1/expeditions/{id}/restart` -- reset a stalled in-progress
run back to step zero. Fails 409 on completed expeditions (trophies).

Per docs/expedition-authoring.md: prerequisites are ANDed; current
kinds are `dex_count_at_least` and `completed_expedition`. The
"never edit ids" invariant from the doc means a row keyed by
expedition_id is a stable handle forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.db import models
from app.db.session import DbSessionDep
from app.models.expedition import (
    Expedition,
    PrereqCompleted,
    PrereqDexCount,
    Step,
)
from app.services.expedition_progress import parse_step_completion
from app.services.expedition_ranking import (
    GEOHASH4_RE,
    region_iconic_taxa,
    relevance_for,
    required_iconic_taxa,
)

router = APIRouter(prefix="/v1/expeditions", tags=["expeditions"])

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class Relevance(BaseModel):
    """How well an expedition fits what people report near the caller."""

    level: Literal["great_here", "tricky_here", "unknown"]
    reason: str | None


class ExpeditionSummary(BaseModel):
    id: str
    title: str
    subtitle: str | None
    tier: int
    duration_minutes: int
    environments: list[str]
    intro: str
    # Additive: always present, defaults to "no regional signal".
    relevance: Relevance = Relevance(level="unknown", reason=None)


class AvailableListResponse(BaseModel):
    items: list[ExpeditionSummary]


class StepProgress(BaseModel):
    """One step of an in-progress expedition, in content order."""

    id: str
    description: str
    hint: str | None
    completed_at: datetime | None


class ProgressItem(BaseModel):
    expedition_id: str
    title: str
    subtitle: str | None
    intro: str
    outro: str
    started_at: datetime
    completed_at: datetime | None
    focused_at: datetime | None
    completed_step_count: int
    total_step_count: int
    steps: list[StepProgress]


class MyProgressResponse(BaseModel):
    active_expedition_id: str | None
    items: list[ProgressItem]


class StartResponse(BaseModel):
    expedition_id: str
    started_at: datetime


class FocusResponse(BaseModel):
    expedition_id: str
    focused_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _user_dex_count(session: AsyncSession, user_id: str) -> int:
    """Sum dex_count across the user's memberships (one in Phase 1)."""
    rows = (
        await session.execute(
            select(models.Membership.dex_count).where(models.Membership.user_id == user_id)
        )
    ).all()
    return sum(r[0] for r in rows)


async def _completed_expedition_ids(session: AsyncSession, user_id: str) -> set[str]:
    rows = (
        await session.execute(
            select(models.ExpeditionProgress.expedition_id).where(
                models.ExpeditionProgress.user_id == user_id,
                models.ExpeditionProgress.completed_at.is_not(None),
            )
        )
    ).all()
    return {r[0] for r in rows}


async def _any_progress_expedition_ids(session: AsyncSession, user_id: str) -> set[str]:
    """Both in-progress AND completed. Used to filter the "available" list."""
    rows = (
        await session.execute(
            select(models.ExpeditionProgress.expedition_id).where(
                models.ExpeditionProgress.user_id == user_id,
            )
        )
    ).all()
    return {r[0] for r in rows}


def _prerequisites_met(exp: Expedition, *, dex_count: int, completed_ids: set[str]) -> bool:
    for prereq in exp.prerequisites:
        if isinstance(prereq, PrereqDexCount) and dex_count < prereq.value:
            return False
        if isinstance(prereq, PrereqCompleted) and prereq.value not in completed_ids:
            return False
    return True


def _step_progress(step: Step, completed_value: object) -> StepProgress:
    """Build one StepProgress, coercing the stored iso string via Pydantic.

    A malformed completed_at string in a stored row must not 500 the
    whole /me response -- it degrades to "not completed" for that step.
    """
    completion = parse_step_completion(completed_value)
    try:
        return StepProgress.model_validate(
            {
                "id": step.id,
                "description": step.description,
                "hint": step.hint,
                "completed_at": completion.completed_at,
            }
        )
    except Exception:
        return StepProgress(
            id=step.id,
            description=step.description,
            hint=step.hint,
            completed_at=None,
        )


def _completed_step_count(exp: Expedition, completed: dict[str, object]) -> int:
    """Count completed keys that still exist in the current content body."""
    return sum(1 for step in exp.steps if step.id in completed)


async def _focus_progress(
    session: AsyncSession,
    *,
    user_id: str,
    progress: models.ExpeditionProgress,
    focused_at: datetime | None = None,
) -> datetime:
    """Set this progress row as the user's only focused expedition."""
    focused_at = focused_at or datetime.now(UTC)
    await session.execute(
        update(models.ExpeditionProgress)
        .where(
            models.ExpeditionProgress.user_id == user_id,
            models.ExpeditionProgress.id != progress.id,
            models.ExpeditionProgress.focused_at.is_not(None),
        )
        .values(focused_at=None)
    )
    progress.focused_at = focused_at
    return focused_at


def _active_expedition_id(items: list[ProgressItem]) -> str | None:
    focused = [item for item in items if item.completed_at is None and item.focused_at is not None]
    if focused:
        focused.sort(
            key=lambda item: item.focused_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return focused[0].expedition_id
    for item in items:
        if item.completed_at is None:
            return item.expedition_id
    return None


# ---------------------------------------------------------------------------
# GET /v1/expeditions/available
# ---------------------------------------------------------------------------


@router.get("/available", response_model=AvailableListResponse)
async def list_available(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    geohash4: Annotated[str | None, Query()] = None,
) -> AvailableListResponse:
    user = await resolve_current_user_row(session, current_user)
    dex_count = await _user_dex_count(session, user.id)
    completed_ids = await _completed_expedition_ids(session, user.id)
    any_progress_ids = await _any_progress_expedition_ids(session, user.id)

    # Validated in code rather than Query(pattern=...) on purpose: an
    # invalid geohash4 from an old or buggy client must silently degrade
    # to the unranked list, never 422 the whole endpoint.
    region_hash: str | None = None
    if geohash4 is not None:
        candidate = geohash4.lower()
        if GEOHASH4_RE.fullmatch(candidate):
            region_hash = candidate

    rows = (
        (
            await session.execute(
                select(models.ExpeditionContent)
                .where(models.ExpeditionContent.archived.is_(False))
                .order_by(models.ExpeditionContent.tier, models.ExpeditionContent.id)
            )
        )
        .scalars()
        .all()
    )

    region = await region_iconic_taxa(session, region_hash) if region_hash is not None else None

    ranked: list[tuple[int, ExpeditionSummary]] = []
    for row in rows:
        if row.id in any_progress_ids:
            # Either in-progress or already done -- not "available".
            continue
        try:
            exp = Expedition.model_validate(row.body)
        except Exception:
            log.warning("expeditions.available.bad_content", id=row.id)
            continue
        if not _prerequisites_met(exp, dex_count=dex_count, completed_ids=completed_ids):
            continue
        bucket, level, reason = relevance_for(required_iconic_taxa(exp), region)
        ranked.append(
            (
                bucket,
                ExpeditionSummary(
                    id=exp.id,
                    title=exp.title,
                    subtitle=exp.subtitle,
                    tier=exp.tier,
                    duration_minutes=exp.duration_minutes,
                    environments=list(exp.environments),
                    intro=exp.intro,
                    relevance=Relevance(level=level, reason=reason),
                ),
            )
        )

    # Rows arrive (tier, id)-ordered, so this stable sort yields
    # (bucket, tier, id). Downrank, never hide: every bucket stays
    # listed and startable. Without a region baseline every bucket is
    # 0 and the order is untouched.
    ranked.sort(key=lambda entry: entry[0])

    # Privacy: geohash4 is the only location-shaped value on this
    # endpoint -- never lat/lng (none exists here anyway). Invalid
    # input is logged as None, not echoed.
    log.info(
        "expeditions.available.ranked",
        geohash4=region_hash,
        ranked=region_hash is not None,
        region_known=region is not None,
    )

    return AvailableListResponse(items=[summary for _, summary in ranked])


# ---------------------------------------------------------------------------
# GET /v1/expeditions/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MyProgressResponse)
async def list_my_progress(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> MyProgressResponse:
    user = await resolve_current_user_row(session, current_user)

    rows = (
        await session.execute(
            select(models.ExpeditionProgress, models.ExpeditionContent)
            .join(
                models.ExpeditionContent,
                models.ExpeditionProgress.expedition_id == models.ExpeditionContent.id,
            )
            .where(models.ExpeditionProgress.user_id == user.id)
            .order_by(desc(models.ExpeditionProgress.created_at))
        )
    ).all()

    items: list[ProgressItem] = []
    for progress, content in rows:
        completed = progress.completed_steps or {}
        try:
            exp = Expedition.model_validate(content.body)
            total_steps = len(exp.steps)
            title = exp.title
            subtitle = exp.subtitle
            intro = exp.intro
            outro = exp.outro
            steps = [_step_progress(step, completed.get(step.id)) for step in exp.steps]
            completed_step_count = _completed_step_count(exp, completed)
        except Exception:
            log.warning("expeditions.me.bad_content", id=content.id)
            total_steps = 0
            title = content.id
            subtitle = None
            intro = ""
            outro = ""
            steps = []
            completed_step_count = 0
        items.append(
            ProgressItem(
                expedition_id=progress.expedition_id,
                title=title,
                subtitle=subtitle,
                intro=intro,
                outro=outro,
                started_at=progress.created_at,
                completed_at=progress.completed_at,
                focused_at=progress.focused_at,
                completed_step_count=completed_step_count,
                total_step_count=total_steps,
                steps=steps,
            )
        )

    return MyProgressResponse(active_expedition_id=_active_expedition_id(items), items=items)


# ---------------------------------------------------------------------------
# POST /v1/expeditions/{id}/start
# ---------------------------------------------------------------------------


@router.post(
    "/{expedition_id}/start",
    response_model=StartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_expedition(
    expedition_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> StartResponse:
    user = await resolve_current_user_row(session, current_user)
    if not current_user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is missing group_id claim",
        )
    group_id = current_user.group_id

    content = (
        await session.execute(
            select(models.ExpeditionContent).where(
                models.ExpeditionContent.id == expedition_id,
                models.ExpeditionContent.archived.is_(False),
            )
        )
    ).scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not found")

    try:
        exp = Expedition.model_validate(content.body)
    except Exception as exc:
        log.warning("expeditions.start.bad_content", id=expedition_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Expedition content is invalid",
        ) from exc

    # Prerequisite check
    dex_count = await _user_dex_count(session, user.id)
    completed_ids = await _completed_expedition_ids(session, user.id)
    if not _prerequisites_met(exp, dex_count=dex_count, completed_ids=completed_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prerequisites not met",
        )

    # Already started / completed?
    existing = (
        await session.execute(
            select(models.ExpeditionProgress.id).where(
                models.ExpeditionProgress.user_id == user.id,
                models.ExpeditionProgress.expedition_id == expedition_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expedition already started",
        )

    progress = models.ExpeditionProgress(
        id=str(ULID()),
        user_id=user.id,
        group_id=group_id,
        expedition_id=expedition_id,
        completed_steps={},
    )
    session.add(progress)
    await _focus_progress(session, user_id=user.id, progress=progress)
    try:
        await session.commit()
    except IntegrityError as exc:
        # Two concurrent starts both pass the SELECT above; the loser's
        # commit trips uq_expedition_progress_user_exp and must surface
        # the documented 409, not a 500.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expedition already started",
        ) from exc
    await session.refresh(progress)

    log.info(
        "expeditions.started",
        expedition_id=expedition_id,
        user_id=user.id,
    )
    return StartResponse(expedition_id=expedition_id, started_at=progress.created_at)


# ---------------------------------------------------------------------------
# POST /v1/expeditions/{id}/restart
# ---------------------------------------------------------------------------


@router.post(
    "/{expedition_id}/restart",
    response_model=StartResponse,
    status_code=status.HTTP_200_OK,
)
async def restart_expedition(
    expedition_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> StartResponse:
    """Reset a stalled in-progress run back to step zero.

    Restart is for stalled runs -- the kid started an expedition, got
    stuck, and wants a fresh go. Completed expeditions are trophies
    (and `completed_expedition` prerequisites hang off them), so they
    are never restartable. Deliberately migration-free: the existing
    progress row is reset in place rather than adding restart
    bookkeeping columns.
    """
    user = await resolve_current_user_row(session, current_user)

    content = (
        await session.execute(
            select(models.ExpeditionContent).where(
                models.ExpeditionContent.id == expedition_id,
                models.ExpeditionContent.archived.is_(False),
            )
        )
    ).scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not found")

    progress = (
        await session.execute(
            select(models.ExpeditionProgress)
            .where(
                models.ExpeditionProgress.user_id == user.id,
                models.ExpeditionProgress.expedition_id == expedition_id,
            )
            # Restart and the dispatcher's ExpeditionHandler are both
            # read-modify-write writers on this row; the row lock closes
            # the restart-vs-dispatch lost-update window. The transaction
            # is short and commits promptly below.
            .with_for_update()
        )
    ).scalar_one_or_none()
    if progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not started")

    if progress.completed_at is not None:
        # Completed expeditions are trophies and back completed_expedition
        # prerequisites -- never restartable.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expedition already completed",
        )

    progress.completed_steps = {}
    # JSONB mutation tracking needs an explicit nudge when we reassign.
    flag_modified(progress, "completed_steps")
    progress.completed_at = None
    # Re-anchor created_at on the current run -- deliberate: /me
    # started_at, list ordering, and funnel time-to-complete should all
    # measure the fresh attempt, not the abandoned one.
    progress.created_at = datetime.now(UTC)
    await _focus_progress(
        session,
        user_id=user.id,
        progress=progress,
        focused_at=progress.created_at,
    )
    await session.commit()
    await session.refresh(progress)

    log.info(
        "expeditions.restarted",
        expedition_id=expedition_id,
        user_id=user.id,
    )
    return StartResponse(expedition_id=expedition_id, started_at=progress.created_at)


@router.post(
    "/{expedition_id}/focus",
    response_model=FocusResponse,
    status_code=status.HTTP_200_OK,
)
async def focus_expedition(
    expedition_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> FocusResponse:
    """Make an already-started incomplete expedition the kid's active quest."""
    user = await resolve_current_user_row(session, current_user)

    progress = (
        await session.execute(
            select(models.ExpeditionProgress)
            .where(
                models.ExpeditionProgress.user_id == user.id,
                models.ExpeditionProgress.expedition_id == expedition_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expedition not started")
    if progress.completed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Completed expeditions cannot be focused",
        )

    focused_at = await _focus_progress(session, user_id=user.id, progress=progress)
    await session.commit()

    log.info(
        "expeditions.focused",
        expedition_id=expedition_id,
        user_id=user.id,
    )
    return FocusResponse(expedition_id=expedition_id, focused_at=focused_at)
