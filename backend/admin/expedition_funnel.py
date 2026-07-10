"""Engagement-funnel report over expedition progress.

For each expedition this reports: how many kids started it, how many
advanced past zero steps, how many finished, median minutes to finish,
and per-step completion counts in content order -- so the step where
kids drop off is obvious at a glance. Archived content is included
deliberately: historical funnel data still counts.

Run after a pilot (or any time you want a read on engagement):

    python -m admin.expedition_funnel                # all time
    python -m admin.expedition_funnel --days 14      # pilot window only
    python -m admin.expedition_funnel --days 14 --csv > funnel.csv

In Azure this runs as the manual Container Apps Job
`hinterland-expedition-funnel` (kick off with `az containerapp job
start`); the report goes to stdout, so read it from the job execution
logs.

Same admin-task pattern as rarity_refresh / dispatcher_replay:

    python -m admin.expedition_funnel
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models
from app.models.expedition import Expedition

log = structlog.get_logger()


@dataclass(frozen=True)
class FunnelRow:
    """One expedition's funnel numbers.

    `step_counts` is (step_id, times_completed) in content order, with a
    trailing ("orphaned_keys", n) entry when progress rows reference step
    ids the current content no longer has (content edits).
    """

    expedition_id: str
    title: str
    starts: int
    advanced: int
    completed: int
    completion_rate: float
    advance_rate: float
    median_minutes_to_complete: float | None
    step_counts: tuple[tuple[str, int], ...]


def summarize(
    pairs: list[tuple[models.ExpeditionProgress, models.ExpeditionContent]],
) -> list[FunnelRow]:
    """Aggregate (progress, content) pairs into one FunnelRow per expedition.

    Pure function -- all aggregation happens in Python (volumes are
    tiny), so tests can feed in-memory ORM rows directly. Any `--days`
    filtering happens in SQL before rows reach this function.
    """
    progress_by_expedition: dict[str, list[models.ExpeditionProgress]] = defaultdict(list)
    content_by_expedition: dict[str, models.ExpeditionContent] = {}
    for progress, content in pairs:
        progress_by_expedition[content.id].append(progress)
        content_by_expedition[content.id] = content

    rows: list[FunnelRow] = []
    for expedition_id, progresses in progress_by_expedition.items():
        content = content_by_expedition[expedition_id]
        try:
            exp = Expedition.model_validate(content.body)
            title = exp.title
            step_order = [step.id for step in exp.steps]
        except Exception:  # body got corrupted somehow; don't crash
            log.warning("expedition_funnel.bad_content", expedition_id=content.id)
            title = content.id
            step_order = sorted({key for p in progresses for key in (p.completed_steps or {})})

        starts = len(progresses)
        advanced = sum(1 for p in progresses if p.completed_steps)
        completed = sum(1 for p in progresses if p.completed_at is not None)

        # Key presence alone marks a step completed; the stored value
        # (dict or legacy string -- see app.services.expedition_progress)
        # only matters to readers that need timestamps per step.
        known_ids = set(step_order)
        step_counts = [
            (step_id, sum(1 for p in progresses if step_id in (p.completed_steps or {})))
            for step_id in step_order
        ]
        orphaned = sum(
            1 for p in progresses for key in (p.completed_steps or {}) if key not in known_ids
        )
        if orphaned:
            step_counts.append(("orphaned_keys", orphaned))

        # Restart-in-place re-anchors created_at, so this measures the
        # kid's CURRENT run, not time since their first attempt --
        # deliberate.
        durations: list[float] = []
        for p in progresses:
            if p.completed_at is not None:
                durations.append((p.completed_at - p.created_at).total_seconds() / 60.0)
        median_minutes = statistics.median(durations) if durations else None

        rows.append(
            FunnelRow(
                expedition_id=expedition_id,
                title=title,
                starts=starts,
                advanced=advanced,
                completed=completed,
                completion_rate=completed / starts,
                advance_rate=advanced / starts,
                median_minutes_to_complete=median_minutes,
                step_counts=tuple(step_counts),
            )
        )

    rows.sort(key=lambda row: (content_by_expedition[row.expedition_id].tier, row.expedition_id))
    return rows


async def load_pairs(
    session: AsyncSession, *, days: int | None
) -> list[tuple[models.ExpeditionProgress, models.ExpeditionContent]]:
    """Load all (progress, content) pairs, optionally windowed by start time.

    Deliberately no `archived` filter -- kids started (and finished)
    expeditions while that content was live, and the funnel should
    still count them.
    """
    stmt = (
        select(models.ExpeditionProgress, models.ExpeditionContent)
        .join(
            models.ExpeditionContent,
            models.ExpeditionProgress.expedition_id == models.ExpeditionContent.id,
        )
        .order_by(models.ExpeditionProgress.created_at)
    )
    if days is not None:
        stmt = stmt.where(
            models.ExpeditionProgress.created_at >= datetime.now(UTC) - timedelta(days=days)
        )
    result = await session.execute(stmt)
    return [(progress, content) for progress, content in result.all()]


def _format_steps(step_counts: tuple[tuple[str, int], ...]) -> str:
    return " ".join(f"{step_id}:{count}" for step_id, count in step_counts)


def print_table(rows: list[FunnelRow]) -> None:
    if not rows:
        print("no expedition progress rows found")
        return
    header = (
        "expedition",
        "title",
        "starts",
        "advanced",
        "completed",
        "advance%",
        "completion%",
        "median_min",
        "steps",
    )
    table = [header]
    for row in rows:
        median = row.median_minutes_to_complete
        table.append(
            (
                row.expedition_id,
                row.title,
                str(row.starts),
                str(row.advanced),
                str(row.completed),
                f"{row.advance_rate * 100:.1f}",
                f"{row.completion_rate * 100:.1f}",
                "-" if median is None else f"{median:.1f}",
                _format_steps(row.step_counts),
            )
        )
    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    for line in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)).rstrip())


def print_csv(rows: list[FunnelRow]) -> None:
    """One row per (expedition, step) with the summary columns repeated."""
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "expedition_id",
            "title",
            "starts",
            "advanced",
            "completed",
            "advance_rate",
            "completion_rate",
            "median_minutes_to_complete",
            "step_id",
            "step_completions",
        ]
    )
    for row in rows:
        median = row.median_minutes_to_complete
        summary = [
            row.expedition_id,
            row.title,
            str(row.starts),
            str(row.advanced),
            str(row.completed),
            f"{row.advance_rate:.4f}",
            f"{row.completion_rate:.4f}",
            "" if median is None else f"{median:.2f}",
        ]
        if not row.step_counts:
            writer.writerow([*summary, "", ""])
            continue
        for step_id, count in row.step_counts:
            writer.writerow([*summary, step_id, str(count)])


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only include progress rows started in the last N days (default: all time)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Emit CSV instead of the aligned text table",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            pairs = await load_pairs(session, days=args.days)
        rows = summarize(pairs)
        if args.csv:
            print_csv(rows)
        else:
            print_table(rows)
        log.info(
            "expedition_funnel.complete",
            expeditions=len(rows),
            progress_rows=len(pairs),
            days=args.days,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
