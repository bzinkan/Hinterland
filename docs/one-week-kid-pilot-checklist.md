# One-week kid-pilot checklist

Day-by-day checklist for the controlled Google Play Internal testing
pilot with known adult-supervised kid testers. This is the "what does
Brian have to do this week" list -- everything that can't be done by
an agent.

Sister docs:
- [`google-play-internal-testing.md`](google-play-internal-testing.md)
  for the Play Console process.
- [`risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  for the precise-location decision that gates Day 2.
- [`app-store-compliance-checklist.md`](app-store-compliance-checklist.md)
  for what's blocking moving beyond Internal testing.
- [`risks/0005-beta-launch-human-action-items.md`](risks/0005-beta-launch-human-action-items.md)
  for the broader "what does the project need from humans" list.

## Pre-flight (before Day 1)

- [ ] Brian has confirmed the play-internal AAB build path is functional
  on his EAS account (one trial build, no Play upload).
- [ ] Risk 0007 has a CHOSEN mitigation option (A, B, C, or D). Default
  if undecided: Option C (adult-supervised, known-family, internal-test
  only with explicit consent + Brian's manual review of every captured
  location pin). Anything other than Option C may require a code change
  before Day 2.
- [ ] Pilot families identified (1-3 households, 9-12yo kid with at
  least one adult per session). Their tester email addresses are in a
  private spreadsheet, NOT in the repo.
- [ ] Brian's calendar has at least one 30-min onboarding slot per
  family in the W1 window.
- [ ] Backend is on the Azure side per the Phase 0-11 migration. The
  `api.dragonfly-app.net` and `parents.dragonfly-app.net` URLs both
  return 200.

## Day 1: Backend smoke + AAB build

### Backend smoke

```sh
# /health
curl -sS https://api.dragonfly-app.net/health
# Expect: {"status":"ok","env":"prod","version":"0.1.0"}

# JWKS endpoint -- proves the Phase 6a kid-JWT path is wired
curl -sS https://api.dragonfly-app.net/.well-known/dragonfly-kid-jwks.json | jq .
# Expect: {"keys":[{"kty":"RSA","kid":"k1-2026-06",...}]}

# Phase 4 smoke (parent signup -> group -> kid -> token flow)
DRAGONFLY_API_BASE_URL=https://api.dragonfly-app.net \
  python scripts/smoke_phase4.py
# Expect: ALL CHECKS PASSED at the end. If any step 4xx/5xxs, stop
# and read the response body BEFORE building the AAB.
```

The smoke script provisions a throwaway parent (`smoke+<ts>@dragonfly-test.invalid`)
and exercises the full Phase 4 flow. If anything fails, the AAB build
should NOT happen the same day.

- [ ] `/health` returns 200.
- [ ] `/.well-known/dragonfly-kid-jwks.json` returns a JWKS with the
  expected kid.
- [ ] `scripts/smoke_phase4.py` exits 0 ("ALL CHECKS PASSED").
- [ ] At least one /v1/auth/consent POST shows up in the Container App
  logs from the smoke run.
- [ ] The `parent_consent_records` table exists on prod (verify with
  `\dt parent_consent_records` from `psql`). PR
  `feat(consent): persist parent consent records` added it; if it's
  missing, the migration didn't run -- stop and run
  `alembic upgrade head` before continuing.
- [ ] A consent row from the smoke run is visible in the table
  (`select id, parent_email, policy_version, recorded_at from
  parent_consent_records order by recorded_at desc limit 3;`). This
  is the audit-of-record we'll show a COPPA auditor; if rows aren't
  landing, the pilot doesn't start.

### AAB build

```sh
cd mobile
APP_ENV=play-internal npx eas-cli build \
  --platform android \
  --profile play-internal \
  --non-interactive
```

- [ ] EAS build completes successfully.
- [ ] AAB downloaded locally; package name is `com.dragonfly.app`
  (NOT `.dev` / `.staging`).
- [ ] Display name on a temporary install is "Dragonfly Internal".
- [ ] Version code is the EAS-incremented value, not 1 (would indicate
  a fresh EAS project init, which means the version-code monotonicity
  for Play uploads is broken).

## Day 2: Play Console upload + device smoke

Following [`google-play-internal-testing.md`](google-play-internal-testing.md):

- [ ] Play Console app entry created (or reused if W0 already created
  it). DO NOT rename it.
- [ ] AAB uploaded to **Internal testing** track.
- [ ] Tester email list created and the operator email is on it.
- [ ] Rollout to Internal testing started.
- [ ] Opt-in URL captured; saved in the same private spreadsheet as
  the tester emails.

### Device smoke on Brian's phone

Install the build on Brian's own Android phone via the opt-in URL.

- [ ] Install completes; app icon is the "Dragonfly Internal" icon.
- [ ] App opens to the splash + first run UI without crashing.
- [ ] Sign-in screen renders. On Android native the Firebase email +
  password form is what appears (Phase 11a MSAL is web-only).

### Brian's-account end-to-end

Run through the full Phase 1 flow with Brian's own (parent) account:

- [ ] Parent signup completes via the in-app form.
- [ ] Group create flow works; a 6-character join code is shown.
- [ ] Kid create flow works; the QR handoff modal renders.
- [ ] Kid login on a SECOND Android device (or a second emulator)
  succeeds by scanning the QR -- the QR payload is
  `dragonfly.kid-handoff.v2` per Phase 7.
- [ ] Kid observation submit completes outdoors. Photo and location
  populate.
- [ ] Observation appears in "My Observations" within ~10 seconds.
- [ ] Review queue (parent account) renders without 500s. Empty state
  is fine.

## Day 3-4: First pilot family session

- [ ] 30-min onboarding session held with family #1, with Brian
  present.
- [ ] Parental consent captured via the public `/consent` page BEFORE
  the kid account is provisioned. Brian confirms (a) the
  `auth.consent.recorded` event lands in Container App logs AND (b)
  a `parent_consent_records` row exists for that parent's email.
  Note the row's ULID `id` in the session journal -- this is the
  per-family receipt.
- [ ] After the parent signs in via MSAL and hits
  `POST /v1/auth/parent-signup`, confirm the matching consent row's
  `linked_parent_user_id` got populated with the new `users.id`
  (this proves the parent → consent → users threading works
  end-to-end and we have a durable join key for the audit trail).
- [ ] Family creates parent account, group, kid account in real time.
- [ ] At least one real outdoor observation is submitted from the
  kid's device.
- [ ] The observation appears in "My Observations" before the family
  leaves the session.
- [ ] If the observation triggered moderation (it should not for
  clean photos), Brian shows the parent the review queue.
- [ ] Account-deletion path demonstrated to the parent before they
  leave.
- [ ] Feedback notes captured in a session journal (NOT in the repo;
  store in the same private location as the tester emails).

## Day 5: Second family or hardening

Pick one based on Day 3-4 outcome:

### If Day 3-4 went smoothly:

- [ ] Repeat the Day 3-4 flow with pilot family #2 (and #3 if
  applicable).

### If Day 3-4 surfaced bugs:

- [ ] File bugs in the issue tracker, scoped to the specific
  reproduction step. Mark each with `pilot-blocker` or `pilot-nice-
  to-have`.
- [ ] Patch the highest-severity blockers, rebuild the AAB, push to
  Play Internal testing (same opt-in URL; testers get the update
  automatically), and re-smoke before the next family.
- [ ] No family-two session until the blockers are patched.

## Day 6: Review-queue + non-iNat sanity

Confirm the non-happy-path surface for the pilot:

- [ ] One deliberately moderation-triggering photo submitted (e.g.
  blurry indoor photo of something that's not an organism). Confirm
  it lands in the parent's review queue and the parent can
  Reject it cleanly.
- [ ] Confirm `DRAGONFLY_INAT_OAUTH_TOKEN` is NOT set on the Container
  App. This guarantees no observation from the pilot leaks to the
  public iNaturalist project. Run `az containerapp show` and grep
  the env list.
- [ ] Confirm the Play Console **Internal testing** rollout is still
  in its un-promoted state (i.e. no accidental promotion to Closed
  or Production).

## Day 7: Reflect + handoff

- [ ] Pilot families thanked.
- [ ] Bugs filed during the week summarized in a single internal
  note. Mark which are blockers for moving to Closed testing.
- [ ] Decide the W2 disposition: continue Internal testing with the
  same families, expand the tester list (still Internal), or pause
  for the Data Safety + Closed testing prep cycle described in
  [`app-store-compliance-checklist.md`](app-store-compliance-checklist.md).

## What is NOT in this pilot

These are explicit non-goals for the W1 pilot:

- **No public iNat submissions.** `DRAGONFLY_INAT_OAUTH_TOKEN`
  unset; the iNat client refuses to call. See
  [`risks/0002-async-workers-production-unwired.md`](risks/0002-async-workers-production-unwired.md).
- **No public Play release.** Internal testing only; no rollout to
  Closed, Open, or Production.
- **No App Store / TestFlight.** iOS path is separate work.
- **No marketing / outreach.** The opt-in URL is shared by direct
  email to known testers only.
- **No data-analytics SDKs added or enabled.** The Phase 1 surface
  ships with Sentry only; nothing more for the pilot.
- **No iNat OAuth token application.** That's a precondition for
  Closed testing per
  [`risks/0001-inat-cv-correctness-target-unverified.md`](risks/0001-inat-cv-correctness-target-unverified.md).
