"""WorldHandler -- Phase 2 Sanctuary writes + world_unlock/world_evolution rewards.

Owns:

- ``sanctuary_observation_contributions`` (the per-observation replay gate)
- ``sanctuary_zone_state`` (per-user observation_count + depth_tier upsert)
- ``sanctuary_elements`` (atomic first-fire on (user_id, zone_id, element_id))
- ``sanctuary_events`` (append-only celebration log)
- ``world_unlock`` / ``world_evolution`` rewards

Does NOT own:

- ``memberships`` columns (leaderboard counters stay on membership rows per
  AGENTS.md)
- Any external HTTP (no iNat, no Maps, no moderation provider, no LLM --
  per ``docs/sanctuary.md`` section 9 invariants)

Ordering (per ``registry.py``): runs after ``DexHandler`` + ``RarityHandler``
and before ``ExpeditionHandler``. Reads
``ctx.results["dex"].state["is_first_find"]`` to gate coarse-zone unlocks
and MAY read ``ctx.results["rarity"].state["tier"]`` for payload enrichment
only -- the planner does NOT branch on tier.

Per AGENTS.md non-negotiables:

- First-fire uses an atomic conditional write (``INSERT ... ON CONFLICT
  DO NOTHING RETURNING``). Never introduce a read-then-write here.
- The kid path must not depend on Sanctuary success. Internal exceptions
  return an empty ``HandlerResult`` so the dispatcher's outer catch-all
  has nothing left to do; the observation submission still succeeds.
- No external HTTP. This handler reads Postgres and writes Postgres.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ulid import ULID

from app.db import models
from app.dispatcher.types import Context, HandlerResult, Reward, RewardType
from app.sanctuary import (
    ElementSnapshot,
    ObservationInput,
    ServiceInputs,
    ZoneStateSnapshot,
    compute_sanctuary_plan,
    get_sanctuary_content,
)

log = structlog.get_logger()

# Brief override of docs/sanctuary.md section 8: world_unlock lands at
# weight 60 (the "rare / high-tier contextual" band, same as
# rarity_tier legendary). The planner emits world_unlock at its own
# constant; this handler re-stamps the weight before forwarding to the
# dispatcher. world_evolution stays at the planner's 30.
_WORLD_UNLOCK_REWARD_WEIGHT = 60
_WORLD_EVOLUTION_REWARD_WEIGHT = 30

# Bare string match for the DexHandler.STATE_IS_FIRST_FIND key. Avoids
# importing ``DexHandler`` from this module -- world.py is a sibling of
# dex.py and a future shared-types extraction must not introduce an
# import cycle. The key is part of the dispatcher's published handler-
# state contract (see ``docs/dispatcher.md``).
_DEX_STATE_IS_FIRST_FIND = "is_first_find"
_RARITY_STATE_TIER = "tier"


class WorldHandler:
    name = "world"

    # Public state keys -- downstream handlers reference these constants
    # instead of bare strings.
    STATE_CONTRIBUTION_ID = "contribution_id"
    STATE_ZONE_ID = "zone_id"
    STATE_BEFORE_TIER = "before_tier"
    STATE_AFTER_TIER = "after_tier"
    STATE_CROSSED_THRESHOLDS = "crossed_thresholds"
    STATE_TIER_HINT = "tier_hint"
    STATE_REPLAY = "replay"
    STATE_ERROR = "error"

    async def handle(self, ctx: Context) -> HandlerResult:
        try:
            return await self._handle_inner(ctx)
        except Exception:
            # Belt-and-suspenders on top of the dispatcher's catch-all:
            # the kid-facing submission must not be derailed by a
            # Sanctuary failure.
            log.exception(
                "dispatcher.world.failed",
                observation_id=ctx.observation.id,
                user_id=ctx.user.id,
            )
            return HandlerResult(rewards=[], state={self.STATE_ERROR: True})

    async def _handle_inner(self, ctx: Context) -> HandlerResult:
        obs = ctx.observation

        is_first_find = self._read_is_first_find(ctx)
        tier_hint = self._read_rarity_tier(ctx)
        iconic_taxon = await self._resolve_iconic_taxon(ctx, obs.taxon_id)

        zone_states = await self._load_zone_states(ctx)
        elements = await self._load_elements(ctx)

        observation_input = ObservationInput(
            user_id=ctx.user.id,
            observation_id=obs.id,
            taxon_id=obs.taxon_id,
            species_name=obs.species_name,
            iconic_taxon=iconic_taxon,
            is_first_find=is_first_find,
            current_date=obs.created_at.date(),
        )
        plan = compute_sanctuary_plan(
            ServiceInputs(
                observation=observation_input,
                zone_states=zone_states,
                elements=elements,
            ),
            get_sanctuary_content(),
        )

        # Replay gate -- FIRST DB write. The PK on observation_id means
        # a second dispatch hits ON CONFLICT and the handler short-
        # circuits with no counter bumps, no element fires, no events.
        element_ids_for_contribution = [eu.element_id for eu in plan.elements_to_unlock]
        contribution_stmt = (
            pg_insert(models.SanctuaryObservationContribution)
            .values(
                observation_id=obs.id,
                user_id=ctx.user.id,
                zone_id=plan.contribution_zone_id,
                taxon_id=obs.taxon_id,
                iconic_taxon=iconic_taxon,
                element_ids=element_ids_for_contribution,
            )
            .on_conflict_do_nothing(index_elements=["observation_id"])
            .returning(models.SanctuaryObservationContribution.observation_id)
        )
        contribution_id = (await ctx.db.execute(contribution_stmt)).scalar_one_or_none()
        if contribution_id is None:
            # Replay: dispatcher already processed this observation.
            await ctx.db.commit()
            log.info(
                "dispatcher.world.replay",
                observation_id=obs.id,
                user_id=ctx.user.id,
            )
            return HandlerResult(
                rewards=[],
                state={
                    self.STATE_CONTRIBUTION_ID: None,
                    self.STATE_REPLAY: True,
                },
            )

        # Apply the zone transition. The planner guarantees exactly one
        # transition per dispatch (for ``plan.contribution_zone_id``).
        transition = plan.zone_transitions[0]
        await self._upsert_zone_state(
            ctx,
            zone_id=transition.zone_id,
            after_count=transition.after_count,
            after_tier=transition.after_tier,
            crossed_any_threshold=bool(transition.crossed_thresholds),
            source_observation_id=obs.id,
        )

        # Fire elements with atomic conflict-skip. Track which
        # element_ids actually inserted so we can suppress events +
        # rewards for already-owned elements.
        inserted_element_ids: set[str] = set()
        for element in plan.elements_to_unlock:
            element_stmt = (
                pg_insert(models.SanctuaryElement)
                .values(
                    id=str(ULID()),
                    user_id=ctx.user.id,
                    zone_id=element.zone_id,
                    element_id=element.element_id,
                    element_type=element.element_type,
                    source_observation_id=obs.id,
                    taxon_id=element.taxon_id,
                    payload=element.payload,
                    unlocked_at=obs.created_at,
                )
                .on_conflict_do_nothing(
                    constraint="uq_sanctuary_elements_user_zone_element",
                )
                .returning(models.SanctuaryElement.element_id)
            )
            inserted = (await ctx.db.execute(element_stmt)).scalar_one_or_none()
            if inserted is not None:
                inserted_element_ids.add(inserted)

        # Append events. Skip element-keyed events when the element
        # conflicted (already-owned).
        for event in plan.events:
            if event.element_id is not None and event.element_id not in inserted_element_ids:
                continue
            event_stmt = pg_insert(models.SanctuaryEvent).values(
                id=str(ULID()),
                user_id=ctx.user.id,
                observation_id=obs.id,
                event_type=event.event_type,
                zone_id=event.zone_id,
                element_id=event.element_id,
                title=event.title,
                detail=event.detail,
                payload=event.payload,
            )
            await ctx.db.execute(event_stmt)

        # Re-wrap planner rewards as dispatcher rewards. The planner
        # emits at its own weights; we re-stamp the canonical dispatcher
        # weights here (world_unlock=60, world_evolution=30).
        dispatcher_rewards: list[Reward] = []
        for plan_reward in plan.rewards:
            element_id = plan_reward.payload.get("element_id") if plan_reward.payload else None
            if (
                isinstance(element_id, str)
                and element_id not in inserted_element_ids
                and any(eu.element_id == element_id for eu in plan.elements_to_unlock)
            ):
                # Element was generated by the planner but lost the
                # INSERT race -- suppress at the celebration layer too.
                continue

            payload: dict[str, Any] = dict(plan_reward.payload) if plan_reward.payload else {}
            if tier_hint is not None and "tier_hint" not in payload:
                payload["tier_hint"] = tier_hint

            reward_type: RewardType = plan_reward.type  # type: ignore[assignment]
            dispatcher_rewards.append(
                Reward(
                    type=reward_type,
                    title=plan_reward.title,
                    detail=plan_reward.detail or "",
                    icon=plan_reward.icon,
                    weight=_translate_weight(plan_reward.type),
                    payload=payload,
                )
            )

        await ctx.db.commit()

        log.info(
            "dispatcher.world.complete",
            observation_id=obs.id,
            user_id=ctx.user.id,
            zone_id=plan.contribution_zone_id,
            elements_inserted=len(inserted_element_ids),
            rewards_emitted=len(dispatcher_rewards),
            crossed_thresholds=list(transition.crossed_thresholds),
        )

        state: dict[str, Any] = {
            self.STATE_CONTRIBUTION_ID: contribution_id,
            self.STATE_ZONE_ID: plan.contribution_zone_id,
            self.STATE_REPLAY: False,
            self.STATE_BEFORE_TIER: transition.before_tier,
            self.STATE_AFTER_TIER: transition.after_tier,
            self.STATE_CROSSED_THRESHOLDS: list(transition.crossed_thresholds),
        }
        if tier_hint is not None:
            state[self.STATE_TIER_HINT] = tier_hint

        return HandlerResult(rewards=dispatcher_rewards, state=state)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_is_first_find(ctx: Context) -> bool:
        dex_result = ctx.results.get("dex")
        if dex_result is None:
            return False
        value = dex_result.state.get(_DEX_STATE_IS_FIRST_FIND, False)
        return bool(value)

    @staticmethod
    def _read_rarity_tier(ctx: Context) -> str | None:
        rarity_result = ctx.results.get("rarity")
        if rarity_result is None:
            return None
        tier = rarity_result.state.get(_RARITY_STATE_TIER)
        if isinstance(tier, str):
            return tier
        return None

    @staticmethod
    async def _resolve_iconic_taxon(ctx: Context, taxon_id: int | None) -> str | None:
        if taxon_id is None:
            return None
        stmt = select(models.SpeciesCache.iconic_taxon).where(
            models.SpeciesCache.taxon_id == taxon_id,
        )
        return (await ctx.db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _load_zone_states(ctx: Context) -> list[ZoneStateSnapshot]:
        stmt = select(models.SanctuaryZoneState).where(
            models.SanctuaryZoneState.user_id == ctx.user.id,
        )
        rows = (await ctx.db.execute(stmt)).scalars().all()
        return [
            ZoneStateSnapshot(
                user_id=row.user_id,
                zone_id=row.zone_id,  # type: ignore[arg-type]
                observation_count=row.observation_count,
                depth_tier=row.depth_tier,
            )
            for row in rows
        ]

    @staticmethod
    async def _load_elements(ctx: Context) -> list[ElementSnapshot]:
        stmt = select(models.SanctuaryElement).where(
            models.SanctuaryElement.user_id == ctx.user.id,
        )
        rows = (await ctx.db.execute(stmt)).scalars().all()
        return [
            ElementSnapshot(
                user_id=row.user_id,
                zone_id=row.zone_id,  # type: ignore[arg-type]
                element_id=row.element_id,
                element_type=row.element_type,  # type: ignore[arg-type]
            )
            for row in rows
        ]

    async def _upsert_zone_state(
        self,
        ctx: Context,
        *,
        zone_id: str,
        after_count: int,
        after_tier: int,
        crossed_any_threshold: bool,
        source_observation_id: str,
    ) -> None:
        new_id = str(ULID())
        insert_stmt = pg_insert(models.SanctuaryZoneState).values(
            id=new_id,
            user_id=ctx.user.id,
            zone_id=zone_id,
            observation_count=after_count,
            depth_tier=after_tier,
            first_unlocked_observation_id=source_observation_id,
            last_evolved_observation_id=(source_observation_id if crossed_any_threshold else None),
            last_observed_at=ctx.observation.created_at,
        )
        set_clause: dict[str, Any] = {
            "observation_count": insert_stmt.excluded.observation_count,
            "depth_tier": insert_stmt.excluded.depth_tier,
            "last_observed_at": insert_stmt.excluded.last_observed_at,
        }
        if crossed_any_threshold:
            set_clause["last_evolved_observation_id"] = (
                insert_stmt.excluded.last_evolved_observation_id
            )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_sanctuary_zone_state_user_zone",
            set_=set_clause,
        )
        await ctx.db.execute(upsert_stmt)


def _translate_weight(reward_type: str) -> int:
    """Re-stamp planner reward weights with the canonical dispatcher
    weights documented in ``docs/dispatcher.md``."""
    if reward_type == "world_unlock":
        return _WORLD_UNLOCK_REWARD_WEIGHT
    if reward_type == "world_evolution":
        return _WORLD_EVOLUTION_REWARD_WEIGHT
    # Defensive: pass-through for any future reward types the planner
    # may emit. The dispatcher's stable sort still places them correctly.
    return 0
