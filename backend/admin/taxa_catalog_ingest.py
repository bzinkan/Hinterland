"""Replayable project-owned taxonomy pack ingest.

The kid-facing search endpoint reads only Postgres. This job imports a reviewed
pack (the bundled global core by default) and records the run in ``ingest_runs``.
Larger global/regional packs use the same JSON contract and command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from ulid import ULID

from app.core.config import get_settings
from app.core.storage import BlobSignedUrlGenerator
from app.db import models

log = structlog.get_logger()


class TaxonRecord(BaseModel):
    taxon_id: int = Field(gt=0)
    scientific_name: str = Field(min_length=1, max_length=200)
    common_name: str | None = Field(default=None, max_length=200)
    rank: str | None = Field(default=None, max_length=40)
    iconic_taxon: str | None = Field(default=None, max_length=80)
    ancestor_ids: list[int] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class TaxonPack(BaseModel):
    pack_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_-]+$")
    version: str = Field(min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=80)
    taxa: list[TaxonRecord]


def load_pack(path: Path) -> tuple[TaxonPack, str]:
    raw = path.read_bytes()
    pack = TaxonPack.model_validate_json(raw)
    ids = [record.taxon_id for record in pack.taxa]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate taxon_id in {path}")
    return pack, hashlib.sha256(raw).hexdigest()


def _default_pack_path(*, cwd: Path | None = None, module_file: Path | None = None) -> Path:
    """Resolve the core pack in both the source tree and the API image."""
    working_directory = cwd or Path.cwd()
    module_path = (module_file or Path(__file__)).resolve()
    relative_pack = Path("content") / "taxa" / "core.json"
    candidates = (
        working_directory / relative_pack,
        module_path.parents[1] / relative_pack,
        module_path.parents[2] / relative_pack,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


async def ingest(
    session: AsyncSession,
    *,
    pack: TaxonPack,
    checksum: str,
    size_bytes: int,
    bucket: str,
    object_name: str,
    published: bool,
) -> bool:
    source_run_id = f"{pack.pack_id}:{pack.version}"
    existing = (
        await session.execute(
            select(models.IngestRun).where(
                models.IngestRun.source == "species_taxa",
                models.IngestRun.source_run_id == source_run_id,
            )
        )
    ).scalar_one_or_none()
    published_pack = (
        await session.execute(
            select(models.TaxonomyPack).where(
                models.TaxonomyPack.pack_id == pack.pack_id,
                models.TaxonomyPack.version == pack.version,
            )
        )
    ).scalar_one_or_none()
    already_published = (
        published_pack is not None
        and published_pack.active
        and published_pack.checksum_sha256 == checksum
    )
    if (
        existing is not None
        and existing.status == "succeeded"
        and existing.checksum == checksum
        and (not published or already_published)
    ):
        log.info("taxa_catalog_ingest.already_applied", source_run_id=source_run_id)
        return False

    if existing is None:
        run = models.IngestRun(
            id=str(ULID()),
            source="species_taxa",
            source_run_id=source_run_id,
            status="running",
            checksum=checksum,
            cursor={"pack_id": pack.pack_id, "version": pack.version},
            retry_count=0,
            started_at=datetime.now(UTC),
        )
        session.add(run)
    else:
        run = existing
        run.status = "running"
        run.checksum = checksum
        run.retry_count += 1
        run.last_error = None
        run.started_at = datetime.now(UTC)
        run.completed_at = None
    await session.commit()

    try:
        for record in pack.taxa:
            payload: dict[str, Any] = record.model_dump()
            stmt = pg_insert(models.SpeciesCache).values(
                taxon_id=record.taxon_id,
                scientific_name=record.scientific_name,
                common_name=record.common_name,
                iconic_taxon=record.iconic_taxon,
                rank=record.rank,
                ancestor_ids=record.ancestor_ids,
                aliases=record.aliases,
                active=True,
                catalog_version=pack.version,
                source_updated_at=datetime.now(UTC),
                source_payload=payload,
                expires_at=None,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["taxon_id"],
                    set_={
                        "scientific_name": stmt.excluded.scientific_name,
                        "common_name": stmt.excluded.common_name,
                        "iconic_taxon": stmt.excluded.iconic_taxon,
                        "rank": stmt.excluded.rank,
                        "ancestor_ids": stmt.excluded.ancestor_ids,
                        "aliases": stmt.excluded.aliases,
                        "active": stmt.excluded.active,
                        "catalog_version": stmt.excluded.catalog_version,
                        "source_updated_at": stmt.excluded.source_updated_at,
                        "source_payload": stmt.excluded.source_payload,
                        "expires_at": None,
                    },
                )
            )

        if published:
            await session.execute(
                update(models.TaxonomyPack)
                .where(models.TaxonomyPack.pack_id == pack.pack_id)
                .values(active=False)
            )
        pack_stmt = pg_insert(models.TaxonomyPack).values(
            id=str(ULID()),
            pack_id=pack.pack_id,
            version=pack.version,
            scope=pack.scope,
            checksum_sha256=checksum,
            size_bytes=size_bytes,
            taxon_count=len(pack.taxa),
            bucket=bucket,
            object_name=object_name,
            active=published,
        )
        await session.execute(
            pack_stmt.on_conflict_do_update(
                constraint="uq_taxonomy_packs_id_version",
                set_={
                    "scope": pack_stmt.excluded.scope,
                    "checksum_sha256": pack_stmt.excluded.checksum_sha256,
                    "size_bytes": pack_stmt.excluded.size_bytes,
                    "taxon_count": pack_stmt.excluded.taxon_count,
                    "bucket": pack_stmt.excluded.bucket,
                    "object_name": pack_stmt.excluded.object_name,
                    "active": pack_stmt.excluded.active,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        run.status = "succeeded"
        run.completed_at = datetime.now(UTC)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        failed = (
            await session.execute(
                select(models.IngestRun).where(
                    models.IngestRun.source == "species_taxa",
                    models.IngestRun.source_run_id == source_run_id,
                )
            )
        ).scalar_one()
        failed.status = "failed"
        failed.last_error = f"{type(exc).__name__}: {exc}"[:4000]
        failed.completed_at = datetime.now(UTC)
        await session.commit()
        raise

    log.info(
        "taxa_catalog_ingest.succeeded",
        source_run_id=source_run_id,
        taxon_count=len(pack.taxa),
        checksum=checksum,
    )
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pack",
        nargs="?",
        type=Path,
        default=_default_pack_path(),
    )
    parser.add_argument(
        "--database-only",
        action="store_true",
        help="Import search rows but leave the download manifest inactive.",
    )
    return parser.parse_args(argv)


async def main() -> None:
    args = _parse_args()
    pack, checksum = load_pack(args.pack)
    raw = args.pack.read_bytes()
    settings = get_settings()
    object_name = f"packs/{pack.pack_id}/{pack.version}/{checksum}.json"
    published = not args.database_only
    if published:
        if not settings.blob_account_endpoint:
            raise RuntimeError(
                "Publishing a taxonomy pack requires HINTERLAND_BLOB_ACCOUNT_ENDPOINT; "
                "use --database-only for a local search-only import"
            )
        storage = BlobSignedUrlGenerator(settings.blob_account_endpoint)
        await asyncio.to_thread(
            storage.put_object_bytes,
            bucket=settings.taxonomy_packs_bucket,
            object_name=object_name,
            data=raw,
            content_type="application/json",
            metadata={
                "sha256": checksum,
                "pack_id": pack.pack_id,
                "version": pack.version,
            },
            overwrite=False,
            expected_sha256=checksum,
        )
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        async with sessions() as session:
            changed = await ingest(
                session,
                pack=pack,
                checksum=checksum,
                size_bytes=len(raw),
                bucket=settings.taxonomy_packs_bucket,
                object_name=object_name,
                published=published,
            )
        print(f"taxa_catalog_ingest: {'applied' if changed else 'already current'}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
