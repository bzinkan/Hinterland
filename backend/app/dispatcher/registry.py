"""Handler registration. The ONLY place that changes when adding a handler.

Order matters -- DexHandler must run before any handler that reads
`ctx.results["dex"].state["is_first_find"]`. Per `docs/dispatcher.md`,
sequentialism is by design (handlers are I/O bound on a single Postgres
connection; parallelizing buys nothing and costs determinism).
"""

from __future__ import annotations

from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.expedition import ExpeditionHandler
from app.dispatcher.handlers.rarity import RarityHandler
from app.dispatcher.handlers.world import WorldHandler
from app.dispatcher.types import Handler

HANDLERS: list[Handler] = [
    DexHandler(),
    RarityHandler(),
    # WorldHandler runs after Dex (reads `is_first_find` to gate coarse
    # zone wake-ups) and Rarity (reads `tier` for payload enrichment
    # only -- the planner does NOT branch on rarity). Runs BEFORE
    # Expedition so the existing expedition rewards still appear in
    # the celebration sequence at their documented weights. See
    # `docs/sanctuary.md` section 9 for the contract.
    WorldHandler(),
    ExpeditionHandler(),  # full impl (Phase 10); per-observation replay gate inside
    # TerritoryHandler()  -- Phase 2
    # SeasonHandler()     -- Phase 3
    # MissionHandler()    -- Phase 4
]
