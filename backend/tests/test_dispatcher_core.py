"""Unit tests for the dispatcher core (exception isolation + sort order)."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.db import models
from app.dispatcher.core import dispatch
from app.dispatcher.types import Context, Handler, HandlerResult, Reward


def _ctx() -> Context:
    user = models.User(id="u-1", firebase_uid="fb-1", role="kid", display_name="K")
    obs = models.Observation(
        id="o-1",
        user_id=user.id,
        group_id="g-1",
        photo_id="p-1",
        latitude=39.1,
        longitude=-84.5,
    )
    photo = models.Photo(
        id="p-1",
        user_id=user.id,
        bucket="b",
        object_name="pending/p-1.jpg",
        status="pending",
    )
    return Context(
        db=MagicMock(),
        user=user,
        group=None,
        observation=obs,
        photo=photo,
    )


class _GoodHandler:
    def __init__(self, name: str, weight: int) -> None:
        self.name = name
        self._weight = weight

    async def handle(self, ctx: Context) -> HandlerResult:
        return HandlerResult(
            rewards=[
                Reward(
                    type="repeat_find",
                    title=self.name,
                    detail="d",
                    icon="i",
                    weight=self._weight,
                )
            ]
        )


class _BoomHandler:
    name = "boom"

    async def handle(self, ctx: Context) -> HandlerResult:
        raise RuntimeError("intentional")


async def test_dispatch_returns_rewards_sorted_by_weight_desc() -> None:
    handlers: list[Handler] = [
        _GoodHandler("low", weight=10),
        _GoodHandler("high", weight=80),
        _GoodHandler("mid", weight=40),
    ]
    rewards = await dispatch(_ctx(), handlers)
    assert [r.weight for r in rewards] == [80, 40, 10]


async def test_dispatch_isolates_handler_exceptions() -> None:
    handlers: list[Handler] = [
        _GoodHandler("first", weight=10),
        _BoomHandler(),
        _GoodHandler("third", weight=20),
    ]
    rewards = await dispatch(_ctx(), handlers)
    # Two surviving rewards
    assert len(rewards) == 2
    assert {r.title for r in rewards} == {"first", "third"}


async def test_dispatch_does_not_fabricate_result_for_failed_handler() -> None:
    ctx = _ctx()
    handlers: list[Handler] = [_BoomHandler(), _GoodHandler("after", weight=1)]
    await dispatch(ctx, handlers)
    assert "boom" not in ctx.results


async def test_dispatch_blocks_handler_when_required_predecessor_failed() -> None:
    class BoomDex:
        name = "dex"

        async def handle(self, ctx: Context) -> HandlerResult:
            raise RuntimeError("dex failed")

    ctx = _ctx()
    rewards = await dispatch(
        ctx,
        [BoomDex(), _GoodHandler("world", weight=50)],  # type: ignore[list-item]
    )

    assert rewards == []
    assert "dex" not in ctx.results
    assert "world" not in ctx.results


async def test_equal_weights_preserve_handler_registration_order() -> None:
    """Ties resolve by handler registration order (stable sort)."""
    handlers: list[Handler] = [
        _GoodHandler("first", weight=50),
        _GoodHandler("second", weight=50),
        _GoodHandler("third", weight=50),
    ]
    rewards = await dispatch(_ctx(), handlers)
    assert [r.title for r in rewards] == ["first", "second", "third"]


async def test_dispatch_empty_handler_list() -> None:
    rewards = await dispatch(_ctx(), [])
    assert rewards == []
