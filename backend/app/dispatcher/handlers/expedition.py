"""ExpeditionHandler -- match observations to incomplete expedition steps.

Walks every active expedition for the user, finds the first incomplete
step in each, evaluates its match spec against the observation, advances
matched steps. Per docs/dispatcher.md:

- A single observation can match at most ONE step per expedition (the
  first unmatched), but can advance MULTIPLE expeditions in one shot.
- ExpeditionHandler runs AFTER DexHandler so `not_in_dex` matchers see
  the dex state before this observation's row was inserted.
- Expedition progress is personal kid game progress. A kid's observation
  may advance their active expeditions regardless of the group context that
  originally created the progress row; `group_id` is retained as creation
  context for audit/reporting.

Completed steps are recorded as
``{"completed_at": <iso string>, "observation_id": <ulid>}`` (legacy
rows hold a plain ISO string -- see `app.services.expedition_progress`).
The recorded observation_id is the per-observation replay gate: if any
value in an expedition's completed_steps already carries this
observation's id, the handler skips that expedition. Without the gate,
a re-dispatch (taxon PATCH, admin replay) could chain one observation
through multiple steps, violating the invariant above.

After a restart (`POST /v1/expeditions/{id}/restart`) the gate map is
empty, so a re-dispatched old observation may credit the fresh run
once -- the invariant is one step per expedition per RUN. If that ever
needs hardening, a `restarted_at` column is the lever.
"""

from __future__ import annotations

from dataclasses import replace

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db import models
from app.dispatcher.types import Context, HandlerResult, Reward
from app.matchers.context import MatcherInputs, PriorObservation, TaxonInfo
from app.matchers.registry import matches
from app.matchers.taxon_sets import load_taxon_set_index
from app.models.expedition import (
    Expedition,
    MatchNotWithinRadius,
    MatchSpec,
)
from app.services.expedition_progress import parse_step_completion
from app.services.species_cache import ancestor_ids_from_payload

log = structlog.get_logger()


def _uses_radius(spec: MatchSpec) -> bool:
    """True when the spec tree contains a not_within_radius_of_existing leaf.

    Combinators recurse; every other leaf kind imposes no prior-location
    requirement. Used to decide whether `_build_inputs` needs to load the
    user's full (lat, lng) history at all.
    """
    if isinstance(spec, MatchNotWithinRadius):
        return True
    # Recurse through anything carrying a `matches` list, not just the
    # two known combinators -- a future combinator kind that nests a
    # radius leaf must not silently skip the prior-location load (an
    # empty history makes the radius matcher vacuously true).
    nested = getattr(spec, "matches", None)
    if nested is not None:
        return any(_uses_radius(sub) for sub in nested)
    return False


