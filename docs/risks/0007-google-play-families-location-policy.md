# Risk 0007: Google Play Families policy + precise-location permission

- **Status:** Mitigated for Play Internal; keep open until device verification
- **Date filed:** 2026-06-03
- **Updated:** 2026-07-09 for ADR 0015
- **Chosen option:** Option B, coarse/foreground location for `play-internal`
- **Owner:** Brian

## Decision

The `play-internal` Android build must not request
`android.permission.ACCESS_FINE_LOCATION`. It explicitly requests
`android.permission.ACCESS_COARSE_LOCATION` and blocks fine location in
`mobile/app.config.ts`.

Location is optional. When accepted, mobile computes `geohash4` locally from
an approximate fix and discards raw coordinates before SQLite or network
writes. Denial/disabled services save with no location. Imported library photos
never silently inherit current device location.

The server persists only optional `geohash4` plus
`device_coarse|manual_coarse|none`. One compatibility release may accept legacy
coordinates, but converts them in memory and never persists or logs them.
Radius-based Expedition rules decline when only coarse/no location is present.

## Code State

- `APP_ENV=play-internal` uses package `app.thehinterlandguide` and display name
  `The Hinterland Guide Internal`.
- `android.blockedPermissions` includes
  `android.permission.ACCESS_FINE_LOCATION`, microphone, overlay, and legacy
  broad-storage permissions for both store profiles. W1 additionally removes
  foreground media-service and audio-settings permissions while Sanctuary
  audio is disabled.
- `android.permissions` includes
  `android.permission.ACCESS_COARSE_LOCATION`.
- CI runs `APP_ENV=play-internal npm run config:play-internal` to verify the
  public Expo config.

## Update 2026-07-02: passive coarse use on the expeditions tab

The expeditions tab now passively uses already-granted coarse foreground
location to rank suggested expeditions ("relevance"). Scope of the change:

- **No new permission requests.** The tab only CHECKS the foreground
  permission (`getForegroundPermissionsAsync`); the observe-submit screen
  remains the ONLY place that requests location. Denied/undetermined means
  the tab behaves exactly as before.
- **On-device geohash only.** The device encodes lat/lng to a 4-character
  geohash cell (a grid square roughly 20 by 40 km — coarser than
  Android's approximate location)
  and sends ONLY that cell as the `geohash4` query param on
  `GET /v1/expeditions/available`. Raw coordinates never leave the device
  for this feature.
- **Purpose:** ranking suggested activities — app functionality.
- **No background use.** Foreground tab load only.
- **No third-party sharing.**
- **No server-side storage keyed to the child** beyond the transient query
  param and a coarse request-log field. Note the raw query string (the
  4-char cell, including invalid junk a client might send) also appears in
  standard HTTP access logs in Log Analytics — same coarseness, disclosed
  here for completeness.
- **Data Safety form:** the approximate-location purpose list must include
  App functionality / personalization before any track that requires the
  Data Safety declaration (see
  [`../app-store-compliance-checklist.md`](../app-store-compliance-checklist.md)).

Ship the disclosure updates in this file and the compliance checklist
BEFORE any play-internal build that includes this feature.

## Remaining Verification

- [ ] Run an EAS `play-internal` AAB build.
- [ ] Inspect/confirm the generated Android manifest has no fine-location
      permission.
- [ ] Install on a physical Android device from Internal Testing.
- [ ] Confirm the runtime location prompt matches coarse/approximate behavior.
- [ ] Deny location and confirm the Observation still saves.
- [ ] Inspect PostgreSQL and Log Analytics for no raw coordinates or
      coordinate-bearing URLs.
- [ ] Import a library image and confirm current device location is not applied
      without a separate explicit choice.
- [ ] Record the chosen option and device result in the private pilot journal.

## Rule

Do not promote a build past Internal Testing until this verification is complete
and the current Google Play review requirements are rechecked against official
store docs.
