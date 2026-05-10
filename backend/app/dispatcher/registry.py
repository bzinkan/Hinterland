"""Handler registration. The ONLY place that changes when adding a handler.

Order matters -- DexHandler must run before any handler that reads
`ctx.results["dex"].state["is_first_find"]`. Per `docs/dispatcher.md`,
sequentialism is by design (handlers are I/O bound on a single Postgres
connection; parallelizing buys nothing and costs determinism).
"""

from __future__ import annotations

from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.rarity import RarityHandler
from app.dispatcher.types import Handler

HANDLERS: list[Handler] = [
    DexHandler(),
    RarityHandler(),
    # ExpeditionHandler() -- Phase 9 slice 3 (stub) / Phase 10 (full)
    # TerritoryHandler()  -- Phase 2
    # SeasonHandler()     -- Phase 3
    # MissionHandler()    -- Phase 4
]
