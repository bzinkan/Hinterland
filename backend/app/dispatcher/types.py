"""Dispatcher contracts: Reward, Context, HandlerResult, Handler.

Public surface frozen by `docs/dispatcher.md`. Anything that needs to
change here is an ADR-level decision since handlers form a Phase 2-4
ecosystem on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

RewardType = Literal[
    # Phase 1
    "first_find",
    "repeat_find",
    "expedition_step",
    "expedition_complete",
    "rarity_tier",
    "unrecorded",
    # Phase 2 -- Sanctuary (WorldHandler, docs/sanctuary.md sections 8-9)
    "world_unlock",
    "world_evolution",
    # Phase 2+ (defined now so the type union is forward-compatible)
    "territory_claimed",
    "season_hit",
    "mission_progress",
    "mission_complete",
]


@dataclass(frozen=True)
class Reward:
    """User-visible feedback unit. Immutable, serialized verbatim to client.

    `weight` is the sort order for the celebration sequence -- higher
    shown first. Convention table is in `docs/dispatcher.md`:
        100 = "never happened in the world before" (unrecorded)
         80 = "never happened for you before" (first_find)
         60 = rare/high-tier contextual (rarity_tier legendary/epic)
         40 = progress on a goal (expedition_step)
         30 = goal completion (expedition_complete)
         10 = ambient acknowledgment (repeat_find)
    """

    type: RewardType
    title: str
    detail: str
    icon: str
    weight: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandlerResult:
    """Output from one handler.

    `state` is handler-private structured data that downstream handlers
    can read off `Context.results[<earlier_handler_name>].state`.
    Define typed accessors as constants on the handler module
    (e.g. `DexHandler.STATE_IS_FIRST_FIND = "is_first_find"`) so the
    coupling is explicit.
    """

    rewards: list[Reward]
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """The universe handlers see.

    Built once per dispatch, passed by reference. Core fields are
    populated up front and never mutated. `results` is enriched as
    handlers complete -- only read keys for handlers that have already
    run (registry order is the contract).
    """

    db: AsyncSession
    user: models.User
    group: models.Group | None
    observation: models.Observation
    photo: models.Photo
    results: dict[str, HandlerResult] = field(default_factory=dict)


class Handler(Protocol):
    """A reward handler. Duck-typed so tests don't need to subclass.

    `name` MUST be unique across registered handlers (used as the
    Context.results key). It MUST be stable across releases (changing
    it breaks any downstream handler reading state from it).
    """

    name: str

    async def handle(self, ctx: Context) -> HandlerResult:
        """Run side effects and return rewards.

        Contract:
        - Idempotent (same observation twice -> same result, no double writes)
        - Self-contained (no external HTTP; the dispatcher's p95 budget is 300ms)
        - Exception-safe semantics (the dispatcher catches; handler shouldn't
          assume teardown won't run if it raises mid-call)
        """
        ...
