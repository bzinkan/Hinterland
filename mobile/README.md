# Hinterland mobile

Expo (React Native) app for Hinterland (formerly Dragonfly). iOS, Android, and a thin web build for the
parent-consent / teacher dashboard surface (per `docs/mobile.md`).

Phase 6+ surface: Field Journal tab (route `index`) opens to saved photos
from `GET /v1/observations/me` and includes a Species segment backed by
verified Dex entries from `GET /v1/dex/me`. Manual typed names and unknown
observations stay in Photos only. The Field Journal is read-only:
kid-authored journal notes would need an ADR first (moderation /
no-kid-free-text invariants). Observe tab is the camera capture + submit
flow, Expeditions is the real expedition surface, and Sanctuary tab renders
the kid's living-diorama from `GET /v1/sanctuary/me` (MVP with placeholder
art -- see `docs/sanctuary.md` section 10). The Sanctuary tab also carries
a small date-driven seasonal banner (server-selected from the current UTC
date, Northern-Hemisphere calendar -- limitation documented in
`docs/sanctuary.md`) and a text-only "Sounds" placeholder panel that lists
future ambient sounds without playing audio, requesting microphone
permission, or adding analytics. Settings holds the dev bearer-token
shortcut + build info + the Adult tools section (review queue link). The
old Dex tab route redirects back to Field Journal for compatibility.

**Web build is the adult-console surface only** (per `docs/mobile.md`):
`npm run web` shows the Field Journal + Settings only -- Observe / Expeditions /
Sanctuary are hidden from the nav since those are kid-phone surfaces. The review queue is
reachable from Settings the same way on both platforms. The kid capture flow
isn't there because: (a) docs/mobile.md is explicit that web isn't the kid
experience, and (b) outdoor-with-a-laptop isn't the kid use case. Classroom
Chromebooks are a real future case but they're not Phase 1.

## Quick start

```powershell
cd mobile
npm install
npm run start         # opens the Expo dev server; scan QR with Expo Go
npm run web           # browser preview at http://localhost:8081
```

Defaults to `APP_ENV=development`, which points the API base URL at
`https://api.thehinterlandguide.app`. Other envs:

```powershell
$env:APP_ENV="preview";    npm run start
$env:APP_ENV="production"; npm run start
```

The active env, API URL, and update channel are visible on the Settings tab.

## Layout

```
mobile/
  app.config.ts          # env-switched Expo config (replaces app.json)
  eas.json               # development / preview / production build profiles
  app/                   # Expo Router file-based routes
    (tabs)/              # bottom tab nav: Journal, Observe, Expeditions, Sanctuary, Settings
    observe-submit.tsx   # stack-pushed submit screen after camera capture
  src/
    api/                 # client + typed endpoint wrappers + queryClient
    auth/token.ts        # bearer token in expo-secure-store
    config/env.ts        # typed read of expoConfig.extra
    observation/         # draftStore (Zustand) + useMyObservations (TanStack Query)
    sanctuary/           # useSanctuary (TanStack Query) over GET /v1/sanctuary/me
  components/            # shared UI bits
```

`app/` is the Expo Router root — anything in there becomes a route. `src/`
holds non-route code (API clients, state stores, hooks).

## Observation durability and release gate

Observation submissions use an owner-scoped SQLite record plus a canonical
JPEG in the app document directory. The same submission ULID is used for
presign and create, and the queue resumes from its exact durable stage after
network loss or process death. A completed row retains the canonical server
response and hash metadata, but its local JPEG is deleted. The device-wide
queue limit is 50 entries.

Run the deterministic mobile checks with:

```powershell
npm run typecheck
npm test -- --runInBand
npm run config:play-internal
```

The committed `.maestro/` workspace drives the complete synthetic-photo,
no-location, Unknown, upload, persisted completion, and Field Journal return
flow by stable React Native `testID`. It deliberately uses `clearState: false`:
the exact store build must already be authenticated as the isolated W1 kid.

Physical execution is a remaining promotion gate, not a result produced by
the repository checks. At implementation time no exact Play Internal artifact,
Maestro CLI, or physical device was attached, so no device pass is claimed.
Run it as follows:

1. Use a physical Android device with no more than 4 GB RAM, enable USB
   debugging, and install **Hinterland Internal from the Google Play Internal
   testing opt-in link**. Do not substitute a local APK or Expo export; Play's
   split install must come from the exact AAB being promoted.
2. Complete the adult-managed kid handoff, leave that W1 kid signed in, and
   clear or reconcile any prior local Observation queue entries. The committed
   flow adds `assets/images/icon.png` to the device gallery as synthetic media.
3. From `mobile/`, record the installed version and run the workspace with
   Maestro (Java 17 or 21):

   ```powershell
   adb devices
   $serial = "REPLACE_WITH_ADB_SERIAL"
   adb -s $serial shell dumpsys package com.dragonfly.app |
     Select-String "versionCode|versionName"
   maestro --device=$serial test .\.maestro
   ```

4. Attach the Maestro artifacts, device model/RAM/Android version, ADB serial,
   Play version code, EAS build ID, and AAB SHA-256 to the promotion record.
   A green run against any other build or an emulator does not satisfy this
   gate.

The automated flow complements, but does not replace, these supervised fault
scenarios on the same exact AAB and device:

- Capture in airplane mode, kill the app, relaunch, reconnect, and confirm one observation.
- Kill after the Azure PUT, relaunch, and confirm create replays without another photo or reward set.
- Destroy the picker activity, relaunch, and confirm the pending library result returns only to its initiating account.
- Switch kid accounts during location, upload, identification, review, and delete requests; confirm no prior-account photo, draft, result, or alert appears.
- Choose **No location** while location/reverse-geocode work is pending and confirm no coarse area later reappears.
- Inspect pending, pilot-private, quarantined, rejected, and completed queue states and confirm child surfaces never render local or signed private bytes.

## Environment switching

`APP_ENV` is read at build/start time by `app.config.ts` and baked into
`Constants.expoConfig.extra`. `src/config/env.ts` is the single typed read site
— do not call `Constants.expoConfig.extra` from screens directly.

| APP_ENV       | API base URL                          | Bundle ID                  | Update channel |
| ------------- | ------------------------------------- | -------------------------- | -------------- |
| `development` | `https://api.thehinterlandguide.app`  | `com.dragonfly.app.dev`    | `development`  |
| `preview`     | `https://api.thehinterlandguide.app`  | `app.thehinterlandguide.staging` | `preview`      |
| `production`  | `https://api.thehinterlandguide.app`  | `com.dragonfly.app`        | `production`   |

All current build profiles point at the development Azure API until separate
staging/production Container Apps exist.

## What's NOT in here yet

Per `docs/mobile.md` the full stack also includes Nativewind, Sentry, and EAS
Update wiring. Each lands with the phase that needs it — see `AGENTS.md`.
