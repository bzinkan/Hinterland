# The Hinterland Guide

The Hinterland Guide is a citizen-science field app for curious explorers.
People record observations, build a personal Dex, complete Expeditions, and
grow a Sanctuary. Kid accounts are adult-managed.

## Read First

1. `README.md`
2. `docs/architecture.md`
3. `docs/data-model.md`
4. `docs/dispatcher.md`
5. `docs/mobile.md`
6. `docs/adr/0010-azure-target-architecture.md`
7. `docs/adr/0013-hinterland-rename.md`

## Active Architecture

- Azure is the only active platform: Container Apps, PostgreSQL Flexible
  Server, Blob Storage, Key Vault, Service Bus, Static Web Apps, and Entra.
- Adult authentication uses Entra External Identities. Kid authentication uses
  Hinterland-signed RS256 handoff and session JWTs.
- Configuration uses `HINTERLAND_*` variables only.
- The mobile identity is `app.thehinterlandguide`; preview and development use
  the `.staging` and `.dev` suffixes.
- The public kid-token endpoints are
  `/.well-known/hinterland-kid-jwks.json` and
  `hinterland.kid-handoff.v1`.

## Invariants

- Keep the observation submission hot path stable and additive.
- First-find detection must remain atomic.
- Kid-facing runtime LLM calls are forbidden.
- Third-party availability, moderation, and iNaturalist submission must not
  block saving an observation.
- Moderation and public iNaturalist submission remain asynchronous.
- Expedition JSON is the authoring source of truth.
- Do not add ads, public chat, direct messages, or kid free text.

## Working Rules

- Use a focused feature branch and keep `main` deployable.
- Update tests and docs with behavioral changes.
- Do not commit secrets, local environment files, or real child data.
- The naming gate must pass: no tracked filename or content may contain the
  retired product identifier.
- The Azure deploy workflow is the sole API deployment path. Do not add an
  alternate cloud runtime or a compatibility configuration layer.

## Done Means

- Focused tests, lint, formatting, type checks, content validation, and the
  naming gate pass.
- Azure health, readiness, and authenticated smoke checks pass after deploy.
- Mobile builds use the current Hinterland app identity and are tested on a
  physical device before release.
