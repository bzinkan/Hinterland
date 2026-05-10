"""ExpeditionHandler -- match an observation to incomplete expedition steps.

Phase 9 ships a stub: the handler returns an empty result without doing
any work. Phase 10 (Content + Expeditions in `AGENTS.md`) builds the
expedition content tree, the matcher registry, and the per-step match
specs the handler walks.

When implemented, the handler will:
1. Load incomplete `expedition_progress` rows for the user.
2. For each, run the next-incomplete-step's match spec against the
   observation via the matcher registry.
3. Advance matched steps; emit `expedition_step` rewards (weight 40).
4. On the final step, also emit `expedition_complete` (weight 30).

Correctness invariant per docs/dispatcher.md: a single observation can
match at most one step per expedition (the first unmatched step), but
can progress multiple expeditions at once.
"""

from __future__ import annotations

from app.dispatcher.types import Context, HandlerResult


class ExpeditionHandler:
    name = "expedition"

    async def handle(self, ctx: Context) -> HandlerResult:
        # Phase 9 stub. See module docstring.
        return HandlerResult(rewards=[])
