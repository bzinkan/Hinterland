#!/usr/bin/env python
"""Sync content/expeditions/*.json into the `expedition_content` table.

The repo is the source of truth; Postgres is a materialized view. Only
write path is this script. Per docs/expedition-authoring.md:

- Validates every file with the Pydantic model first (broken file ->
  abort the whole run, no partial writes)
- Computes a SHA-256 content_hash per expedition
- INSERT ... ON CONFLICT(id) DO UPDATE only when the hash differs
  (idempotent; re-running with no changes is a no-op)
- Never deletes -- tombstoning is manual per the doc

Run from a controlled deploy job or one-off admin machine with the
same DRAGONFLY_DATABASE_* env as the target Cloud Run service. Same
deployment pattern as `admin/cleanup_smoke_users.py` and the migrate
job.

Usage:
    python scripts/sync_expeditions.py
    python scripts/sync_expeditions.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _REPO_ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.models.expedition import Expedition  # noqa: E402

log = structlog.get_logger()


def _load_and_validate() -> list[tuple[Expedition, str]]:
    """Return (expedition, content_hash) for every JSON file under
    content/expeditions/. Aborts on the first validation failure."""
    content_root = _REPO_ROOT / "content" / "expeditions"
    files = sorted(content_root.rglob("*.json"))
    out: list[tuple[Expedition, str]] = []
    for path in files:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        exp = Expedition.model_validate(data)
        # Hash on the canonical Pydantic dump (key-sorted) so editor
        # whitespace doesn't churn the hash.
        canonical = json.dumps(exp.model_dump(mode="json"), sort_keys=True).encode(
            "utf-8"
        )
        content_hash = hashlib.sha256(canonical).hexdigest()
        out.append((exp, content_hash))
    return out


async def _upsert(session: AsyncSession, exp: Expedition, content_hash: str) -> str:
    """Returns 'inserted' / 'updated' / 'unchanged'."""
    existing = (
        await session.execute(
            select(models.ExpeditionContent).where(
                models.ExpeditionContent.id == exp.id
            )
        )
    ).scalar_one_or_none()

    body = exp.model_dump(mode="json")

    if existing is None:
        session.add(
            models.ExpeditionContent(
                id=exp.id,
                tier=exp.tier,
                content_hash=content_hash,
                body=body,
                archived=False,
            )
        )
        return "inserted"

    if existing.content_hash == content_hash:
        return "unchanged"

    existing.tier = exp.tier
    existing.content_hash = content_hash
    existing.body = body
    existing.archived = False
    return "updated"


async def main(dry_run: bool) -> int:
    expeditions = _load_and_validate()
    if not expeditions:
        print("No expeditions to sync.")
        return 0

    if dry_run:
        for exp, h in expeditions:
            print(f"[dry-run] would consider {exp.id} (hash {h[:12]})")
        return 0

    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    try:
        async with sessions() as session:
            for exp, content_hash in expeditions:
                result = await _upsert(session, exp, content_hash)
                counts[result] += 1
                log.info(
                    "expeditions.sync.row",
                    id=exp.id,
                    tier=exp.tier,
                    result=result,
                )
            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"sync complete: {counts['inserted']} inserted, "
        f"{counts['updated']} updated, {counts['unchanged']} unchanged"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
