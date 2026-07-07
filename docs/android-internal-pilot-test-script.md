# Android Internal pilot test script

One-pass test script for the adult tester (Brian) to walk a real kid
through the app on a real Android device during the W1 pilot. This is
the **physical-device** runbook — not an emulator dry-run. Use it once
per pilot session, after the gate checks in
[`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
all pass.

Sister docs:
- [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
  for the strict gates that must already be green before this script
  runs.
- [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md)
  for the abort / rollback procedure if any step below fails.
- [`docs/google-play-internal-testing.md`](google-play-internal-testing.md)
  for the Play Console upload procedure that produced the build under
  test.
- [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  for the precise-location mitigation option that gates the kid-flow
  permission prompt below.

This pilot is **adult-supervised, known-family kid testing only** —
1–3 kids age 9–12, with at least one adult per session, on the Play
Internal testing track. No classroom rollout. No public release.

## Pre-flight (adult setup, before the kid arrives)

- [ ] Install from the Play Internal testing opt-in URL captured during
  Gate 2 of
  [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md).
  The opt-in URL lives in the private tester list (location outside
  this repo).
- [ ] Home-screen app name reads **"Hinterland Internal"**. If it reads
  bare "Hinterland", the AAB came from the `production` profile, not
  `play-internal` — stop and follow
  [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md).
- [ ] Confirm environment + API base URL on the device. Open the app,
  tap the **Settings** tab (cog icon, bottom tab bar), scroll to the
  **Build** section, and read the three rows:
  - `env: play-internal` (must NOT read `development` on a tester
    device; `play-internal` is the discriminator for the Play
    Internal track build).
  - `API: https://api.thehinterlandguide.app`.
  - `updates channel: play-internal`. The `updates channel` row is
    the most reliable discriminator — it is sourced verbatim from
    the `play-internal` branch of `app.config.ts`. Use this row as
    the source-of-truth if the `env:` row collapses to a different
    string at runtime.

  If the device cannot reach the app at all, cross-check the API
  health from a desktop terminal:

  ```sh
  curl -sS https://api.thehinterlandguide.app/health
  # Expect: {"status":"ok","env":"prod","version":"0.1.0"}
  ```

- [ ] Create or sign in to the parent account from the parents web app
  at `https://parents.thehinterlandguide.app`. The native `play-internal`
  build does not use native adult password sign-in.
- [ ] Record consent. Open the public `/consent` page (the
  parents-facing web host — verify the host returns 200 first via
  `curl -I` if the URL has changed since
  [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
  was last updated). Submit the form, then capture the consent
  receipt returned by `POST /v1/auth/consent`:
  - `id` — the 26-char ULID. **Write this down in the session
    journal now**; it is the per-family receipt the audit trail joins
    on and the artifact a COPPA auditor will ask for.
  - `recorded_at` — server timestamp.
  - `policy_version` — must match the policy version currently
    served at `/consent`.
- [ ] Create the group from the parent account in the parents web app. The
  parent-account group-create flow returns a 6-character join code
  (same flow exercised by `scripts/smoke_azure_parent_kid.py`).
- [ ] Create the kid account from the parent account in the parents web app.
  The kid-create flow renders a QR handoff modal whose payload is
  `dragonfly.kid-handoff.v2` per Phase 7. Have the kid's device ready
  to scan.

## Kid flow (adult-supervised; the kid is the one tapping)

The adult stays in the room and watches every screen. The kid does
the tapping.

- [ ] Kid signs in. On the Android device, open **Sign in** -> **Scan kid QR**
  and scan the QR from the adult screen to complete the handoff.
- [ ] Kid sees the welcome / onboarding screen without crashing or
  showing a blank state.
- [ ] Kid grants only the **expected** permissions when prompted:
  - **Camera** (via expo-camera).
  - **Photo library** (implicit via expo-image-picker; no Android
    13+ permission prompt expected).
  - **Location** — the prompt the kid sees must match the option
    recorded in
    [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
    for this build:
    - Option A → no precise-location prompt; manual location picker.
    - Option B → coarse-only prompt (`ACCESS_COARSE_LOCATION`). This is the
      chosen `play-internal` path.
    - Option D → pilot deferred; this script should not be running.
  - **Nothing else.** No microphone, no contacts, no SMS, no
    calendar. If any of those prompt, stop immediately.
- [ ] Kid captures or selects ONE safe outdoor organism photo (the
  adult chose the target organism beforehand).
- [ ] Kid confirms the species manually. The pilot does not require
  iNat CV to be live — manual species confirmation is the documented
  fallback for the W1 pilot.
- [ ] Kid taps submit on the observation.
- [ ] Kid sees the success / reward screen. Submission must NOT block
  on iNat, Google/Maps, moderation, or rarity refresh — the kid
  should see success regardless of whether those subsystems are
  reachable. This is a hard AGENTS.md invariant.
- [ ] **If the submit triggers a Sanctuary unlock**, the Sanctuary
  reveal modal appears on top of the success screen with header
  "Something changed in your Sanctuary" + the reward title and
  detail from the dispatcher. The kid can dismiss via either
  **See Sanctuary** (which navigates to the Sanctuary tab) or
  **Done** (which closes the modal and returns via the existing
  flow). The modal must NOT auto-dismiss and must NOT auto-navigate.
  If the submit triggered no Sanctuary reward (e.g. repeat-find of a
  species the kid already has, in a zone they have already deepened),
  the reveal modal must NOT appear and the existing success screen
  stays unchanged. See `docs/sanctuary.md` section 10
  "new-arrival reveal".
- [ ] Observation appears in Home / My Observations within ~10
  seconds. (Soft latency target; the success screen itself is the
  hard gate, the list-refresh is best-effort.)
- [ ] Adult confirms no crash, no confusing screen, no surprise dialog
  (no consent re-prompt, no third-party SDK login dialog, no
  unexpected permission prompt).

## Adult review (parent account, after the kid step)

Switch back to the parent's device.

- [ ] Open the **Adult tools** section on the Settings tab and tap
  the review-queue link.
- [ ] Confirm the review queue renders without 500s. Either:
  - Empty state renders cleanly (expected — clean outdoor photos
    should not trigger moderation; moderation is the noop path in
    W1 per
    [`docs/risks/0002-async-workers-production-unwired.md`](risks/0002-async-workers-production-unwired.md)),
    OR
  - A pending item renders with a working photo thumbnail and
    visible Approve / Reject buttons.
- [ ] Confirm the kid-facing surface had no public / social /
  free-text features visible during the kid's run — no public chat,
  no DMs, no kid-to-kid free text, no public feed. (This is an
  AGENTS.md Phase 1 invariant; surfacing one is an automatic
  hard-stop.)
- [ ] Confirm no ads, no marketing prompts, no third-party SDK login
  dialogs (Google Sign-In, Facebook, etc.) appeared at any point on
  the kid's device.

## Exit criteria

- The pilot session is GREEN only if every checkbox above passes on
  at least one physical Android device. Brian's own phone counts for
  the dry-run; a different family-supplied device is the actual
  kid-test gate.
- Any FAIL on auth, persistence, moderation, location handling, or
  kid-data exposure → immediately follow
  [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md).
- Document the session in the private session journal (NOT in the
  repo; same private location as the tester emails). The journal
  entry MUST include the `parent_consent_records` receipt `id`
  captured in pre-flight — that is the per-family receipt the audit
  trail joins on.

## What this doc is NOT

- It is not the gate checklist — that is
  [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md).
- It is not the rollback / abort plan — that is
  [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md).
- It is not the Play Console upload procedure — that is
  [`docs/google-play-internal-testing.md`](google-play-internal-testing.md).
- It is not the policy decision authority for location — that is
  [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).
  This script only verifies that the prompt the kid sees matches the
  option already chosen there.
