"""Container Apps Job entry point for nightly rarity refresh.

Same image + invocation pattern as cleanup_smoke_users:

    python -m admin.rarity_refresh

Runtime config: same `HINTERLAND_DATABASE_*` env as the API, plus
`HINTERLAND_INAT_OAUTH_TOKEN` (without it the iNat calls 401 and every
region is skipped -- safe but useless).
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.inat.client import build_inat_client
from app.rarity.refresh import run_refresh

log = structlog.get_logger()


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    inat_client = build_inat_client(settings)

    try:
        async with sessions() as session:
            results = await run_refresh(session, inat_client)
        log.info(
            "rarity.job.complete",
            regions_processed=len(results),
            low_data_regions=sum(1 for r in results if r.low_data),
        )
    finally:
        await inat_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
