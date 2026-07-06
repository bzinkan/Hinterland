# Hinterland mobile

Expo (React Native) app for Hinterland (formerly Dragonfly). iOS, Android, and a thin web build for the
parent-consent / teacher dashboard surface (per `docs/mobile.md`).

Phase 6+ surface: Field Journal tab (route `index`) lists the signed-in
user's observations (`GET /v1/observations/me`). The Field Journal is
read-only: kid-authored journal notes would need an ADR first
(moderation / no-kid-free-text invariants). Observe tab is the camera
capture + submit flow,
Dex / Expeditions placeholder + real Expeditions tab, and Sanctuary tab
renders the kid's living-diorama from `GET /v1/sanctuary/me` (MVP with
placeholder art -- see `docs/sanctuary.md` section 10). The Sanctuary tab
also carries a small date-driven seasonal banner (server-selected from
the current UTC date, Northern-Hemisphere calendar -- limitation
documented in `docs/sanctuary.md`) and a text-only "Sounds" placeholder
panel that lists future ambient sounds without playing audio,
requesting microphone permission, or adding analytics. Settings holds
the dev bearer-token shortcut + build info + the
Adult tools section (review queue link).

**Web build is the adult-console surface only** (per `docs/mobile.md`):
`npm run web` shows the Field Journal + Settings only -- Observe / Dex / Expeditions /
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
`https://api.dragonfly-app.net`. Other envs:

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
    (tabs)/              # bottom tab nav: Journal, Observe, Dex, Expeditions, Sanctuary, Settings
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

## Environment switching

`APP_ENV` is read at build/start time by `app.config.ts` and baked into
`Constants.expoConfig.extra`. `src/config/env.ts` is the single typed read site
— do not call `Constants.expoConfig.extra` from screens directly.

| APP_ENV       | API base URL                          | Bundle ID                  | Update channel |
| ------------- | ------------------------------------- | -------------------------- | -------------- |
| `development` | `https://api.dragonfly-app.net`       | `com.dragonfly.app.dev`    | `development`  |
| `preview`     | `https://api.staging.dragonfly-app.net` (TBD) | `com.dragonfly.app.staging` | `preview`      |
| `production`  | `https://api.dragonfly-app.net` (TBD prod URL) | `com.dragonfly.app`        | `production`   |

Staging and production API URLs are placeholders until those environments
exist (per `infra-azure/README.md`).

## What's NOT in here yet

Per `docs/mobile.md` the full stack also includes Nativewind, Sentry,
expo-sqlite (offline queue), the celebration sequence, and EAS Update wiring.
Each lands with the phase that needs it — see `AGENTS.md` Phases 7–11.
