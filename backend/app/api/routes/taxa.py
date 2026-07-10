"""Project-owned canonical taxonomy search for kid-facing identification."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, func, or_, select

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1/taxa", tags=["taxa"])


class TaxonSearchItem(BaseModel):
    taxon_id: int
    scientific_name: str | None
    common_name: str | None
    iconic_taxon: str | None
    rank: str | None
    ancestor_ids: list[int] = Field(default_factory=list)
    catalog_version: str


class TaxonSearchResponse(BaseModel):
    items: list[TaxonSearchItem]


class TaxonPackManifest(BaseModel):
    pack_id: str
    version: str
    scope: str
    checksum_sha256: str
    size_bytes: int
    taxon_count: int
    download_url: str
    expires_at: datetime


@router.get("/search", response_model=TaxonSearchResponse)
async def search_taxa(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> TaxonSearchResponse:
    """Search only the ingested catalog; never fall through to iNaturalist."""
    await resolve_current_user_row(session, current_user)
    normalized = q.strip().lower()
    if len(normalized) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Search must include at least two non-space characters",
        )
    pattern = f"%{normalized}%"
    exact_first = case(
        (
            or_(
                func.lower(models.SpeciesCache.common_name) == normalized,
                func.lower(models.SpeciesCache.scientific_name) == normalized,
            ),
            0,
        ),
        else_=1,
    )
    rows = (
        await session.execute(
            select(models.SpeciesCache)
            .where(
                models.SpeciesCache.active.is_(True),
                or_(
                    models.SpeciesCache.common_name.ilike(pattern),
                    models.SpeciesCache.scientific_name.ilike(pattern),
                    cast(models.SpeciesCache.aliases, String).ilike(pattern),
                ),
            )
            .order_by(
                exact_first,
                func.coalesce(
                    models.SpeciesCache.common_name,
                    models.SpeciesCache.scientific_name,
                ),
                models.SpeciesCache.taxon_id,
            )
            .limit(limit)
        )
    ).scalars()
    return TaxonSearchResponse(
        items=[
            TaxonSearchItem(
                taxon_id=row.taxon_id,
                scientific_name=row.scientific_name,
                common_name=row.common_name,
                iconic_taxon=row.iconic_taxon,
                rank=row.rank,
                ancestor_ids=list(row.ancestor_ids or []),
                catalog_version=row.catalog_version,
            )
            for row in rows
        ]
    )


@router.get("/packs/{pack_id}", response_model=TaxonPackManifest)
async def get_taxon_pack(
    pack_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: SignedUrlGeneratorDep,
) -> TaxonPackManifest:
    """Return the newest published immutable pack with a short-lived SAS."""
    await resolve_current_user_row(session, current_user)
    pack = (
        await session.execute(
            select(models.TaxonomyPack)
            .where(
                models.TaxonomyPack.pack_id == pack_id,
                models.TaxonomyPack.active.is_(True),
            )
            .order_by(models.TaxonomyPack.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Taxonomy pack not found")

    download_url, expires_at = storage.generate_get_url(
        bucket=pack.bucket,
        object_name=pack.object_name,
        expires_in=timedelta(minutes=15),
    )
    return TaxonPackManifest(
        pack_id=pack.pack_id,
        version=pack.version,
        scope=pack.scope,
        checksum_sha256=pack.checksum_sha256,
        size_bytes=pack.size_bytes,
        taxon_count=pack.taxon_count,
        download_url=download_url,
        expires_at=expires_at,
    )
