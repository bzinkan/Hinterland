# Risk 0004: LLM-assisted expedition authoring tool is a stub

- **Status:** Open
- **Date filed:** 2026-05-10
- **Source:** AGENTS.md Phase 10 deliverable ("Author-time `draft_expedition.py` tool, with no kid-facing runtime LLM path")
- **Owner:** Brian (decision: which LLM provider; cost; setup)

## What we have

`scripts/draft_expedition.py` exists at the path docs reference, but it's a stub that prints a one-line "see this risk" message. The full Phase 10 manual-authoring path -- write JSON in your editor, run `python scripts/validate_content.py`, run `python scripts/sync_expeditions.py` -- is fully shipped (PRs #54, #55).

## Why deferred

Three reasons, none urgent:

1. **API cost is real and small.** Anthropic / OpenAI bills per call. Even at $0.01 per draft, the budget needs an explicit owner before this lands. Trivial to enable -- just needs a decision.
2. **ADR 0007 needs to be respected explicitly.** The doc says "multi-agent AI is internal/adult-only; never on a kid-facing request path." This script qualifies (author-time, run from a developer machine), but the implementation should reaffirm that boundary in code: no environment variable that would let it run inside the Cloud Run container, no feature flag that routes it through the API.
3. **The five starter expeditions are already written by hand.** They unblock Phase 10's "starter expeditions are visible in the app and can complete through the dispatcher" exit criterion. The drafting tool is for future tier-2+ expeditions, which is Phase 11 (week 11) work.

## What the implementation will look like

When unblocked:

- Reads a one-line prompt from argv (e.g. `"a city-park expedition focused on insects you can find without flipping rocks"`)
- Calls the chosen LLM with `docs/expedition-authoring.md` voice/tone rules baked into the system prompt
- Pipes the response through `Expedition.model_validate` so authors never get malformed JSON to hand-fix
- Writes to stdout for the author to review + redirect into `content/expeditions/<tier>/<id>.json`
- The author edits, runs `make validate-content`, commits

No autonomous deploy path. Author always reviews. The code path is strictly local.

## Mitigation in the meantime

The five starter expeditions are written, validated, and ready to sync. New expeditions can be authored by hand against the schema (autocomplete via `content/schema/expedition.schema.json` -- next risk-doc follow-up to commit).

The full authoring loop a human takes today:

1. Copy an existing JSON file under `content/expeditions/starters/` as a template
2. Edit fields per `docs/expedition-authoring.md` voice rules
3. `python scripts/validate_content.py` (or `make validate-content` once added)
4. Commit + push; CI's `content-validate` workflow re-validates
5. After merge: `python scripts/sync_expeditions.py` materializes to Postgres

This works for the next ~10-20 expeditions easily. Beyond that, the LLM tool removes friction.

## Production unblock checklist

- [ ] Pick LLM provider (Claude is the natural pick given ADR 0007's "multi-agent AI" framing — same vendor as the rest of the agent tooling)
- [ ] Decide budget cap (an env var like `DRAGONFLY_DRAFT_EXPEDITION_MAX_USD=2.00`)
- [ ] Implement `draft_expedition.py` per the shape above
- [ ] Add a CONTRIBUTING note about how to use it
- [ ] Close this risk
