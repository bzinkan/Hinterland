# Roadmap

Hinterland's Phase 1 feature surface is code-complete for a controlled beta.
The active roadmap is risk closure and pilot hardening, not new product scope.

## Current Target

W1 Android Internal Testing with 1-3 known, adult-supervised kid testers.

Required before any kid sees the build:

1. Azure API deploy path is green and the stale Cloud Run workflow cannot
   recreate GCP services.
2. Parent/teacher setup works through the parents web app using Entra.
3. Native kid QR handoff works end to end:
   `create kid -> QR -> /v1/auth/kid-exchange -> /v1/me`.
4. `play-internal` Android build blocks fine location and requests coarse
   foreground location only.
5. Consent ledger writes are visible in Postgres and logs.
6. iNat public submission is off.
7. Physical-device pilot script passes.

## Risk Closure Order

### 1. Platform Hygiene

- Keep ADR 0010 as source of truth.
- Disable old Cloud Run deployment paths.
- Maintain Azure API deploy workflow: ACR build, Container Apps update,
  Alembic migration, public smoke, optional authenticated smoke.
- Keep GCP/Firebase only where ADR 0010 explicitly preserves them for
  residual hosting/DNS/rollback.

### 2. Auth And Pilot Flow

- Production API accepts Entra adult tokens and Hinterland kid JWTs.
- Route code resolves authenticated users through `resolve_current_user_row`.
- Native Android play-internal/production does not issue Firebase ID tokens.
- Kids sign in by scanning `dragonfly.kid-handoff.v2` QR payloads.
- Account deletion request exists at `DELETE /v1/me` and is wired in Settings.

### 3. Store And Privacy Gates

- Resolve Risk 0007 with Option B for Play Internal: coarse-only location.
- Publish lawyer-reviewed privacy policy and Terms before closed/public store
  tracks.
- Set up `support@dragonfly-app.net` and `privacy@dragonfly-app.net`.
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
