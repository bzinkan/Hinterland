# Risk 0007: Google Play Families policy + precise-location permission

- **Status:** Open
- **Date filed:** 2026-06-03
- **Source:** Pre-pilot review for the Google Play Internal Testing
  kid pilot (see [`docs/google-play-internal-testing.md`](../google-play-internal-testing.md)
  and [`docs/one-week-kid-pilot-checklist.md`](../one-week-kid-pilot-checklist.md))
- **Owner:** Brian (operator decision; agent cannot decide whether to
  remove a permission without explicit signoff)

## What we have

The current Dragonfly Android build requests:

- `android.permission.CAMERA` (via the `expo-camera` plugin in
  `mobile/app.config.ts`)
- Implicit photo-library access via `expo-image-picker` (no explicit
  Android permission since Android 13; uses the system photo picker)
- `android.permission.ACCESS_FINE_LOCATION` via the `expo-location`
  plugin with `locationAlwaysAndWhenInUsePermission`. This is
  **precise location** -- both in foreground and (depending on
  runtime grant) in background.

Observations are submitted with `latitude` and `longitude` floats
straight from the device GPS fix. The backend stores the precise
coordinates on the `observations` row; only a coarse 4-character
geohash (~30 km cell) is shared outside the kid's group per the
privacy DRAFT.

## Why this is a risk for the pilot

Google Play has tightened the rules on apps that target or include
children since 2024:

- The **Designed for Families** program (which kids-app submissions
  need to opt into for the kids-app review benefits) has stricter
  rules on precise-location collection from child accounts than the
  general policy.
- Even on **Internal testing**, the manifest declares the permission;
  if the build path ever leaks to Closed / Open / Production review,
  Play reviewers will scrutinize the precise-location use against
  the Families policy.
- Specifically, Play wants either:
  (a) precise location to NOT be requested from child users, or
  (b) a clear "functional necessity + transparent disclosure +
       in-app justification" rationale that the reviewer can verify,
       AND a parent-or-guardian opt-in surface for child accounts.
- The current build does the former for kid accounts via the
  parent-provisioning flow (kids never see the location prompt; the
  parent-created kid account inherits the kid-role context).
  BUT the parent flow itself requests fine location for its own
  observations, AND the kid flow does technically read the device
  GPS when the kid submits.

This is recoverable for Internal testing because reviewers see the
manifest but don't pull-test the runtime UX. It is NOT recoverable
for a Closed testing submission without changes.

## Pilot-window mitigation options

Pick ONE before the W1 pilot AAB is built. Options are listed in
"most code change" to "least code change" order so the operational
trade-offs are visible.

### Option A. Disable precise location in the child build

Remove the `expo-location` plugin entirely from the `play-internal`
APP_ENV path. Captured observations fall back to a manual
location-picker UI (drop-pin on a map, or pick "approximate" from a
city-level list). Backend continues to accept lat/lng floats; the
client just doesn't read them from GPS.

- **Code change:** medium. Requires a config-time plugin gate and a
  client-side fallback UI that's currently a stub. Probably 1-2
  engineering days.
- **Pilot UX:** noticeably worse. The kid has to manually identify
  where they're standing.
- **Compliance:** strongest. The manifest no longer declares
  fine-location for the child build path. Clean answer for any
  Play reviewer scenario.
- **When to pick:** if the pilot scope is likely to expand into
  Closed testing within the next 30 days, and the UX hit is
  acceptable.

### Option B. Use approximate / manual location for the pilot

Keep the `expo-location` plugin but request only
`ACCESS_COARSE_LOCATION` (city-block precision, ~1 km accuracy)
during the pilot. Observations get coarse location; the rarity /
neighborhood-comparison logic the backend does on geohash4 still
works because geohash4 is already ~30 km.

- **Code change:** small. Tweak the `expo-location` plugin config to
  request coarse-only on the `play-internal` APP_ENV. The Android
  manifest entry changes; the JS API call stays the same. Probably
  half a day of work plus an EAS rebuild.
- **Pilot UX:** mostly unchanged for kids. The location dot on a map
  is less precise but the observation still submits cleanly.
