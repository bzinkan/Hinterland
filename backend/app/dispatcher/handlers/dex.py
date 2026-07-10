"""DexHandler -- atomic first-find detection + dex_count counter bump.

Owns:
- The DexEntry row (one per (user_id, taxon_id))
- memberships.dex_count (incremented on first find)
- The first_find / repeat_find rewards

Per `docs/dispatcher.md` this handler MUST run first because downstream
handlers (ExpeditionHandler, MissionHandler, ...) read
`ctx.results["dex"].state[STATE_IS_FIRST_FIND]` to gate "rare discovery"
bonuses. Don't reorder without auditing every handler that depends on it.

Per AGENTS.md non-negotiables: the first-find check uses an atomic
conditional insert (INSERT ... ON CONFLICT DO NOTHING RETURNING). Never
introduce a read-then-write pattern here -- two simultaneous submissions
of the same species would both see "no row" and both bump the counter.
"""

from __future__ import annotations

import structlog
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ulid import ULID

from app.db import models
from app.dispatcher.types import Context, HandlerResult, Reward

log = structlog.get_logger()


class DexHandler:
    name = "dex"
    version = "1"

    # Public state keys -- downstream handlers should reference these
    # constants instead of bare strings.
    STATE_IS_FIRST_FIND = "is_first_find"

    async def handle(self, ctx: Context) -> HandlerResult:
        obs = ctx.observation

        # No taxon -> no Dex entry. Repeat-find rewards aren't meaningful
        # without a species to deduplicate on.
        if obs.taxon_id is None:
            return HandlerResult(rewards=[], state={self.STATE_IS_FIRST_FIND: False})

        # Atomic insert. RETURNING is empty when ON CONFLICT fired, so
        # we use that as the first-find signal -- no read-then-write race.
        new_id = str(ULID())
        stmt = (
            pg_insert(models.DexEntry)
            .values(
                id=new_id,
                user_id=ctx.user.id,
                group_id=obs.group_id,
                taxon_id=obs.taxon_id,
                species_name=obs.species_name,
                first_observation_id=obs.id,
                first_seen_at=obs.observed_at or obs.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_dex_entries_user_taxon")
            .returning(models.DexEntry.id)
        )
        inserted_id = (await ctx.db.execute(stmt)).scalar_one_or_none()
        is_first_find = inserted_id is not None

        if is_first_find:
            # Bump dex_count atomically on the membership row. The
            # observation_count counter is bumped by the create endpoint;
            # dex_count is the dispatcher's responsibility.
            await ctx.db.execute(
                update(models.Membership)
                .where(
                    models.Membership.user_id == ctx.user.id,
                    models.Membership.group_id == obs.group_id,
                )
                .values(dex_count=models.Membership.dex_count + 1)
            )
            reward = Reward(
                type="first_find",
                title="New species!",
                detail=_format_first_find_detail(obs),
                icon="dex.first_find",
                weight=80,
                payload={"taxon_id": obs.taxon_id},
            )
        else:
            # Repeat find -- no DB writes beyond the no-op insert.
            reward = Reward(
                type="repeat_find",
                title="Logged",
                detail=_format_repeat_find_detail(obs),
                icon="dex.repeat",
                weight=10,
                payload={"taxon_id": obs.taxon_id},
            )

        log.info(
            "dispatcher.dex.complete",
            observation_id=obs.id,
            user_id=ctx.user.id,
            taxon_id=obs.taxon_id,
            is_first_find=is_first_find,
        )
        return HandlerResult(
            rewards=[reward],
            state={self.STATE_IS_FIRST_FIND: is_first_find},
        )


def _format_first_find_detail(obs: models.Observation) -> str:
    species = obs.species_name or "this species"
    return f"First {species} in your Dex"


def _format_repeat_find_detail(obs: models.Observation) -> str:
    species = obs.species_name or "this species"
    return f"Another {species}"
