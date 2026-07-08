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

The migration targets are not open questions: ADR 0012 owns the rebrand and
Gordi-subscription environment, and ADR 0014 removes the stale Firebase/GCP
runtime and hosting paths. Together they fix the new identity end-to-end:
domain `thehinterlandguide.app`, Azure resources such as `hinterland-api`, the
parents/landing Static Web Apps, and the `hinterland-dev-rg` environment (Gordi
subscription, billing-only sharing). Mobile package names and selected wire
contracts remain compatibility exceptions until a separate migration. The old
Dragonfly environment stays as historical reference and is never renamed in
place.

1. **Android/iOS bundle ids.** Play package names are permanent per
   Console listing. The rebrand plan resolves this deliberately: preview
   staging builds now use `app.thehinterlandguide.staging`, while the
   current Internal Testing pilot keeps `com.dragonfly.app` until the
   production Play listing cutover.
2. **`DRAGONFLY_*` env var names and the `env_prefix="DRAGONFLY_"` in
   `backend/app/core/config.py`** (including the `dragonfly_*` Settings
   field names they bind to). `HINTERLAND_` env vars are now accepted by
   the settings layer for the Gordi Container Apps environment, including
   the renamed kid-JWT/dev-auth keys. `DRAGONFLY_` remains supported and
   takes precedence during the overlap window. Comments around these
   compatibility names may say Hinterland.
3. **Client-coordinated protocol strings:** the deep-link scheme
   `dragonfly` (`exp+dragonfly://...`), the kid QR handoff format
   `dragonfly.kid-handoff.v2`, and the JWKS path
   `/.well-known/dragonfly-kid-jwks.json`. Installed clients validate
   these exact strings. The kid-JWT issuer/audience moved with the
   Hinterland Azure environment.
4. **Remaining compatibility identifiers:** Android/iOS bundle ids,
   SecureStore key `dragonfly.bearer_token`, database/user names
   (`dragonfly`), python package paths, content/sprite ids and icon
   keys, terrain seed strings, structlog event names, test fixture ids,
   `package.json`/`pyproject.toml` project names, EAS slug
   `dragonfly`, and legacy `DRAGONFLY_*` env vars.
5. **Moved by ADR 0014:** public domains, active Azure resource names,
   Entra app registrations, landing/parents deploys, and kid-JWT
   issuer/audience now use Hinterland/Gordi identifiers. Old
   Dragonfly/GCP/Firebase names remain only in historical records or
   compatibility fields.
6. **Local folder names** (e.g. `C:\GitHub\Dragonfly`) — cosmetic,
   per-machine, renamed whenever convenient; nothing in the repo may
   depend on the checkout folder name.
7. **Historical records stay verbatim:** ADR 0001–0012 bodies, the
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
