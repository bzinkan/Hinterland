# ADR 0002: LLMs are author-time, not runtime

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Solo author
- **Supersedes:** —
- **Related:** ADR 0001 (single-table DynamoDB)

## Context

Hinterland has API access to Anthropic (Claude) and Google (Gemini). Two questions need an answer before either gets used:

1. **Where do LLMs belong in the architecture?** Candidates include a kid-facing "ask the app anything" chat or species coach, behind-the-scenes features like moderation assist and teacher summaries, and author-time tooling like expedition drafting and species-blurb generation.
2. **What's the audience trust model?** Hinterland's users are kids 9–12, some as young as 9 in group settings like classrooms. Any LLM output reaching a kid needs to meet a higher bar than "probably fine" — hallucinations in a science app are not just embarrassing, they're educational malpractice.

Two design pressures shape this decision:

- **The content treadmill is a real Phase 1 risk.** The build handoff flagged "~2 expeditions/week sustainably" as the critical cadence for keeping classrooms engaged. Miss it and the app goes stale inside one school term. The solo author cannot hit this cadence without tooling.
- **The core loop is latency-sensitive and deterministic.** Observation submission needs sub-second celebration feedback. LLM calls at 2–10+ seconds on the hot path break this, and non-deterministic outputs make the celebration UX unpredictable.

## Decision

**LLMs are used at author-time and in adult-facing surfaces only. They are never invoked on a kid's hot path, and no LLM output reaches a kid without a cache lookup of previously-reviewed content.**

Concretely:

- **Allowed (author-time):** `scripts/draft_expedition.py` to produce expedition JSON candidates from a theme prompt; a species-blurb pipeline that takes iNat taxa data as grounding input and produces cached Dex descriptions; weekly teacher-dashboard summaries generated in batch from a group's observation data.
- **Allowed (adult-facing runtime):** moderation-assist for text notes (if and when the app adds them), scoped to flagging for teacher review — never to autonomous decisions.
- **Forbidden (kid-facing runtime):** no chatbots, no "ask the app" help, no per-observation generated commentary, no dynamic coaching. The Dex species description a kid sees is the cached result of a previously-run, human-reviewed pipeline — not a live LLM call.

The grounding pattern is normative for any kid-facing content: the LLM is given source material (iNat description, Wikipedia extract, curriculum-aligned reference) and instructed to rewrite it for the target age band. It does not write from its own knowledge. The prompt and source are checked in alongside the output, so regenerations are reproducible and review diffs are meaningful.

## Consequences

### Positive

- **Content treadmill becomes sustainable.** An author can go from "this week's theme is spring ephemerals in the Midwest" to reviewed expedition JSON in 30 minutes instead of 4 hours. 2/week stops being a chore.
- **No per-observation LLM cost.** Unit economics stay predictable as the user base grows. Rough numbers: even at 10k DAU logging one observation per day, a per-observation LLM call would dominate the AWS bill.
- **Hot path stays deterministic and fast.** The dispatcher runs at sub-300ms with predictable rewards. No "sometimes the celebration is weird" class of bugs.
- **Safety surface is small.** Every piece of text a kid reads has been looked at by an adult. Hallucinations about whether a plant is edible, or whether an insect bites, cannot leak to a child user.
- **Reviewable outputs.** Expedition and species content lives in git. Changes are diffs. Authors can see what the model produced, what got edited, what got rejected.

### Negative

- **No "magical" in-app help feature.** Kids can't ask the app questions in free-form natural language. This is a real UX loss — and one we accept because the alternatives are worse (hallucination risk) or more expensive (heavy moderation plus guardrails plus review).
- **Author workflow requires tooling investment.** `scripts/draft_expedition.py` and the species-blurb pipeline are real software that need testing, versioning, and prompt maintenance. Not free.
- **Prompts drift.** Model providers update models; outputs change; today's reviewed prompt produces different text in six months. Mitigation: pin model versions, cache outputs, rerun the review pipeline when the model version bumps.
- **No live coaching during observation.** If a kid photographs something and iNat's CV gets it wrong, we can't have an LLM in the loop suggesting "that looks more like a Mourning Cloak than a Painted Lady." We could — it's tempting — but it would mean sending kid-uploaded photos to a third-party LLM, which is a different privacy surface than iNat (which the app is already built around).

### Neutral

- **Vendor choice is not yet locked.** This ADR picks the *pattern*, not the provider. Whether expedition drafting uses Claude or Gemini or both can be decided per-workflow based on quality, cost, and prompt fit at the time.
- **The rule applies to adults-behind-kids-content too.** A teacher-dashboard summary that quotes student observation counts back at them is fine — that's data, not generated content. A teacher-dashboard summary that *characterizes* a student's progress in prose is also allowed, because teachers have context to sanity-check it. An LLM telling a kid "great job identifying that bird!" is not, because the kid has no frame of reference to detect a subtly wrong compliment.

## Alternatives considered

### Expose an LLM chatbot to kids

**Rejected.** The failure modes are well-documented across consumer AI products aimed at minors: hallucinated facts stated with confidence, inappropriate emotional dynamics, privacy concerns around training-data incorporation, and the COPPA/regulatory exposure that comes with logging kid-LLM conversations. The feature would be cool; the risk and compliance work to do it safely would dominate a Phase 2 or Phase 3 entirely, for value that can be delivered better through curated content.

Revisit only if the regulatory landscape and model-safety tooling mature substantially — probably multiple years out, and probably only for older kids (13+) in Phase 3's claiming flow, if at all.

### Use LLMs behind heavy guardrails on the kid path

**Rejected.** "LLM plus classifier plus rule filter plus prompt injection defense" is a plausible design and is how several production consumer apps do it. At Hinterland's scale and solo-dev constraint, the maintenance burden of the guardrail stack is the entire engineering budget for a year. The author-time pattern gets 80% of the content-quality upside at 5% of the runtime complexity.

### Use no LLMs anywhere

**Rejected** — specifically because of the content treadmill. Without LLM-assisted drafting, the realistic expedition-authoring pace is closer to 1/week than 2/week, and the species-blurb backlog becomes a blocker for Dex polish. The tools work; the question is where they sit in the architecture, not whether they exist.

### Use an LLM for iNat CV fallback

**Deferred.** Tempting: when iNat's computer vision returns a low-confidence result, ask an LLM with vision capabilities to disambiguate. But this puts kid-uploaded photos into a third-party context on the hot path, adds 3–10s latency, and shifts the product's scientific grounding away from iNat's community-verified model toward an LLM's opinion. Revisit only if iNat CV proves genuinely unreliable on kid photos (the Week 5 test in Phase 1 is the decision point).

## Follow-ups

- Build `scripts/draft_expedition.py` in Phase 1 Week 10 alongside the starter-expedition authoring work. First use = authoring the 5 starter expeditions.
- Define the species-blurb pipeline: input schema (iNat taxa JSON), prompt template (checked into `content/prompts/species_blurb.md`), output schema (Pydantic-validated), caching key (`SPECIES#<taxonId>` content-hashed). Target Phase 1 Week 8 for the initial cut.
- Pin model versions in every prompt runner. Add a `model_version` field on every cached output so a model bump triggers a reviewable regeneration, not a silent drift.
- Add a CloudWatch alarm on any per-request LLM call from the API Lambda. If one appears, this ADR has been violated and we want to know immediately.
- Revisit the kid-facing LLM question annually or on any substantial change in the regulatory/safety landscape.
