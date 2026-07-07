"""Dex listing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1/dex", tags=["dex"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


class DexListItem(BaseModel):
    id: str
    taxon_id: int
    species_name: str | None
    common_name: str | None
    scientific_name: str | None
    iconic_taxon: str | None
    first_observation_id: str
    first_photo_id: str
    first_photo_status: str
    first_seen_at: datetime
    observation_count: int
    latest_seen_at: datetime


class DexListResponse(BaseModel):
    items: list[DexListItem]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `before` to fetch the next page. Null when this is the last page."
        ),
    )


async def _cursor_first_seen_at(
    session: AsyncSession,
    *,
    user_id: str,
    before: str,
) -> datetime | None:
    return (
        await session.execute(
            select(models.DexEntry.first_seen_at).where(
                models.DexEntry.user_id == user_id,
                models.DexEntry.id == before,
            )
        )
    ).scalar_one_or_none()


@router.get("/me", response_model=DexListResponse)
async def list_my_dex(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    before: Annotated[str | None, Query(min_length=26, max_length=26)] = None,
) -> DexListResponse:
    user = await resolve_current_user_row(session, current_user)

    stats = (
        select(
            models.Observation.taxon_id.label("taxon_id"),
            func.count(models.Observation.id).label("observation_count"),
            func.max(models.Observation.created_at).label("latest_seen_at"),
        )
        .where(
            models.Observation.user_id == user.id,
            models.Observation.taxon_id.is_not(None),
        )
        .group_by(models.Observation.taxon_id)
        .subquery()
    )

    stmt = (
        select(
            models.DexEntry,
            models.Observation,
            models.Photo,
            models.SpeciesCache,
            stats.c.observation_count,
            stats.c.latest_seen_at,
        )
        .join(
            models.Observation,
            models.DexEntry.first_observation_id == models.Observation.id,
        )
        .join(models.Photo, models.Observation.photo_id == models.Photo.id)
        .outerjoin(models.SpeciesCache, models.SpeciesCache.taxon_id == models.DexEntry.taxon_id)
        .outerjoin(stats, stats.c.taxon_id == models.DexEntry.taxon_id)
        .where(models.DexEntry.user_id == user.id)
    )

    if before is not None:
        cursor_first_seen = await _cursor_first_seen_at(
            session,
            user_id=user.id,
            before=before,
        )
        if cursor_first_seen is None:
            return DexListResponse(items=[], next_cursor=None)
        stmt = stmt.where(
            or_(
                models.DexEntry.first_seen_at < cursor_first_seen,
                and_(
                    models.DexEntry.first_seen_at == cursor_first_seen,
                    models.DexEntry.id < before,
                ),
            )
        )

    stmt = stmt.order_by(desc(models.DexEntry.first_seen_at), desc(models.DexEntry.id)).limit(
        limit + 1
    )
    rows = (await session.execute(stmt)).all()

    has_more = len(rows) > limit
    page = rows[:limit]
    items = [
        DexListItem(
            id=dex.id,
            taxon_id=dex.taxon_id,
            species_name=dex.species_name,
            common_name=species.common_name if species is not None else None,
            scientific_name=species.scientific_name if species is not None else None,
            iconic_taxon=species.iconic_taxon if species is not None else None,
            first_observation_id=dex.first_observation_id,
            first_photo_id=first_obs.photo_id,
            first_photo_status=photo.status,
            first_seen_at=dex.first_seen_at,
            observation_count=int(observation_count or 1),
            latest_seen_at=latest_seen_at or dex.first_seen_at,
        )
        for dex, first_obs, photo, species, observation_count, latest_seen_at in page
    ]

    return DexListResponse(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
    )
