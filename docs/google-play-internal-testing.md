# Google Play Console: Internal Testing process

This doc walks through getting Hinterland onto the Google Play **Internal
testing** track for a controlled adult-supervised kid pilot. It covers
the Play Console steps, the build command, and the tester-onboarding
flow. It does NOT cover Closed testing, Open testing, or production --
each of those needs Data Safety, content rating, and other gates that
are out of scope for a 7-day pilot.

Related reading:
- [`app-store-compliance-checklist.md`](app-store-compliance-checklist.md)
  for the production blockers.
- [`landing-pre-play-checklist.md`](landing-pre-play-checklist.md)
  for the final public URL, landing copy, support, and privacy checks before
  using `dragonfly-app.net` in Play Console fields.
- [`one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
  for the day-by-day pilot checklist this doc feeds.
- [`risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  for the precise-location risk and the four mitigation options the
  pilot needs to pick from before the AAB is uploaded.

## ⚠️ One-time setup warnings

Read these before you click anything in the Play Console.

### The first uploaded artifact LOCKS the package name

When you upload the first AAB to a Play Console app, **the package name
on that AAB becomes permanent for that app entry**. There is no rename.
If you upload `com.dragonfly.app.dev` (development build) to a Play
Console entry intended to become the production listing, you must
**delete that Play Console entry and create a new one** to recover --
and the deletion has a multi-week cooldown.

The pilot AAB must be built from the `play-internal` EAS profile so the
package name is the FINAL `com.dragonfly.app`. The development /
preview profiles use `com.dragonfly.app.dev` / `com.dragonfly.app.staging`
and are NOT safe to upload to the production-intended Play Console
entry.

### Do not upload `com.dragonfly.app.dev` to the production-intended app

Same warning, stated for emphasis. If the only thing you read in this
doc is this section, you avoided the worst-case mistake.

## 1. Create the Play Console app entry

If a Hinterland entry does not yet exist:

1. https://play.google.com/console
2. **All apps** → **Create app**
3. App name: **Hinterland** (this is the production-listing display name;
   pilot tester devices show "Hinterland Internal" because that's what
   the AAB's `<application android:label>` resolves to from the
   `play-internal` APP_ENV branch in `mobile/app.config.ts`)
4. Default language: English (United States)
5. App or game: **App**
6. Free or paid: **Free**
7. Confirm the declarations (developer program policies, US export laws)
8. **Create app**

If an entry already exists, skip to step 2.

## 2. Build the AAB

From `mobile/`:

```sh
# One-time: EAS account setup
npx eas-cli login
npx eas-cli project:init    # only if mobile/ has no project id

# Build the Android App Bundle for Play Console
APP_ENV=play-internal npx eas-cli build \
  --platform android \
  --profile play-internal \
  --non-interactive
```

The build runs in EAS's cloud. When it completes, EAS prints a download
URL for the `.aab` file. Save it locally; do NOT commit it. Add the
filename to `.gitignore` if you download it inside the repo.

Expected build properties:
- Package name: `com.dragonfly.app`
- Display name: `Hinterland Internal`
- App version: `0.1.0` (the `version` field in `mobile/app.config.ts`)
- Version code: auto-incremented by EAS (the `play-internal` profile
  has `autoIncrement: true`)
- Target API URL: `https://api.dragonfly-app.net` (the dev API; no
  staging API exists yet, per the brief)

### Final build commands

The strict, gated build procedure that the W1 pilot uses. This is
what closes Gate 1 of
[`one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md);
the casual walkthrough above is for first-time familiarisation.

Working directory: `mobile/`.

1. **Install dependencies.** The repo's package manager is npm
   (`mobile/package-lock.json` is the lockfile). Prefer `npm ci` for
   reproducibility; fall back to `npm install` only if you
   intentionally changed `package.json` in the same PR.

   ```sh
   cd mobile
   npm ci
   ```

2. **Type-check.** Local fast-fail gate; EAS will re-typecheck on
   the remote builder, but failing early saves a build minute.

   ```sh
   npx tsc --noEmit
   ```

   Must exit 0 before you spend an EAS build minute.

3. **Inspect the resolved Expo config** and verify the
   `play-internal` branch of `app.config.ts` resolved correctly:

   ```sh
   APP_ENV=play-internal npx expo config --type public
   ```

   Confirm in the printed JSON (key locations may vary by Expo CLI
   version — cross-check against `mobile/app.config.ts` if a key is
   not at the expected path):

   - [ ] `android.package` is `com.dragonfly.app` (NOT
     `com.dragonfly.app.dev` or `com.dragonfly.app.staging`).
   - [ ] `name` is `Hinterland Internal` (the `play-internal` branch
     of `displayName()` in `mobile/app.config.ts`).
   - [ ] `version` is `0.1.0` (the `version` field in
     `mobile/app.config.ts`; `android.versionCode` is NOT pinned in
     source — EAS manages it remotely via `appVersionSource:
     "remote"` + `autoIncrement: true`).
   - [ ] `extra.appEnv` is `play-internal`.
   - [ ] `extra.apiBaseUrl` is `https://api.dragonfly-app.net`.
   - [ ] `extra.updatesChannel` is `play-internal`.
   - [ ] `extra.firebase` and `extra.entra` are populated.

   If `android.package` is anything other than `com.dragonfly.app`:
   STOP. You are in the wrong `APP_ENV`. Re-run with
   `APP_ENV=play-internal` prefixed.

4. **Build the AAB with EAS.** Exact command from the
   `play-internal` profile in `eas.json` (`channel: play-internal`,
   `autoIncrement: true`, `android.buildType: app-bundle`,
   `env.APP_ENV: play-internal`):

   ```sh
   APP_ENV=play-internal npx eas-cli build \
     --platform android \
     --profile play-internal \
     --non-interactive
   ```

   When the build finishes, EAS prints a download URL for the
   `.aab` and the resolved `versionCode` in the build summary. Save
   the URL locally; do NOT commit the `.aab`.

5. **Upload to Play Console.** Manual upload to the Internal testing
   track — follow §3 below. Do NOT click **Send 1 update for
   review**; the saved (un-rolled-out) release is installable by the
   listed testers and defers policy review.

### Expected values

- [ ] Package name: `com.dragonfly.app`
- [ ] Display name on the device: `Hinterland Internal`
- [ ] App version: `0.1.0`
- [ ] Track: Internal testing (NOT Closed, NOT Open, NOT Production)
- [ ] Tester type: known adult-supervised testers only (1–3
  households). No classroom rollout. No public release.

### Stop conditions during build

- If `npx tsc --noEmit` fails: fix locally before building the AAB.
  Do not ship an AAB whose typecheck did not pass.
- If `npx expo config --type public` shows `android.package` as
  anything other than `com.dragonfly.app`: STOP. You are in the
  wrong `APP_ENV`. Re-run with `APP_ENV=play-internal` prefixed.
- If the EAS build's `versionCode` is not monotonically greater
  than the last AAB uploaded to Play Console on the `play-internal`
  track: STOP. Play Console rejects non-monotonic `versionCode` and
  a bad upload can invalidate later uploads on the same track. (On
  the very first `play-internal` upload, any value is acceptable.)
- If risk
  [`risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)
  is still Open and no option (A / B / C / D) has been picked: the
  default-if-no-choice is **Option C** (adult-supervised,
  known-family, internal-test only with explicit consent + Brian's
  manual review of every captured location pin). Acknowledge the
  choice in the risk doc AND record the option label verbatim in
  the session journal per
  [`one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
  Gate 1 before uploading the AAB — silent Option C selection is
  explicitly called out as a hazard in risk 0007.

## 3. Upload the AAB to Internal testing

1. Play Console → your Hinterland app → **Testing → Internal testing**
2. **Create new release**
3. Upload the `.aab` you downloaded from EAS
4. Release name: leave the default (Play Console fills it from
   `versionName + versionCode`)
5. Release notes: a one-line description of the pilot (e.g. "Internal
   pilot build, kid-supervised testing only")
6. **Next** → review the release → **Save**
7. Do NOT click **Send 1 update for review** yet -- testers can install
   from the saved (un-rolled-out) release; rollout is what triggers
   policy review which we want to defer until Closed testing.

## 4. Create the tester email list

1. Play Console → your Hinterland app → **Testing → Internal testing
   → Testers** tab
2. **Create email list**
3. Name: `Hinterland kid pilot W1`
4. Add tester email addresses (NOT committed to the repo; keep these
   in a private spreadsheet or password manager). Internal testing
   supports up to 100 testers.
5. Save.
6. Back on the **Testers** tab, toggle on the new list.

## 5. Roll out + share the opt-in link

1. Back on the **Releases** tab, click **Review release**.
2. **Start rollout to Internal testing**. This is the rollout that
   makes the build downloadable BY YOUR LISTED TESTERS ONLY -- it does
   NOT go to the public Play Store.
3. Copy the **opt-in URL** that Play Console prints (something like
   `https://play.google.com/apps/internaltest/4701234567890123456`).
4. Email the URL to each tester from a recognizable address (your
   personal Gmail is fine for a 1-3 family pilot). Body must include:
   - "Tap the link from the SAME Google account email I used to add you"
   - A 1-2 sentence description of what the app is
   - "This is a private test -- please do not share the link"
   - A note that the install shows up as **Hinterland Internal** on
     their phone, not "Hinterland"
5. Each tester taps the link → "Become a tester" → goes to the Play
   Store listing for `Hinterland Internal` → **Install**. From then on
   updates auto-flow when you push a new build.

## 6. Adult-supervised kid-test rules

This pilot is **adult-supervised, known-family kid testing only**. The
following rules apply on every test session and override anything the
app's onboarding suggests:

1. **An adult is in the room** for the full session. No "leave the kid
   with the phone."
2. **The adult creates the kid account** via the in-app parent flow.
   No kid types email or password.
3. **Photo capture happens outdoors with the adult present.** Indoor
   capture sessions are fine; the adult signs off on every photo
   before submission.
4. **Real location is collected** by the current build (see risk 0007).
   The pilot operator (Brian) is responsible for picking and applying
   one of the four mitigation options in risk 0007 BEFORE the AAB is
   built -- if option A (disable precise location) is selected, the
   AAB rebuilds with the location plugin removed.
5. **Each pilot family has signed parental consent** captured via the
   in-app `/consent` page (the public unauthenticated endpoint). Brian
   confirms the consent log row exists before the kid account is
   provisioned.
6. **No iNaturalist submission unless the operator has explicitly
   enabled it** for the pilot. The current build defaults to the iNat
   noop path; flipping `DRAGONFLY_INAT_OAUTH_TOKEN` on the Container
   App is what enables submissions. Leave it unset for the W1 pilot.
7. **No screenshots are shared outside the pilot family + Brian.** If
   Brian needs a screenshot for a bug report, the photo content and
   any kid display name are blurred before the screenshot leaves the
   device.
8. **Account deletion is one-tap and confirmed in-session.** Before
   the pilot family leaves the first session, Brian shows the parent
   the in-app account-deletion path.

## 7. What this PR is NOT

Out of scope for the pilot (and out of scope for this PR):
- **Closed testing track** -- needs Data Safety, content rating,
  Designed-for-Families decision, target audience confirmation. None
  of those are filled out yet.
- **Open / Production track** -- additionally needs the legal review
  per [`risks/0005-beta-launch-human-action-items.md`](risks/0005-beta-launch-human-action-items.md).
- **iOS / TestFlight** -- not addressed in this pilot. iOS path lands
  separately.
- **Play Developer API automation** -- the AAB upload above is manual.
  Automation is a post-pilot exercise.
- **Production release of `com.dragonfly.app`** -- the first AAB on
  the Internal testing track locks the package name but does not
  trigger production rollout. The production rollout is its own
  multi-day Play Console process.

## Recovery: if you uploaded the wrong package name

If you uploaded an AAB with `com.dragonfly.app.dev` (or any other
non-final package name) to the Play Console entry you intend to use
for production:

1. **Stop** -- do not upload more artifacts.
2. Play Console → that app entry → **Setup → Advanced settings →
   App availability** → **Remove app**.
3. There is a 30-day cooldown before the package name can be reused.
4. Create a NEW Play Console entry after the cooldown expires, using
   a clean `com.dragonfly.app` AAB.

A development-named AAB on a development-named Play Console entry is
fine, just keep them separate from the production-intended entry.
