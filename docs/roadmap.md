# Roadmap

Hinterland's Phase 1 feature surface is code-complete for a controlled beta.
The active roadmap is risk closure and pilot hardening, not new product scope.

## Current Target

W1 Android Internal Testing with 1-3 known, adult-supervised kid testers.

Required before any kid sees the build:

1. Azure API deploy path is green and the stale Cloud Run workflow cannot
   recreate GCP services.
2. Parent setup works through the parents web app using Entra and lands in
   parent-managed Groups.
3. Native kid QR handoff works end to end:
   `create kid -> QR -> /v1/auth/kid-exchange -> /v1/me`.
4. `play-internal` Android build blocks fine location and requests coarse
   foreground location only.
5. Consent ledger writes are visible in Postgres and logs.
6. iNat public submission is off.
7. Physical-device pilot script passes.

The current Play v12 evidence has not completed the at-most-4-GB device,
accepted adult dry run, actual alert receipt, supervised family session,
post-session audit, or recorded go/no-go decisions. Group-first changes remain
under a merge/deploy hold until those records are complete and archived.

## Risk Closure Order

### 1. Platform Hygiene

- Keep ADR 0010 and ADR 0014 as source of truth.
- Disable old Cloud Run and Firebase/GCP deployment paths.
- Maintain Azure API deploy workflow: ACR build, Container Apps update,
  Alembic migration, public smoke, optional authenticated smoke.
- Keep `infra-gcp/` and old ADRs as historical reference only; active hosting,
  auth, API, storage, DNS/deploy, and CI paths are Azure-only.

### 2. Auth And Pilot Flow

- Production API accepts Entra adult tokens and Hinterland kid JWTs.
- Route code resolves authenticated users through `resolve_current_user_row`.
- Native Android play-internal/production does not issue third-party auth SDK
  tokens.
- Kids sign in by scanning `hinterland.kid-handoff.v1` QR payloads.
- Account deletion request exists at `DELETE /v1/me` and is wired in Settings.
- `/groups` is the canonical adult route; `/classroom` is retained only as a
  one-release redirect.
- The group creator manages group settings and adult invitations/removal. Every
  parent manages only their own children; shared group membership grants no
  cross-family photo, review, handoff, or child-data access.
- Educator-specific onboarding and classroom administration remain deferred.

### 3. Store And Privacy Gates

- Resolve Risk 0007 with Option B for Play Internal: coarse-only location.
- Publish lawyer-reviewed privacy policy and Terms before closed/public store
  tracks.
- Set up `support@thehinterlandguide.app` and `privacy@thehinterlandguide.app`.
- Document deletion follow-up for linked kids/photos/iNat contributions.

### 4. Science And Safety Pipeline

- Close Risk 0001: iNat project account/OAuth token plus 50-photo CV benchmark.
- Close Risk 0002 on Azure: Azure Content Safety moderation, async queue/event
  wiring, iNat retry/DLQ, scheduled rarity/sweep/replay jobs, monitoring.
- Keep moderation and iNat off for W1 Internal Testing.

### 5. Dispatcher And Content

- Close Risk 0003 with current code reality: geohash-3 fallback, missing
  snapshot scenarios, real-Postgres replay/idempotency harness, and measured
  dispatcher p95 after 50 real observations.
- Close Risk 0004 with an author-time-only expedition draft tool or explicitly
  mark LLM-assisted drafting non-blocking for beta.

## Explicit Non-Goals For W1

- Public iNat submission.
- Public/closed Play release.
- iOS/TestFlight.
- Push notifications.
- Territory map.
- Friend challenges or social graph.
- Final Sanctuary audio/illustrations.
- Marketing, growth loops, ads, public chat, DMs, or kid free text.

## Closed Beta Exit Criteria

- First invited beta family/group completes onboarding.
- At least one real kid submits one real outdoor observation through the Azure
  service.
- Observation persists, appears in the app, updates Dex/rewards/Sanctuary, and
  does not depend on iNat/moderation/geocoding being live at submit time.
- Safety/legal gates for the chosen store track are complete.