- **Compliance:** good. `ACCESS_COARSE_LOCATION` is much less
  scrutinized than `ACCESS_FINE_LOCATION` for child accounts.
- **When to pick:** if the pilot wants location semantics but doesn't
  need GPS precision. **This is the recommended technical path** if
  the operator can spare half a day of work before the AAB build.

### Option C. Adult-supervised + known-family + explicit consent

Leave the current precise-location request in place. Mitigate via
the pilot's operational shape:
- Internal testing only, never promoted to Closed testing or
  Production.
- Adult is present in the room for every kid session
  (see [`docs/one-week-kid-pilot-checklist.md`](../one-week-kid-pilot-checklist.md)
  rule 1 in the kid-test rules).
- Each pilot family has signed parental consent captured via the
  `/consent` page before the kid account is provisioned.
- Brian reviews every captured location pin in Cloud Logging /
  Application Insights before promoting the build past Internal
  testing.

- **Code change:** none.
- **Pilot UX:** unchanged (uses the precise-location flow already
  shipped).
- **Compliance:** weakest of the three "actually run the pilot"
  options. The manifest declares precise location for child
  accounts; if Brian ever promotes a build past Internal testing
  without changing the manifest, the Families review will surface
  this and likely reject.
- **When to pick:** if the pilot is genuinely one-shot Internal
  testing and won't be promoted to Closed testing for at least 30
  days, AND the operator commits to swapping to Option A or B
  before the first Closed testing rollout. **This is the default**
  if no other option is chosen by the time the AAB needs to be
  built.

### Option D. Delay the kid testing

Push the pilot back until Option A or B is implemented. Use the
freed time to do the rest of the
[`risks/0005-beta-launch-human-action-items.md`](0005-beta-launch-human-action-items.md)
human work in parallel (legal review, support email setup, etc.).

- **Code change:** none until the eventual A or B work.
- **Pilot UX:** N/A (no pilot).
- **Compliance:** strongest.
- **When to pick:** if the 7-day window is more of a "this would be
  nice" than a firm deadline, and the operator would rather invest
  in the Option A or B implementation first.

## Recommendation

**Option B (coarse location) is the recommended technical path** if the
operator can spare half a day before the AAB build. It's the smallest
code change that meaningfully reduces the precise-location compliance
risk without taking the pilot UX from "kid taps a photo, location is
remembered" down to "kid taps a photo, then manually pins their
location on a map."

If the operator cannot spare any code change in the W1 window,
**Option C** is the next-safest choice WITH the explicit commitment
that the Internal testing build will NOT be promoted to Closed
testing without first implementing Option A or B.

Option A is technically the best, but the half-day cost of Option B
buys most of the same compliance benefit at much lower UX cost. Save
Option A for a "we now have a real product team" moment.

Option D (delay) is the safest from a compliance standpoint but
contradicts the brief's "7-day pilot" goal -- only pick it if the
deadline is soft.

## Tradeoffs the operator should NOT silently make

Per the brief: do not silently remove functionality without
documenting the tradeoff. Specifically:

- Do NOT silently downgrade the `expo-location` plugin to
  `ACCESS_COARSE_LOCATION` without filing this risk doc as RESOLVED
  with the choice noted.
- Do NOT silently disable the `expo-location` plugin and ship a
  build where observations have a missing lat/lng without updating
  the kid-facing copy and the dispatcher behavior (it currently
  computes rarity on geohash4 -- if lat/lng is missing, every
  observation looks like "first find in this region" by accident).
- Do NOT submit an AAB to Play Closed testing while this risk is
  Open. The pilot is Internal testing only by design.

## Closing this risk

This risk closes when ALL of the following are true:

1. The operator has picked Option A, B, C, or D and committed the
   choice (either as a code change for A/B or as a documented
   policy stance for C/D).
2. If A or B: the chosen option is implemented in the codebase and
   the AAB that ships to Play has the corresponding manifest shape.
3. If C: this risk doc is updated with the explicit commitment
   that any future promotion past Internal testing requires
   re-opening this risk and picking A or B first.
4. The Play Console Internal testing AAB has been smoke-installed
   on an Android device and the operator has verified that the
   location permission prompt (or lack thereof) matches the chosen
   option.
