# One-week kid-pilot checklist

Strict-gate checklist for the controlled Google Play Internal testing
pilot with 1–3 known, adult-supervised kid testers. This is the "what
does Brian have to do before the kids touch the build" list —
everything that can't be done by an agent, expressed as hard gates
that must close in order.

Sister docs:
- [`docs/google-play-internal-testing.md`](google-play-internal-testing.md)
  for the Play Console process.
- [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  for the precise-location decision that gates the AAB build.
- [`docs/app-store-compliance-checklist.md`](app-store-compliance-checklist.md)
  for what's blocking moving beyond Internal testing.
- [`docs/risks/0005-beta-launch-human-action-items.md`](risks/0005-beta-launch-human-action-items.md)
  for the broader "what does the project need from humans" list.
- [`docs/android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md)
  for the end-to-end on-device script Brian runs on his own phone
  before any kid sees the build.
- [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md)
  for the rollback playbook triggered by the hard-stop conditions
  below.

## Scope reminder

The W1 pilot is a tiny, adult-supervised, Internal-testing-only smoke
of the kid experience. It exists to prove the round-trip works on a
real Android device in a real adult's hands with a real kid's eyes —
nothing more. Anything that looks like growth, marketing, or public
exposure is out of scope.

- First kid test is 1–3 known kids only.
- Parent / guardian must be present.
- No classroom-wide rollout.
- No public production release.
- No real iNaturalist submission unless explicitly configured and
  approved by Brian.
- Location policy risk per
  [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  must be acknowledged.

## Gate 1: before AAB upload

Nothing leaves Brian's laptop until every box in this gate is
checked. If any step fails, fix it and re-run the gate — do not
proceed.

### Backend smoke

```sh
# /health
curl -sS https://api.dragonfly-app.net/health
# Expect: {"status":"ok","env":"prod","version":"0.1.0"}

# JWKS endpoint -- proves the Phase 6a kid-JWT path is wired
curl -sS https://api.dragonfly-app.net/.well-known/dragonfly-kid-jwks.json | jq .
# Expect: {"keys":[{"kty":"RSA","kid":"k1-2026-06",...}]}

# Azure parent/kid smoke (parent signup -> group -> kid -> handoff flow)
DRAGONFLY_API_BASE_URL=https://api.dragonfly-app.net \
DRAGONFLY_SMOKE_ENTRA_BEARER="<access-token>" \
  python scripts/smoke_azure_parent_kid.py
# Expect: ALL CHECKS PASSED -- Azure parent/kid handoff flow works end-to-end.
# If any step 4xx/5xxs, stop and read the response body BEFORE
# building the AAB.
```

- [ ] `/health` returns 200 with the expected JSON body.
- [ ] `/.well-known/dragonfly-kid-jwks.json` returns a JWKS with kid
  `k1-2026-06`.
- [ ] `scripts/smoke_azure_parent_kid.py` exits 0 with
  `ALL CHECKS PASSED -- Azure parent/kid handoff flow works end-to-end.`

### Consent ledger

- [ ] The `parent_consent_records` table exists on prod (verify with
  `\dt parent_consent_records` from `psql`). PR
  `feat(consent): persist parent consent records` added it; if it is
  missing, the migration did not run — stop and run
  `alembic upgrade head` before continuing.
- [ ] A consent row from the smoke run is visible in the table
  (`select id, parent_email, policy_version, recorded_at from
  parent_consent_records order by recorded_at desc limit 3;`). This
  is the audit-of-record we will show a COPPA auditor; if rows are
  not landing, the pilot does not start.
- [ ] The smoke run's `auth.consent.recorded` event is visible in
  Container App logs and its `consent_id` matches the ULID `id` of
  the row above.

### iNat is off

- [ ] Confirm `DRAGONFLY_INAT_OAUTH_TOKEN` is NOT set on the
  Container App. Run `az containerapp show` and grep the env list.
  This guarantees no observation from the pilot leaks to the public
  iNaturalist project. See
  [`docs/risks/0002-async-workers-production-unwired.md`](risks/0002-async-workers-production-unwired.md).

### Location policy decided

- [ ] Risk 0007 records Option B for `play-internal`.
- [ ] `APP_ENV=play-internal npm run config:play-internal` passes.
- [ ] The generated AAB manifest has no `ACCESS_FINE_LOCATION`.
- [ ] The device prompt matches approximate/coarse location behavior.

### AAB build

```sh
cd mobile
APP_ENV=play-internal npx eas-cli build \
  --platform android \
  --profile play-internal \
  --non-interactive
```

- [ ] EAS build on the `play-internal` profile completes
  successfully.
- [ ] AAB package name is `com.dragonfly.app` (NOT `.dev` /
  `.staging`).
- [ ] Display name on a temporary install is `Hinterland Internal`.
- [ ] AAB `versionCode` is monotonically greater than the last AAB
  uploaded to Play Console on the `play-internal` track. Read the
  resolved value from the EAS build summary URL — `eas.json` sets
  `appVersionSource: "remote"` + `autoIncrement: true`, so the
  canonical value lives at EAS, not in `app.config.ts`. On the very
  first `play-internal` upload, any value is acceptable.

## Gate 2: before adult dry run

No testers — not even Brian's own phone via the opt-in URL — get the
build until this gate closes.

- [ ] AAB uploaded to the **Internal testing** track in Play Console
  per [`docs/google-play-internal-testing.md`](google-play-internal-testing.md).
- [ ] Opt-in URL captured and stored in the private spreadsheet (NOT
  in the repo).
- [ ] Tester email allowlist created in Play Console and Brian's
  tester email is on it. The allowlist itself stays in the private
  spreadsheet alongside the opt-in URL — NOT in the repo.
- [ ] Brian's own Android phone installs the build via the opt-in
  URL; app icon is the `Hinterland Internal` icon and the app opens
  to the first-run UI without crashing.

## Gate 3: before first kid test

No kid sees the build until Brian has personally walked the entire
device script on his own phone and the consent-ledger linkage is
proven on a real account.

- [ ] Brian runs the entirety of
  [`docs/android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md)
  on his own phone, end-to-end, with his own throwaway parent
  account, and every checkbox in that script passes.
- [ ] After Brian's throwaway parent run, a consent row is visible in
  `parent_consent_records` for that parent's email (same `select id,
  parent_email, policy_version, recorded_at ...` query as Gate 1).
  Note the row's ULID `id` in the session journal — this is the
  per-account receipt for the dry run.
- [ ] After the throwaway parent hits `POST /v1/auth/parent-signup`,
  the matching consent row's `linked_parent_user_id` is populated
  with the new `users.id`. This proves the parent → consent → users
  threading works end-to-end on a real device, not just from the
  smoke script.
- [ ] Review queue (parent account, on Brian's phone) renders
  without 500s. Empty state is fine.
- [ ] Brian confirms the Sanctuary reveal modal appears after a
  first-find observation (the dispatcher emits `world_unlock` for
  the zone wake-up + the charismatic species cameo if authored) AND
  dismisses cleanly via both **See Sanctuary** (navigates to the
  Sanctuary tab and shows the new state) and **Done** (closes the
  modal without auto-navigating). On a repeat-find that crosses no
  threshold, the modal must NOT appear.
- [ ] At least one pilot family is scheduled with a confirmed 30-min
  onboarding slot in the W1 window, and their tester email is on the
  Play Console allowlist.

## Gate 4: after first kid test

Run this gate the same evening as the family #1 session, before any
decision about family #2.

- [ ] Session journal entry filed (private, not in repo) capturing:
  family #1's `parent_consent_records.id`, the kid's display name,
  the observation count for the session, the chosen risk 0007 option
  label, and any bugs observed.
- [ ] For the family #1 parent, `linked_parent_user_id` is populated
  on their `parent_consent_records` row (same audit-trail check as
  Gate 3, but on the real family's account).
- [ ] Bugs triaged. Anything matching the hard-stop list below
  triggers [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md)
  immediately. Anything else is filed in the issue tracker with a
  `pilot-blocker` or `pilot-nice-to-have` label, scoped to the
  specific reproduction step.
- [ ] Decision recorded in the session journal: continue with family
  #2, or stop per [`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md).
  No family #2 session until any `pilot-blocker` is patched, the
  AAB is rebuilt, the Gate 1 backend smoke is re-run, and Brian
  re-runs the device test script on his phone.
