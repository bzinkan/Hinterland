# Risk 0005: Beta launch human-action items

- **Status:** Open
- **Date filed:** 2026-05-10
- **Source:** AGENTS.md Phase 11 exit criteria ("first closed-beta group is invited" + "at least one real kid submits at least one real outdoor observation")
- **Owner:** Brian (only Brian can do these; no autonomous unblock path)

## What we have

PRs #59-62 shipped the Phase 11 codebase: signed-GET photo URL + mobile review queue UI, stale-review cleanup admin task, dispatcher replay admin task + `dispatched_at` column, and Cloud Monitoring alarms + dogfood dashboard. Together with Phases 0-10 they fully implement the closed-beta surface from the code's perspective.

What's missing for the AGENTS.md Phase 11 exit criteria isn't more code -- it's a list of human-only steps.

## Human action items, in dependency order

### 1. Legal review of the privacy policy

[`docs/privacy-policy-DRAFT.md`](privacy-policy-DRAFT.md) is structurally complete but is NOT publishable. Required:

- [ ] Lawyer review with a kids-app / COPPA practice
- [ ] Publish the reviewed text at `https://dragonfly-app.net/privacy` (DNS + a trivial static-hosted page; Cloudflare Pages or a 1-line nginx config will do)
- [ ] Publish a Terms of Service at `https://dragonfly-app.net/terms` (lawyer also)
- [ ] Set up `support@dragonfly-app.net` (Gmail forward is fine for beta scale)
- [ ] Set up `privacy@dragonfly-app.net` (same shape)

Without these URLs returning HTTP 200, both Apple and Google will reject the beta submission.

### 2. Resolve risks 0001 + 0002 (iNat token + production worker wiring)

Closed beta with a working observation pipeline requires risk 0001 (iNat OAuth token) and the items from risk 0002 (Eventarc trigger, Cloud Tasks queue, OIDC verification on `/internal/*`, Vision API enabled, observations.moderation_status migration). See those risks for the full checklists.

### 3. Apply Phase 11 Terraform

PRs #54-62 of Phase 10 + 11 added Terraform changes that haven't been applied yet to the dev project:

- [ ] `terraform apply -var-file=environments/dev.tfvars` from `infra-gcp/`
- [ ] Verify the new alarms appear in Cloud Monitoring
- [ ] Verify the dogfood dashboard appears under Monitoring → Dashboards

Pattern is the same as previous Terraform applies (see [risk 0003](0003-dispatcher-snapshots-and-perf-not-fully-validated.md) followups; we paid the ADC quota-project tax there too).

### 4. Wire the new admin tasks into Cloud Scheduler

Three nightly Cloud Run Jobs need creating + wiring (same shape as the existing `dragonfly-cleanup-smoke` Job + cron from PR #26):

- [ ] `dragonfly-rarity-refresh` - runs `python -m admin.rarity_refresh` (job spec)
- [ ] `dragonfly-sweep-stale-reviews` - runs `python -m admin.sweep_stale_reviews`
- [ ] `dragonfly-dispatcher-replay` - runs `python -m admin.dispatcher_replay`
- [ ] Cloud Scheduler crons for each, gated dev-only in Terraform like `dragonfly-cleanup-smoke-nightly`

This is repetitive Terraform work -- once the first new job is wired, copy-paste delivers the rest. Estimated: 2 hours including verification each fires once.

### 5. Run the Phase 5 + 10 device verifications you skipped

Pending acknowledgments from earlier phases:

- [ ] **Phase 6 e2e:** install Expo Go (or EAS Build dev client), paste a parent ID token, verify the full submit → display path on a real Android phone
- [ ] **Phase 10 e2e:** run `python scripts/sync_expeditions.py` against dev Cloud SQL with a parent token loaded; verify the five starters appear in the mobile Expeditions tab; complete one starter end-to-end and confirm `expedition_complete` reward shows up
- [ ] **Phase 11 e2e:** as a parent account, navigate Settings → "Open review queue", confirm the empty state. Get a kid to submit a deliberately flagged photo (or use the moderation worker dev path); confirm it appears in the parent's queue with the photo rendering and that Approve / Reject work

### 6. App-store submissions

Per [`docs/app-store-compliance-checklist.md`](app-store-compliance-checklist.md):

- [ ] EAS Build production iOS + Android
- [ ] Apple App Store Connect setup, screenshots, Privacy Nutrition Label, Kids Category opt-in
- [ ] Google Play Console setup, Data Safety form, Designed for Families opt-in
- [ ] TestFlight Internal Testing track + invite Brian + a couple of beta testers
- [ ] Google Play Internal Testing track, same

Apple's kids-app review historically takes 5-10 business days and roughly half of first submissions get rejected on minor copy issues. Budget two rejection cycles into the launch timeline.

### 6b. Verify the consent audit ledger before the first kid signs

PR `feat(consent): persist parent consent records` swapped the
`POST /v1/auth/consent` endpoint from "log only" to "insert a
`parent_consent_records` row AND log." Before opening the gates:

- [ ] Confirm the Alembic head ran on dev Cloud SQL
      (`alembic upgrade head` from the api job image, or rolled in via
      the Cloud Run startup migration step)
- [ ] Hit `POST /v1/auth/consent` with a throwaway email and confirm
      the response carries a 26-char ULID `id` plus the current
      `policy_version`
- [ ] Confirm a row landed in `parent_consent_records` and the
      structured log event `auth.consent.recorded` includes the same
      `consent_id`
- [ ] Walk a real parent through web consent → parent_signup and
      confirm the row's `linked_parent_user_id` is back-stamped on
      signup (the linker matches case-insensitively on
      Entra-verified email, newest unlinked row only)

This is what we'll point a COPPA auditor at if Apple or Google asks
"prove the parent saw policy X at time Y." Without it, our audit is
30-day-retention Cloud Logging only.

### 7. Pick the first beta group

The Phase 11 exit criterion says "first closed-beta group is invited" + "one real kid submits one real outdoor observation." Concrete steps:

- [ ] Identify 1-3 families willing to dogfood. Brian's own kid + one of Brian's neighbors / friends with kids 9-12 is the natural starting set
- [ ] Schedule 30-min onboarding session per family: install Expo Go, sign up parent, create kid, walk through one expedition outdoors together
- [ ] Watch the dogfood dashboard during the session; pull Cloud Logging if anything stalls
- [ ] After the session: collect feedback, file issues. Iterate before opening the gates wider

## Mitigation

Everything that CAN ship from the code side has shipped. The kid experience is structurally complete. What remains is the operational + legal + adoption work that no amount of code can substitute for.

The dispatched_at column + replay task means a Cloud Run crash mid-dispatch doesn't drop rewards permanently -- the next nightly run picks them up. The signed-GET photo endpoint + mobile review queue UI means an adult CAN review quarantined photos as soon as moderation is wired (risk 0002). The alarms catch operational drift even before real traffic builds up.

## Closing this risk

This risk closes when:

- Privacy policy + ToS are live URLs (item 1)
- Risks 0001 + 0002 + 0003 + 0004 are closed
- The first beta family submits one real outdoor observation that flows through the full pipeline (presign -> upload -> create -> moderation -> dispatch -> mobile celebration -> Dex entry -> nightly iNat submit)
- Brian or a tester confirms the celebration sequence rendered correctly
