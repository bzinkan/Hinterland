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
  for the coarse/no-location gate below.
- [`docs/observation-w1-promotion.md`](observation-w1-promotion.md)
  for the server promotion and exact-AAB evidence contract.

This pilot is **adult-supervised, known-family kid testing only** —
1–3 kids age 9–12, with at least one adult per session, on the Play
Internal testing track. No classroom rollout. No public release.

## Pre-flight (adult setup, before the kid arrives)

- [ ] Install from the Play Internal testing opt-in URL captured during
  Gate 2 of
  [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md).
  The opt-in URL lives in the private tester list (location outside
  this repo).
- [ ] Confirm the installed build's EAS build ID, version code, and AAB SHA-256
  match the W1 release record. Use a device with at most 4 GB RAM for the
  required low-end evidence run.
- [ ] Home-screen app name reads **"The Hinterland Guide Internal"**. If it reads
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
  - `build channel: play-internal`.
  - `updates channel: play-internal` and `updates enabled: yes`; these rows
    come from the native EAS Updates runtime, not display-only app config.
  - `updates source: embedded` and `updates runtime: 0.1.0`. Stop if the
    source is `remote`: W1 must execute the JavaScript/assets embedded in the
    exact AAB whose hash is in the release record.

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
  `hinterland.kid-handoff.v1` per Phase 7. Have the kid's device ready
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
  - **Location** — optional coarse-only prompt
    (`ACCESS_COARSE_LOCATION`) or no-location path. Fine/precise location is a
    hard stop.
  - **Nothing else.** No microphone, no contacts, no SMS, no
    calendar. If any of those prompt, stop immediately.
- [ ] Kid captures or selects ONE safe outdoor organism photo (the
  adult chose the target organism beforehand).
- [ ] Confirmation preview uses `contain` and shows the complete image.
- [ ] Kid selects from bundled/project catalog. In a second dry run, manual
  display text or Unknown saves without earning a Dex species. Pre-save iNat
  CV is off.
- [ ] Kid may add approximate location or no location. Denial cannot block
  save; a library photo does not silently inherit current device location.
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
- [ ] Observation appears once in the **Field Journal** in observed-time order.
  W1 must show the metadata-only private card and exact text, “This photo is
  private during the pilot.” No image placeholder may trigger a photo request,
  and the photo helper is hidden while its capability is false.
- [ ] The Maestro release flow asserts that saved card, private status, and
  absence of photo bytes—not merely that the Field Journal screen exists.
- [ ] Adult confirms no crash, no confusing screen, no surprise dialog
  (no consent re-prompt, no third-party SDK login dialog, no
  unexpected permission prompt).

### Recovery and account-isolation pass

- [ ] Enable airplane mode, capture/confirm, and verify child-friendly local
  safe-copy messaging instead of a raw API error.
- [ ] Restore network, let Azure PUT finish, kill before create response,
  relaunch, and verify the queue resumes with the same submission ULID.
- [ ] Confirm exactly one server observation, membership increment, and reward
  set. The queue JPEG remains until reconciliation, then clears.
- [ ] Sign out with unsynced work and verify the adult warning. Sign in as a
  different kid and verify prior queue, draft, server query, image, and signed
  photo state is invisible.
- [ ] Sign back in as the original kid and resume or explicitly discard only
  that kid's work.
- [ ] Destroy/recreate the Android picker activity and verify the selected
  result recovers. Deny location and verify save still succeeds; an imported
  library image never inherits current-device location.

## Adult review (parent account, after the kid step)

Switch back to the parent's device.

- [ ] Open the **Adult tools** section on the Settings tab and tap
  the review-queue link.
- [ ] Confirm review queue renders without 500s. Empty is expected: W1 NoOp is
  `pilot_private`, not clean/quarantine, and grants no thumbnail.
- [ ] Operator canary confirms `pilot_private`, no signed-photo URL, and no
  iNaturalist outbox/queue work.
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