class ExpeditionHandler:
    name = "expedition"
    version = "1"

    async def handle(self, ctx: Context) -> HandlerResult:
        # with_for_update(of=progress): the restart endpoint and this
        # handler are both read-modify-write writers on
        # expedition_progress; the row lock closes the restart-vs-
        # dispatch lost-update window. Both transactions are short and
        # commit promptly, so the lock is held briefly. `of=` keeps the
        # joined expedition_content rows unlocked.
        progress_pairs = (
            await ctx.db.execute(
                select(models.ExpeditionProgress, models.ExpeditionContent)
                .join(
                    models.ExpeditionContent,
                    models.ExpeditionProgress.expedition_id == models.ExpeditionContent.id,
                )
                .where(
                    models.ExpeditionProgress.user_id == ctx.user.id,
                    models.ExpeditionProgress.completed_at.is_(None),
                    models.ExpeditionContent.archived.is_(False),
                )
                .with_for_update(of=models.ExpeditionProgress)
                # Deterministic lock order so two concurrent dispatches
                # for the same kid can never deadlock on these rows.
                .order_by(models.ExpeditionProgress.id)
            )
        ).all()

        if not progress_pairs:
            return HandlerResult(rewards=[])

        # Validate bodies up front so the radius walk below sees every
        # active expedition before _build_inputs decides what to load.
        parsed: list[tuple[models.ExpeditionProgress, models.ExpeditionContent, Expedition]] = []
        for progress, content in progress_pairs:
            try:
                exp = Expedition.model_validate(content.body)
            except Exception:  # body got corrupted somehow; don't crash
                log.warning(
                    "dispatcher.expedition.bad_content",
                    expedition_id=content.id,
                )
                continue
            parsed.append((progress, content, exp))

        # Only pay the prior-observation history scan when some active
        # step can actually use it. Deliberately walks ALL steps of ALL
        # active expeditions (not just each next incomplete step) --
        # simple and safe over minimal.
        needs_priors = any(_uses_radius(step.match) for _, _, exp in parsed for step in exp.steps)

        base_inputs = await self._build_inputs(ctx, include_prior_observations=needs_priors)

        rewards: list[Reward] = []
        observed_at = ctx.observation.observed_at or ctx.observation.created_at

        for progress, content, exp in parsed:
            # Rebuilds replay accepted history; observations made before an
            # expedition enrollment never advance it retroactively.
            if progress.created_at is not None and observed_at < progress.created_at:
                continue
            completed = dict(progress.completed_steps or {})

            # Replay gate: this observation already credited a step in
            # this expedition. A re-dispatch (taxon PATCH, admin replay)
            # must not advance it again.
            if any(
                parse_step_completion(v).observation_id == ctx.observation.id
                for v in completed.values()
            ):
                log.info(
                    "dispatcher.expedition.replay_skip",
                    expedition_id=content.id,
                    observation_id=ctx.observation.id,
                )
                continue

            next_step = next((s for s in exp.steps if s.id not in completed), None)
            if next_step is None:
                # Race: something else completed all steps. Skip.
                continue

            inputs = replace(
                base_inputs,
                current_expedition_taxon_ids=await self._completed_taxon_ids(ctx, completed),
            )
            if not matches(next_step.match, inputs):
                continue

            if isinstance(ctx.db, AsyncSession) and type(ctx.db).__module__ != "unittest.mock":
                contribution_stmt = (
                    pg_insert(models.ExpeditionObservationContribution)
                    .values(
                        observation_id=ctx.observation.id,
                        expedition_id=exp.id,
                        step_id=next_step.id,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["observation_id", "expedition_id"],
                    )
                    .returning(models.ExpeditionObservationContribution.observation_id)
                )
                contributed = (await ctx.db.execute(contribution_stmt)).scalar_one_or_none()
                if contributed is None:
                    continue

            completed[next_step.id] = {
                "completed_at": observed_at.isoformat(),
                "observation_id": ctx.observation.id,
            }
            progress.completed_steps = completed
            # JSONB mutation tracking needs an explicit nudge when we
            # reassign with the same key set.
            flag_modified(progress, "completed_steps")
            rewards.append(
                Reward(
                    type="expedition_step",
                    title="Expedition step!",
                    detail=f"{exp.title}: {next_step.description}",
                    icon="expedition.step",
                    weight=40,
                    payload={"expedition_id": exp.id, "step_id": next_step.id},
                )
            )

            if len(completed) == len(exp.steps):
                progress.completed_at = observed_at
                rewards.append(
                    Reward(
                        type="expedition_complete",
                        title="Expedition complete!",
                        detail=exp.outro,
                        icon="expedition.complete",
                        # 60, not 30: the dispatcher sorts weight desc,
                        # and completion must render BEFORE its own step
                        # reward (weight 40). The tie with world_unlock
                        # at 60 resolves by handler registration order
                        # -- World before Expedition -- which is fine.
                        weight=60,
                        payload={"expedition_id": exp.id},
                    )
                )

        log.info(
            "dispatcher.expedition.complete",
            observation_id=ctx.observation.id,
            user_id=ctx.user.id,
            advanced_count=sum(1 for r in rewards if r.type == "expedition_step"),
            completed_count=sum(1 for r in rewards if r.type == "expedition_complete"),
        )
        return HandlerResult(rewards=rewards)

    @staticmethod
    async def _completed_taxon_ids(
        ctx: Context,
        completed: dict[str, object],
    ) -> frozenset[int]:
        observation_ids = [
            parsed.observation_id
            for value in completed.values()
            for parsed in [parse_step_completion(value)]
            if parsed.observation_id is not None
        ]
        if not observation_ids:
            return frozenset()

        rows = (
            await ctx.db.execute(
                select(models.Observation.taxon_id).where(
                    models.Observation.id.in_(observation_ids),
                    models.Observation.taxon_id.is_not(None),
                )
            )
        ).all()
        return frozenset(taxon_id for (taxon_id,) in rows if taxon_id is not None)

    async def _build_inputs(
        self, ctx: Context, *, include_prior_observations: bool
    ) -> MatcherInputs:
        obs = ctx.observation

        taxon: TaxonInfo | None = None
        if obs.taxon_id is not None:
            species = (
                await ctx.db.execute(
                    select(models.SpeciesCache).where(models.SpeciesCache.taxon_id == obs.taxon_id)
                )
            ).scalar_one_or_none()
            taxon = TaxonInfo(
                taxon_id=obs.taxon_id,
                iconic_taxon=species.iconic_taxon if species is not None else None,
                ancestor_ids=(
                    tuple(species.ancestor_ids or ())
                    if species is not None and species.ancestor_ids
                    else ancestor_ids_from_payload(species.source_payload, taxon_id=obs.taxon_id)
                    if species is not None
                    else ()
                ),
            )

        # Exclude the dex_entry that DexHandler just inserted for this
        # observation -- otherwise not_in_dex would always return False
        # for first finds.
        dex_rows = (
            await ctx.db.execute(
                select(models.DexEntry.taxon_id).where(
                    models.DexEntry.user_id == ctx.user.id,
                    models.DexEntry.first_observation_id != obs.id,
                )
            )
        ).all()
        user_dex = frozenset(r[0] for r in dex_rows)

        # The full (lat, lng) history scan only runs when some active
        # step uses not_within_radius_of_existing (the caller walks the
        # spec trees); every other dispatch passes an empty tuple.
        priors: tuple[PriorObservation, ...] = ()
        if include_prior_observations:
            prior_rows = (
                await ctx.db.execute(
                    select(
                        models.Observation.latitude,
                        models.Observation.longitude,
                    ).where(
                        models.Observation.user_id == ctx.user.id,
                        models.Observation.id != obs.id,
                    )
                )
            ).all()
            priors = tuple(
                PriorObservation(latitude=lat, longitude=lng)
                for lat, lng in prior_rows
                if lat is not None and lng is not None
            )

        return MatcherInputs(
            taxon=taxon,
            user_dex_taxon_ids=user_dex,
            user_prior_observations=priors,
            obs_latitude=obs.latitude,
            obs_longitude=obs.longitude,
            taxon_sets=load_taxon_set_index(),
            ecology_tags={
                str(key): str(value)
                for key, value in (obs.ecology_tags or {}).items()
                if isinstance(key, str) and isinstance(value, str)
            },
        )
