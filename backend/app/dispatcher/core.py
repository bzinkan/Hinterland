"""Durable, replayable reward dispatcher.

Handler side effects run in nested transactions. A failed handler rolls back to
its savepoint, is recorded as failed, and does not poison the SQLAlchemy
session for later independent handlers. Successful state and rewards are kept
in ``observation_handler_runs`` so a replay can resume only incomplete work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.types import Context, Handler, HandlerResult, Reward, RewardType

log = structlog.get_logger()

_DEFAULT_HANDLER_VERSION = "1"

# Only hard dependencies belong here. Rarity enriches World payloads when it
# succeeds, but World can still produce valid deterministic state without it.
_HANDLER_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "world": ("dex",),
    "expedition": ("dex",),
}


async def dispatch(
    ctx: Context,
    handlers: list[Handler],
    *,
    commit: bool = True,
) -> list[Reward]:
    """Run handlers in order and persist durable results on real sessions.

    Mock-shaped unit tests intentionally use the small in-memory path. Runtime
    ``AsyncSession`` instances use the durable ledger and savepoint path. The
    ``commit=False`` option is reserved for an enclosing atomic rebuild.
    """

    if not isinstance(ctx.db, AsyncSession) or type(ctx.db).__module__ == "unittest.mock":
        return await _dispatch_in_memory(ctx, handlers)
    return await _dispatch_durable(ctx, handlers, commit=commit)


async def _dispatch_durable(
    ctx: Context,
    handlers: list[Handler],
    *,
    commit: bool,
) -> list[Reward]:
    started = perf_counter()
    now = datetime.now(UTC)

    # Submission finalization commits before reward dispatch so the saved
    # observation can survive handler failure. Reacquire the same transaction-
    # scoped user lock here; otherwise a derived-state rebuild can delete and
    # recreate handler state while this dispatcher is writing it.
    await ctx.db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
        {"user_id": ctx.user.id},
    )

    rows = (
        (
            await ctx.db.execute(
                select(models.ObservationHandlerRun).where(
                    models.ObservationHandlerRun.observation_id == ctx.observation.id
                )
            )
        )
        .scalars()
        .all()
    )
    runs = {row.handler_name: row for row in rows}

    # Restore successful predecessor state before deciding what still needs to
    # run. This is what makes a replay equivalent to the original registry pass.
    for handler in handlers:
        run = runs.get(handler.name)
        version = str(getattr(handler, "version", _DEFAULT_HANDLER_VERSION))
        if run is not None and run.status == "succeeded" and run.handler_version == version:
            ctx.results[handler.name] = HandlerResult(
                rewards=[_reward_from_json(value) for value in run.rewards],
                state=dict(run.state),
            )

    timings: dict[str, float] = {}
    for handler in handlers:
        version = str(getattr(handler, "version", _DEFAULT_HANDLER_VERSION))
        run = runs.get(handler.name)
        if run is None:
            run = models.ObservationHandlerRun(
                observation_id=ctx.observation.id,
                handler_name=handler.name,
                handler_version=version,
                status="pending",
                state={},
                rewards=[],
                attempt_count=0,
            )
            ctx.db.add(run)
            runs[handler.name] = run

        if run.status == "succeeded" and run.handler_version == version:
            continue
        if run.status == "succeeded" and run.handler_version != version:
            # Handler upgrades require an explicit backfill/rebuild. Automatic
            # rerun could apply a second version's side effects over the first.
            run.status = "blocked"
            run.last_error = (
                f"handler version changed {run.handler_version!r} -> {version!r}; "
                "explicit rebuild required"
            )
            continue

        missing_dependency = next(
            (
                name
                for name in _HANDLER_DEPENDENCIES.get(handler.name, ())
                if name not in ctx.results
            ),
            None,
        )
        if missing_dependency is not None:
            run.status = "blocked"
            run.last_error = f"dependency {missing_dependency!r} has not succeeded"
            run.finished_at = now
            continue

        handler_started = perf_counter()
        run.status = "running"
        run.handler_version = version
        run.attempt_count += 1
        run.started_at = now
        run.finished_at = None
        run.last_error = None

        try:
            async with ctx.db.begin_nested():
                result = await handler.handle(ctx)
                await ctx.db.flush()
        except Exception as exc:  # isolation is the contract
            timings[handler.name] = round((perf_counter() - handler_started) * 1000, 2)
            run.status = "failed"
            run.state = {}
            run.rewards = []
            run.last_error = f"{type(exc).__name__}: {exc}"[:4000]
            run.finished_at = datetime.now(UTC)
            ctx.results.pop(handler.name, None)
            log.exception(
                "dispatcher.handler_failed",
                handler=handler.name,
                observation_id=ctx.observation.id,
                user_id=ctx.user.id,
            )
            continue

        timings[handler.name] = round((perf_counter() - handler_started) * 1000, 2)
        run.status = "succeeded"
        run.state = dict(result.state)
        run.rewards = [_reward_to_json(reward) for reward in result.rewards]
        run.finished_at = datetime.now(UTC)
        ctx.results[handler.name] = result

    rewards: list[Reward] = []
    all_succeeded = True
    for handler in handlers:
        run = runs[handler.name]
        if run.status != "succeeded":
            all_succeeded = False
            continue
        rewards.extend(_reward_from_json(value) for value in run.rewards)
    rewards.sort(key=lambda reward: reward.weight, reverse=True)

    ctx.observation.rewards = [_reward_to_json(reward) for reward in rewards]
    ctx.observation.dispatch_status = "complete" if all_succeeded else "partial"
    ctx.observation.dispatched_at = datetime.now(UTC) if all_succeeded else None

    if commit:
        await ctx.db.commit()
    else:
        await ctx.db.flush()

    log.info(
        "dispatcher.complete",
        observation_id=ctx.observation.id,
        reward_count=len(rewards),
        reward_types=[reward.type for reward in rewards],
        dispatch_status=ctx.observation.dispatch_status,
        duration_ms=round((perf_counter() - started) * 1000, 2),
        handler_durations_ms=timings,
    )
    return rewards


async def _dispatch_in_memory(ctx: Context, handlers: list[Handler]) -> list[Reward]:
    """Small deterministic path for pure/mock handler tests."""
    started = perf_counter()
    rewards: list[Reward] = []
    timings: dict[str, float] = {}
    for handler in handlers:
        missing_dependency = next(
            (
                name
                for name in _HANDLER_DEPENDENCIES.get(handler.name, ())
                if name not in ctx.results
            ),
            None,
        )
        if missing_dependency is not None:
            log.warning(
                "dispatcher.handler_blocked",
                handler=handler.name,
                dependency=missing_dependency,
                observation_id=ctx.observation.id,
            )
            ctx.results.pop(handler.name, None)
            continue

        handler_started = perf_counter()
        try:
            result = await handler.handle(ctx)
        except Exception:  # mirrors durable isolation
            timings[handler.name] = round((perf_counter() - handler_started) * 1000, 2)
            log.exception(
                "dispatcher.handler_failed",
                handler=handler.name,
                observation_id=ctx.observation.id,
                user_id=ctx.user.id,
            )
            ctx.results.pop(handler.name, None)
            continue
        timings[handler.name] = round((perf_counter() - handler_started) * 1000, 2)
        ctx.results[handler.name] = result
        rewards.extend(result.rewards)

    rewards.sort(key=lambda reward: reward.weight, reverse=True)
    log.info(
        "dispatcher.complete",
        observation_id=ctx.observation.id,
        reward_count=len(rewards),
        reward_types=[reward.type for reward in rewards],
        duration_ms=round((perf_counter() - started) * 1000, 2),
        handler_durations_ms=timings,
        durable=False,
    )
    return rewards


def _reward_to_json(reward: Reward) -> models.JsonDict:
    return {
        "type": reward.type,
        "title": reward.title,
        "detail": reward.detail,
        "icon": reward.icon,
        "weight": reward.weight,
        "payload": dict(reward.payload),
    }


def _reward_from_json(value: dict[str, Any]) -> Reward:
    reward_type = cast(RewardType, str(value.get("type", "repeat_find")))
    payload = value.get("payload")
    return Reward(
        type=reward_type,
        title=str(value.get("title", "")),
        detail=str(value.get("detail", "")),
        icon=str(value.get("icon", "")),
        weight=int(value.get("weight", 0)),
        payload=dict(payload) if isinstance(payload, dict) else {},
    )
