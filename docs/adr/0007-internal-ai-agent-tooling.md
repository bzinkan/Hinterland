# ADR 0007: Multi-agent AI is internal tooling only

- **Status:** Accepted
- **Date:** 2026-05-06
- **Deciders:** Solo author
- **Related:** ADR 0002

## Context

CrewAI and similar frameworks can coordinate specialized agents for content,
triage, summaries, and operational support. Hinterland also serves kids ages
9-12, so runtime AI carries a much higher safety, privacy, and correctness
burden than normal internal automation.

ADR 0002 already forbids kid-facing runtime LLM calls.

## Decision

CrewAI or any multi-agent framework may be used only for internal/adult
workflows, and only after a proof of concept shows the output is reviewable,
cached, validated, and outside the backend request path.

Allowed uses:

- expedition draft generation for human review
- content linting and schema repair suggestions
- species blurb drafts from checked source material
- docs drift checks
- CI/deploy triage summaries
- closed-beta feedback summaries
- moderation-review summaries for adults

Forbidden uses:

- controlling API request routing
- deciding auth, moderation, rewards, or observation writes
- importing agent dependencies from `backend/app`
- live kid-facing chat, coaching, or generated commentary
- sending kid-uploaded photos through an agent workflow without a new ADR

## Consequences

- The production app remains deterministic: FastAPI, Postgres, Cloud Tasks,
  Eventarc, Cloud Scheduler, and typed workers.
- Internal AI can still speed up the content treadmill and operations.
- Any future exception requires a new ADR and safety review.

