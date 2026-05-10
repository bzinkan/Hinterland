# Dragonfly mobile

Expo (React Native) app for Dragonfly. iOS, Android, and a thin web build for the
parent-consent / teacher dashboard surface (per `docs/mobile.md`).

Phase 6 surface: Home tab lists the signed-in user's observations
(`GET /v1/observations/me`), Observe tab is the camera capture + submit flow,
Dex / Expeditions remain placeholders. Settings holds the dev "paste a Firebase
ID token" auth shortcut + build info. Real auth (Firebase Web SDK) replaces
the paste field in a later slice.

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
    (tabs)/              # bottom tab nav: Home, Observe, Dex, Expeditions, Settings
    observe-submit.tsx   # stack-pushed submit screen after camera capture
  src/
    api/                 # client + typed endpoint wrappers + queryClient
    auth/token.ts        # bearer token in expo-secure-store
    config/env.ts        # typed read of expoConfig.extra
    observation/         # draftStore (Zustand) + useMyObservations (TanStack Query)
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
exist (per `infra-gcp/README.md`).

## What's NOT in here yet

Per `docs/mobile.md` the full stack also includes Nativewind, Sentry,
expo-sqlite (offline queue), the celebration sequence, and EAS Update wiring.
Each lands with the phase that needs it — see `AGENTS.md` Phases 7–11.
