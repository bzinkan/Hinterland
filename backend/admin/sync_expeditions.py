"""Sync expedition content JSON into the `expedition_content` table.

The repo is the source of truth; Postgres is a materialized view. The
content ships INSIDE the deployed image (backend/Dockerfile copies
content/expeditions/ to /app/content/expeditions), so the image version
IS the content version. Per docs/expedition-authoring.md:

- Validates every file with the Pydantic model first (broken file ->
  abort the whole run, no writes)
- Computes a SHA-256 content_hash per expedition
- Inserts missing rows (archived=False on INSERT only), updates
  tier/content_hash/body only when the hash differs (idempotent;
  re-running with no changes is a no-op)
- Never deletes and never resurrects: `archived` is untouched on
  update. Tombstoning stays manual; un-tombstoning is the explicit
  `--unarchive <expedition_id>` flag (repeatable). An --unarchive id
  that matches nothing fails the run (exit 1) AFTER the rest of the
  sync completes -- a typo'd id silently no-oping is how tombstones
  get "revived" without anyone noticing it didn't happen.

In Azure this runs as the manual Container Apps Job
`hinterland-sync-expeditions`, started after each deploy when provisioned (see
docs/runbook.md). Local dev goes through the scripts/sync_expeditions.py
shim, which points `DRAGONFLY_CONTENT_ROOT` at the repo checkout:

    python -m admin.sync_expeditions
    python -m admin.sync_expeditions --dry-run
    python -m admin.sync_expeditions --unarchive backyard_starter
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models
from app.models.expedition import Expedition

log = structlog.get_logger()


class ContentValidationError(Exception):
    """A content file failed JSON parsing or schema validation."""


def _content_hash(exp: Expedition) -> str:
    # Hash the canonical Pydantic dump (key-sorted) so editor whitespace
    # doesn't churn the hash.
    canonical = json.dumps(exp.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_and_validate(root: Path) -> list[tuple[Expedition, str]]:
    """Return (expedition, content_hash) for every JSON file under `root`.

    Raises ContentValidationError on the first broken file -- callers
    must not write anything when any file is invalid.
    """
    out: list[tuple[Expedition, str]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            exp = Expedition.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ContentValidationError(f"{path}: {exc}") from exc
        out.append((exp, _content_hash(exp)))
    return out


async def sync(
    session: AsyncSession,
    expeditions: list[tuple[Expedition, str]],
    *,
    unarchive_ids: Sequence[str] = (),
    dry_run: bool = False,
) -> tuple[dict[str, int], list[str]]:
    """Upsert validated expeditions; returns (counts per action, unknown
    --unarchive ids).

    With dry_run=True the reads still run but nothing is added, mutated,
    or committed -- only the planned actions are logged. Unknown
    unarchive ids don't stop the run here; `main` turns them into a
    nonzero exit after everything else has been processed.
    """
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "unarchived": 0}
    unknown_ids: list[str] = []

    for exp, content_hash in expeditions:
        existing = (
            await session.execute(
                select(models.ExpeditionContent).where(models.ExpeditionContent.id == exp.id)
            )
        ).scalar_one_or_none()

        if existing is None:
            result = "inserted"
            if not dry_run:
                session.add(
                    models.ExpeditionContent(
                        id=exp.id,
                        tier=exp.tier,
                        content_hash=content_hash,
                        body=exp.model_dump(mode="json"),
                        archived=False,
                    )
                )
        elif existing.content_hash == content_hash:
            result = "unchanged"
        else:
            result = "updated"
            if not dry_run:
                # `archived` is deliberately untouched: a hash drift on a
                # tombstoned expedition must not resurrect it. Use
                # --unarchive for that.
                existing.tier = exp.tier
                existing.content_hash = content_hash
                existing.body = exp.model_dump(mode="json")

        counts[result] += 1
        log.info(
            "sync_expeditions.row",
            id=exp.id,
            tier=exp.tier,
            result=result,
            dry_run=dry_run,
        )

    for expedition_id in unarchive_ids:
        existing = (
            await session.execute(
                select(models.ExpeditionContent).where(models.ExpeditionContent.id == expedition_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            log.warning("sync_expeditions.unarchive_unknown_id", expedition_id=expedition_id)
            unknown_ids.append(expedition_id)
            continue
        if not existing.archived:
            log.info("sync_expeditions.unarchive_already_active", expedition_id=expedition_id)
            continue
        if not dry_run:
            existing.archived = False
        counts["unarchived"] += 1
        log.info("sync_expeditions.unarchived", expedition_id=expedition_id, dry_run=dry_run)

    if not dry_run:
        await session.commit()
    return counts, unknown_ids


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned actions without writing anything",
    )
    parser.add_argument(
        "--unarchive",
        action="append",
        default=[],
        metavar="EXPEDITION_ID",
        help="Explicitly set archived=False for this expedition id (repeatable)",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    content_root = Path(settings.content_root)

    # A missing root is a packaging regression (bad DRAGONFLY_CONTENT_ROOT,
    # or the Dockerfile/.dockerignore stopped shipping content/expeditions),
    # not an empty catalog -- fail loud instead of "nothing to sync".
    if not content_root.is_dir():
        log.error("sync_expeditions.content_root_missing", content_root=str(content_root))
        return 1

    try:
        expeditions = load_and_validate(content_root)
    except ContentValidationError as exc:
        log.error("sync_expeditions.validation_failed", error=str(exc))
        return 1

    if not expeditions and not args.unarchive:
        print(f"No expeditions found under {content_root}; nothing to sync.")
        return 0

    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            counts, unknown_ids = await sync(
                session,
                expeditions,
                unarchive_ids=args.unarchive,
                dry_run=args.dry_run,
            )
    finally:
        await engine.dispose()

    log.info("sync_expeditions.synced", dry_run=args.dry_run, **counts)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}sync complete: {counts['inserted']} inserted, "
        f"{counts['updated']} updated, {counts['unchanged']} unchanged, "
        f"{counts['unarchived']} unarchived"
    )
    if unknown_ids:
        # An explicit operator action that matched nothing must fail the
        # run: a typo'd --unarchive silently no-oping is how tombstones
        # get "revived" without anyone noticing they typoed. The rest of
        # the sync above still landed (and committed) before this exit.
        log.error("sync_expeditions.unarchive_unknown_ids", expedition_ids=unknown_ids)
        print(
            f"{prefix}error: --unarchive id(s) not found: {', '.join(unknown_ids)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
