# ADR 0013: Product rename — Dragonfly becomes Hinterland

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Solo author
- **Related:** ADR 0010 (Azure target architecture), ADR 0012 (Sanctuary
  2.5D diorama)

## Decision

The product formerly called **Dragonfly** is now **The Hinterland
Guide**. The full title is used sparingly (store listings, formal
copy); the short form **Hinterland** is the standard name everywhere
user-facing. The GitHub repository is already renamed to
`bzinkan/Hinterland` (GitHub redirects the old remotes, so existing
clones keep working).

This ADR records the **layer-1** rename shipped in the accompanying
PR: user-facing copy, living docs, comments/docstrings, and cosmetic
API titles (FastAPI title / `/v1/meta` name, the Expo display names
"Hinterland", "Hinterland Internal", "Hinterland (env)"). No wire
contract, identifier, or deployed resource changes.

## Layer-2 identifiers that DELIBERATELY stay "dragonfly"

Each of these is a deployed contract. Renaming any of them is its own
coordinated migration, listed with its owner/trigger below. Do not
"clean these up" opportunistically.

1. **Android/iOS bundle ids `com.dragonfly.app*`.** Play package names
   are permanent per Console listing — a rename means a NEW Play
   listing (new package, new tester opt-ins, review restart). Only
   revisit as an explicit Play-listing decision before any public
   release; the current Internal Testing pilot keeps the package.
2. **`DRAGONFLY_*` env var names and the `env_prefix="DRAGONFLY_"` in
   `backend/app/core/config.py`** (including the `dragonfly_*` Settings
   field names they bind to). The deployed Container Apps, jobs, CI
   workflows, and operator scripts all set these. Migration = a
   coordinated Container Apps / CI config change with a
   both-names-accepted overlap window. Comments around them may say
   Hinterland.
3. **Client-coordinated protocol strings:** the deep-link scheme
   `dragonfly` (`exp+dragonfly://…`), the kid QR handoff format
   `dragonfly.kid-handoff.v2`, the JWKS path
   `/.well-known/dragonfly-kid-jwks.json`, and the kid-JWT
   issuer/audience (`https://api.dragonfly-app.net` / `dragonfly-api`).
   Installed clients validate these exact strings; changing them
   requires a versioned client rollout (accept-both, then cut over).
4. **Azure resource names** (`dragonfly-dev-rg`, `dragonflyacrdev`,
   `dragonfly-api`, `dragonfly-kv-dev`, `dragonfly-*` jobs/workers,
   Entra app registrations incl. `api://dragonfly-api`) **and the
   `dragonfly-app.net` domain plus every URL on it** (API base,
   parents web, JWT issuer, geocoding/iNat user-agent strings). The
   in-flight `hinterland-dev-rg` environment migration owns these;
   until cutover, the old names are the live environment.
5. **`web/` (public landing site).** Deploys automatically to the live
   `dragonfly-app.net` on merge. The public brand cutover (landing
   copy, privacy/terms, social cards, store-facing URLs) is a separate
   coordinated step that ships together with the domain decision.
   Landing-page docs that mirror the live site copy stay as-is until
   then.
6. **On-device and data-layer names:** SecureStore key
   `dragonfly.bearer_token` (kid session continuity across app
   updates), database/user names (`dragonfly`), python package paths,
   content/sprite ids and icon keys, terrain seed strings, structlog
   event names, test fixture ids, `package.json`/`pyproject.toml`
   project names, Firebase/EAS project references (slug `dragonfly`,
   `dragonflyapp-495423`).
7. **Local folder names** (e.g. `C:\GitHub\Dragonfly`) — cosmetic,
   per-machine, renamed whenever convenient; nothing in the repo may
   depend on the checkout folder name.
8. **Historical records stay verbatim:** ADR 0001–0012 bodies, the
   AGENTS.md phase-status log, dated risk-doc entries, frozen Alembic
   migration docstrings, and the phase-10 GCP decommission record all
   keep "Dragonfly" as written at the time.

## Not a rename target: the Sanctuary guide mascot

The Sanctuary guide character IS a dragonfly — the insect. Everything
about the mascot keeps its name: `guide_message.speaker == "dragonfly"`
(backend route, `content/sanctuary/guide_lines.json`, mobile
consumers), the guide-bar speaker label "Dragonfly", the
`sanctuary.pond.dragonfly` sprite, and Odonata expedition copy that
mentions dragonflies. When a doc says "the dragonfly guide", that
stays.

## Consequences

- Users see "Hinterland" from the next build; nothing breaks because
  no identifier moved.
- The repo will grep positive for "dragonfly" indefinitely. That is by
  design; every remaining occurrence must be attributable to one of
  the layer-2 categories above.
- Each layer-2 migration above happens (or is consciously declined) on
  its own schedule with its own rollback plan.
