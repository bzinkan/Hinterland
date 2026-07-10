"""RarityHandler -- region rarity rewards + rarest_tier counter.

Reads `rarity_cache` (populated nightly by `app.rarity.refresh`). For an
observation with a known taxon in a known region:

- If `rarity_cache(region_geohash=obs.geohash4, taxon_id=obs.taxon_id)`
  exists, emit a `rarity_tier` reward weighted by the tier:

      legendary / epic -> 60
      rare             -> 40
      common           -> 10
      abundant         -> suppressed (no reward)

- If the region has any rarity_cache rows but THIS taxon isn't one of
  them, emit an `unrecorded` reward at weight 100. The species hasn't
  been logged in this region by anyone iNat tracks, so the kid's
  observation is changing the regional species list.

- If the geohash-4 region has zero rows in rarity_cache, fall back to the
  parent geohash-3 region when it has data. If neither has a baseline,
  treat it as cold-start and skip both rewards.

This handler also owns the `memberships.rarest_tier` counter -- on a
strictly-higher-tier observation, conditionally update it. The tier
ordering (unrecorded > legendary > epic > rare > common > abundant)
is encoded in `_TIER_RANK`.

Per docs/dispatcher.md, the geohash-3 fallback lets low-data child cells
still use a broader local baseline when the nightly rarity pipeline has
materialized one.
"""

from __future__ import annotations

import structlog
from sqlalchemy import case, select, update

from app.db import models
from app.dispatcher.types import Context, HandlerResult, Reward

log = structlog.get_logger()

# Higher rank == rarer. Used to compare against memberships.rarest_tier.
_TIER_RANK: dict[str, int] = {
    "abundant": 1,
    "common": 2,
    "rare": 3,
    "epic": 4,
    "legendary": 5,
    "unrecorded": 6,
}

_TIER_REWARD_WEIGHT: dict[str, int] = {
    "common": 10,
    "rare": 40,
    "epic": 60,
    "legendary": 60,
}


class RarityHandler:
    name = "rarity"
    version = "1"

    async def handle(self, ctx: Context) -> HandlerResult:
        obs = ctx.observation
        if obs.taxon_id is None or not obs.geohash4:
            return HandlerResult(rewards=[])

        lookup = await _lookup_rarity(ctx, region=obs.geohash4, taxon_id=obs.taxon_id)

        rewards: list[Reward] = []
        observed_tier: str | None = None

        if lookup.tier is not None and lookup.kind == "species":
            observed_tier = lookup.tier
            weight = _TIER_REWARD_WEIGHT.get(lookup.tier)
            if weight is not None:
                rewards.append(
                    Reward(
                        type="rarity_tier",
                        title=_tier_title(lookup.tier),
                        detail=_tier_detail(lookup.tier, obs.species_name),
                        icon=f"rarity.{lookup.tier}",
                        weight=weight,
                        payload={"tier": lookup.tier, "region": lookup.region},
                    )
                )
            # else: abundant -> suppressed, no reward emitted.
        elif lookup.kind == "region_seen":
            observed_tier = "unrecorded"
            rewards.append(
                Reward(
                    type="unrecorded",
                    title="First in this region!",
                    detail=_unrecorded_detail(obs.species_name),
                    icon="rarity.unrecorded",
                    weight=100,
                    payload={"region": lookup.region},
                )
            )

        # rarest_tier counter on the membership row. Only bump when the
        # new observation is strictly rarer than what we already have.
        if observed_tier is not None and ctx.group is not None:
            new_rank = _TIER_RANK[observed_tier]
            await ctx.db.execute(
                update(models.Membership)
                .where(
                    models.Membership.user_id == ctx.user.id,
                    models.Membership.group_id == ctx.group.id,
                    case(_TIER_RANK, value=models.Membership.rarest_tier, else_=0) < new_rank,
                )
                .values(rarest_tier=observed_tier)
            )
        log.info(
            "dispatcher.rarity.complete",
            observation_id=obs.id,
            taxon_id=obs.taxon_id,
            region=obs.geohash4,
            rarity_region=lookup.region,
            tier=observed_tier,
            reward_count=len(rewards),
        )

        return HandlerResult(rewards=rewards, state={"tier": observed_tier})


def _tier_title(tier: str) -> str:
    if tier == "legendary":
        return "Legendary find!"
    if tier == "epic":
        return "Epic find!"
    if tier == "rare":
        return "Rare find"
    return "Logged"


def _tier_detail(tier: str, species_name: str | None) -> str:
    species = species_name or "this species"
    if tier in ("legendary", "epic"):
        return f"{species} -- almost no one logs this here"
    if tier == "rare":
        return f"{species} doesn't show up in this region often"
    return f"{species} is common here"


def _unrecorded_detail(species_name: str | None) -> str:
    species = species_name or "this species"
    return f"You're the first to log {species} in this region"


class _RarityLookup:
    def __init__(
        self,
        *,
        kind: str,
        region: str,
        tier: str | None = None,
    ) -> None:
        self.kind = kind
        self.region = region
        self.tier = tier


async def _lookup_rarity(ctx: Context, *, region: str, taxon_id: int) -> _RarityLookup:
    """Find rarity for a species, falling back from geohash-4 to geohash-3."""
    direct = await _lookup_region(ctx, region=region, taxon_id=taxon_id)
    if direct.kind != "cold_start":
        return direct

    parent_region = region[:3]
    if len(parent_region) < 3 or parent_region == region:
        return direct

    parent = await _lookup_region(ctx, region=parent_region, taxon_id=taxon_id)
    if parent.kind != "cold_start":
        return parent
    return direct


async def _lookup_region(ctx: Context, *, region: str, taxon_id: int) -> _RarityLookup:
    species_row = (
        await ctx.db.execute(
            select(models.RarityCache).where(
                models.RarityCache.region_geohash == region,
                models.RarityCache.taxon_id == taxon_id,
            )
        )
    ).scalar_one_or_none()
    if species_row is not None:
        return _RarityLookup(kind="species", region=region, tier=species_row.tier)

    region_seen = (
        await ctx.db.execute(
            select(models.RarityCache.region_geohash)
            .where(models.RarityCache.region_geohash == region)
            .limit(1)
        )
    ).scalar_one_or_none()
    if region_seen is not None:
        return _RarityLookup(kind="region_seen", region=region)
    return _RarityLookup(kind="cold_start", region=region)
