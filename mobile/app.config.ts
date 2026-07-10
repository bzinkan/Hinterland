import type { ExpoConfig } from "expo/config";

// `play-internal` is the Google Play Internal Testing track build path.
// Uses the FINAL package name `app.thehinterlandguide` (no `.dev` suffix) and
// surfaces as "The Hinterland Guide Internal" so testers can tell pilot installs
// apart from a future production install. See
// docs/google-play-internal-testing.md for the full process.
type AppEnv =
  | "development"
  | "preview"
  | "production"
  | "play-internal";

const APP_ENV: AppEnv =
  (process.env.APP_ENV as AppEnv | undefined) ?? "development";

// Existing EAS project owned by the `thehinterlandguides-team` Expo
// organization. Its dashboard display name is `hinterland`; the immutable
// project ID is what binds builds and credentials to the correct project.
const EAS_PROJECT_ID = "278f4a33-e1b1-4468-8d02-a51defe03267";

type EntraConfig = {
  clientId: string;
  authority: string;
  redirectUri: string;
};

type EnvConfig = {
  apiBaseUrl: string;
  androidPackage: string;
  iosBundleIdentifier: string;
  updatesChannel: string;
  entra: EntraConfig;
};

// Entra External Identities customer tenant (CIAM) from Phase 1.
// clientId is the `hinterland-client` public app registration; authority
// targets the `login.microsoftonline.com/{ciam-tenant-id}/v2.0` flow.
// Public values; access is gated by Entra + pre-authorized scope.
const ENTRA_DEV: EntraConfig = {
  clientId: "60504e4c-6b5f-4031-a80a-3e4bdfae29b2",
  authority:
    "https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404",
  redirectUri: "https://parents.thehinterlandguide.app/auth/callback",
};

const ENV: Record<AppEnv, EnvConfig> = {
  development: {
    apiBaseUrl: "https://api.thehinterlandguide.app",
    androidPackage: "app.thehinterlandguide.dev",
    iosBundleIdentifier: "app.thehinterlandguide.dev",
    updatesChannel: "development",
    entra: ENTRA_DEV,
  },
  preview: {
    apiBaseUrl: "https://api.thehinterlandguide.app",
    androidPackage: "app.thehinterlandguide.staging",
    iosBundleIdentifier: "app.thehinterlandguide.staging",
    updatesChannel: "preview",
    entra: ENTRA_DEV,
  },
  production: {
    apiBaseUrl: "https://api.thehinterlandguide.app",
    androidPackage: "app.thehinterlandguide",
    iosBundleIdentifier: "app.thehinterlandguide",
    updatesChannel: "production",
    entra: ENTRA_DEV,
  },
  // play-internal uses the FINAL package name `app.thehinterlandguide`
  // for the current pilot. First upload of this artifact LOCKS the
  // package name on the Play Console app -- see docs/google-play-
  // internal-testing.md for the warning + recovery path. Points at
  // the dev API since no staging API exists yet.
  "play-internal": {
    apiBaseUrl: "https://api.thehinterlandguide.app",
    androidPackage: "app.thehinterlandguide",
    iosBundleIdentifier: "app.thehinterlandguide",
    updatesChannel: "play-internal",
    entra: ENTRA_DEV,
  },
};

const env = ENV[APP_ENV];
const isPlayInternal = APP_ENV === "play-internal";
const devLoginKey = process.env.HINTERLAND_DEV_AUTH_TOKEN ?? null;

// Sanctuary 2.5D diorama build flag (ADR 0011/0012). Build-time default
// only -- runtime overrides (screen reader, Simple view, renderer crash
// latch) layer on top in src/config/featureFlags.ts. Stays "0" for
// play-internal/production until the post-pilot flag-flip milestone.
//
// Local `expo start` has no eas.json env, so the development environment
// defaults ON unless explicitly disabled with SANCTUARY_DIORAMA=0.
const SANCTUARY_DIORAMA =
  process.env.SANCTUARY_DIORAMA !== undefined
    ? process.env.SANCTUARY_DIORAMA === "1"
    : APP_ENV === "development";

function displayName(appEnv: AppEnv): string {
  switch (appEnv) {
    case "production":
      return "The Hinterland Guide";
    case "play-internal":
      return "The Hinterland Guide Internal";
    default:
      return `The Hinterland Guide ${appEnv}`;
  }
}

const config: ExpoConfig = {
  name: displayName(APP_ENV),
  slug: "hinterland",
  owner: "thehinterlandguides-team",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/images/icon.png",
  scheme: "hinterland",
  userInterfaceStyle: "automatic",
  newArchEnabled: true,
  splash: {
    image: "./assets/images/splash-icon.png",
    resizeMode: "contain",
    backgroundColor: "#ffffff",
  },
  ios: {
    bundleIdentifier: env.iosBundleIdentifier,
    supportsTablet: true,
  },
  android: {
    package: env.androidPackage,
    permissions: isPlayInternal
      ? ["android.permission.ACCESS_COARSE_LOCATION"]
      : undefined,
    blockedPermissions: isPlayInternal
      ? [
          "android.permission.ACCESS_FINE_LOCATION",
          // expo-audio is playback-only here (Sanctuary soundscapes,
          // ADR 0012); a kids app must never carry a mic permission.
          "android.permission.RECORD_AUDIO",
        ]
      : ["android.permission.RECORD_AUDIO"],
    adaptiveIcon: {
      foregroundImage: "./assets/images/adaptive-icon.png",
      backgroundColor: "#ffffff",
    },
    edgeToEdgeEnabled: true,
    predictiveBackGestureEnabled: false,
  },
  web: {
    bundler: "metro",
    output: "static",
    favicon: "./assets/images/favicon.png",
  },
  plugins: [
    "expo-router",
    "expo-secure-store",
    "expo-sqlite",
    "expo-font",
    "expo-asset",
    [
      "expo-camera",
      {
        cameraPermission:
          "Hinterland uses your camera to take photos of plants and animals you find.",
        recordAudioAndroid: false,
      },
    ],
    [
      "expo-image-picker",
      {
        photosPermission:
          "Hinterland uses your photo library so you can pick a photo of a plant or animal you found.",
      },
    ],
    [
      "expo-location",
      {
        locationAlwaysAndWhenInUsePermission:
          "Hinterland uses your location to remember where you spotted each species.",
      },
    ],
    [
      "expo-audio",
      {
        // Playback-only (Sanctuary soundscapes). No recording anywhere
        // in the app, so no microphone permission on either platform.
        microphonePermission: false,
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
  },
  extra: {
    eas: {
      projectId: EAS_PROJECT_ID,
    },
    appEnv: APP_ENV,
    apiBaseUrl: env.apiBaseUrl,
    updatesChannel: env.updatesChannel,
    entra: env.entra,
    // Shared key for the silent dev auto-login (POST /v1/auth/dev-login).
    // Baked in ONLY for development/preview builds, and only when the
    // builder exports HINTERLAND_DEV_AUTH_TOKEN. Store builds
    // (play-internal/production) ALWAYS get null -- enforced by
    // scripts/verify-play-internal-config.mjs in CI.
    devLoginKey:
      APP_ENV === "development" || APP_ENV === "preview"
        ? devLoginKey
        : null,
    sanctuaryDiorama: SANCTUARY_DIORAMA,
  },
};

export default config;