- [ ] Play Console **Internal testing** rollout is still in its
  un-promoted state (no accidental promotion to Closed, Open, or
  Production).

## Hard stop conditions

If any of the following is observed at any point during the pilot,
stop and follow
[`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md)
— do not patch in-place, do not continue to the next family, do not
"see if it reproduces."

- auth failure exposing wrong user data
- child can reach public chat or social features
- photo appears publicly without a moderation decision
- crash during submit that loses kid data
- incorrect consent state
- location / privacy surprise

The response playbook (rollback, revision pin, env-var disable,
comms to families) lives in
[`docs/android-internal-pilot-stop-plan.md`](android-internal-pilot-stop-plan.md).
This checklist only lists the triggers; the wording above is
identical to the trigger list there so the two docs cannot drift.

## What is NOT in this pilot

These are explicit non-goals for the W1 pilot:

- **No public iNat submissions.** `DRAGONFLY_INAT_OAUTH_TOKEN`
  unset; the iNat client refuses to call. See
  [`docs/risks/0002-async-workers-production-unwired.md`](risks/0002-async-workers-production-unwired.md).
- **No public Play release.** Internal testing only; no rollout to
  Closed, Open, or Production.
- **No App Store / TestFlight.** iOS path is separate work.
- **No marketing / outreach.** The opt-in URL is shared by direct
  email to known testers only.
- **No data-analytics SDKs added or enabled.** The Phase 1 surface
  ships with Sentry only; nothing more for the pilot.
- **No iNat OAuth token application.** That's a precondition for
  Closed testing per
  [`docs/risks/0001-inat-cv-correctness-target-unverified.md`](risks/0001-inat-cv-correctness-target-unverified.md).
