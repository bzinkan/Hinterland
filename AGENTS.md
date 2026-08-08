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

## Current W1 and Adult QA Hold

- Play Internal v12 (version code 12, commit
  `20a8b54717ed7a450f5c0811f2543288a8571ab0`) is an immutable W1 evidence
  artifact. Do not rebuild, update, or combine its evidence with a later source
  revision.
- The operator-approved low-memory exception is scoped only to v12's additional
  hardware gate for devices with 4 GB of RAM or less. A 2 GiB Google Play
  emulator completed the constrained run; this does not claim physical
  low-end-device compatibility or carry to a later build.
- The constrained run passed execution, exactly-once saving, and private-photo
  assertions, but the formal W1 device gate is not green: the original Maestro
  assertion timed out at 30 seconds, `pilot_private` was observed 66–69 seconds
  after save, and Observation detail has unreadable dark-on-dark text in light
  mode. Repair these defects in a new immutable Play version, rerun protected
  promotion, and repeat device evidence.
- Remaining human W1 gates are received synthetic-alert evidence, the adult
  consent/group/QR/Observation and rollback dry run with GO/NO-GO, one
  supervised family session, and the post-session privacy/operations audit and
  continuation decision.
- PRs #229, #230, and #231 remain a draft stack. Do not merge or deploy any of
  them while this W1 hold is active.
- Adult QA has no provisioned resources and no deployed release. Do not
  provision or deploy Adult QA, enable real Content Safety or CV, or add public
  iNaturalist submission without the plan's explicit operator approvals and
  promotion gates. Public iNaturalist submission remains disabled.

## Done Means

- Focused tests, lint, formatting, type checks, content validation, and the
  naming gate pass.
- Azure health, readiness, and authenticated smoke checks pass after deploy.
- Mobile builds use the current Hinterland app identity and are tested on a
  physical device before release.
- A documented, version-scoped operator exception may substitute a constrained
  Google Play emulator only for an additional hardware-class requirement. It
  does not replace baseline physical-device testing, establish physical-device
  compatibility, or carry to later builds.
