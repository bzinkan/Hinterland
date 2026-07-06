import type { ExpoConfig } from "expo/config";

// `play-internal` is the Google Play Internal Testing track build path.
// Uses the FINAL package name `com.dragonfly.app` (no `.dev` suffix) and
// surfaces as "Hinterland Internal" so testers can tell pilot installs
// apart from a future production install. See
// docs/google-play-internal-testing.md for the full process.
type AppEnv =
  | "development"
  | "preview"
  | "production"
  | "play-internal";

const APP_ENV: AppEnv =
  (process.env.APP_ENV as AppEnv | undefined) ?? "development";

type FirebaseConfig = {
  apiKey: string;
  authDomain: string;
  projectId: string;
};

type EntraConfig = {
  clientId: string;
  authority: string;
  redirectUri: string;
};

type EnvConfig = {
  apiBaseUrl: string;
  bundleIdSuffix: string;
  updatesChannel: string;
  firebase: FirebaseConfig;
  entra: EntraConfig;
};

// Firebase Web API keys are public identifiers, not secrets -- access is
// gated by Firebase Auth + Security Rules. Safe to embed in client bundles.
const FIREBASE_DEV: FirebaseConfig = {
  apiKey: "AIzaSyAg2gIzrXoYbeLx5cKWB1QXCZiDWEF2Yh4",
  authDomain: "dragonflyapp-495423.firebaseapp.com",
  projectId: "dragonflyapp-495423",
};

// Entra External Identities customer tenant (CIAM) from Phase 1.
// clientId is the `dragonfly-client` public app registration; authority
// targets the `login.microsoftonline.com/{ciam-tenant-id}/v2.0` flow.
// Public values; access is gated by Entra + pre-authorized scope.
const ENTRA_DEV: EntraConfig = {
  clientId: "6d1b6e1f-42fa-4977-b67f-a15b1f84d4ff",
  authority:
    "https://login.microsoftonline.com/dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f",
  redirectUri: "https://parents.dragonfly-app.net/auth/callback",
};

const ENV: Record<AppEnv, EnvConfig> = {
  development: {
    apiBaseUrl: "https://api.dragonfly-app.net",
    bundleIdSuffix: ".dev",
    updatesChannel: "development",
    firebase: FIREBASE_DEV,
    entra: ENTRA_DEV,
  },
  preview: {
    apiBaseUrl: "https://api.staging.dragonfly-app.net",
    bundleIdSuffix: ".staging",
    updatesChannel: "preview",
    firebase: FIREBASE_DEV,
    entra: ENTRA_DEV,
  },
  production: {
    apiBaseUrl: "https://api.dragonfly-app.net",
    bundleIdSuffix: "",
    updatesChannel: "production",
    firebase: FIREBASE_DEV,
    entra: ENTRA_DEV,
  },
  // play-internal uses the FINAL package name `com.dragonfly.app`
  // (bundleIdSuffix=""). First upload of this artifact LOCKS the
  // package name on the Play Console app -- see docs/google-play-
  // internal-testing.md for the warning + recovery path. Points at
  // the dev API since no staging API exists yet.
  "play-internal": {
    apiBaseUrl: "https://api.dragonfly-app.net",
    bundleIdSuffix: "",
    updatesChannel: "play-internal",
    firebase: FIREBASE_DEV,
    entra: ENTRA_DEV,
  },
};

const env = ENV[APP_ENV];
const isPlayInternal = APP_ENV === "play-internal";

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
      return "Hinterland";
    case "play-internal":
      return "Hinterland Internal";
    default:
      return `Hinterland (${appEnv})`;
  }
}

const config: ExpoConfig = {
  name: displayName(APP_ENV),
  slug: "dragonfly",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/images/icon.png",
  scheme: "dragonfly",
  userInterfaceStyle: "automatic",
  newArchEnabled: true,
  splash: {
    image: "./assets/images/splash-icon.png",
    resizeMode: "contain",
    backgroundColor: "#ffffff",
  },
  ios: {
    bundleIdentifier: `com.dragonfly.app${env.bundleIdSuffix}`,
    supportsTablet: true,
  },
  android: {
    package: `com.dragonfly.app${env.bundleIdSuffix}`,
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
    appEnv: APP_ENV,
    apiBaseUrl: env.apiBaseUrl,
    updatesChannel: env.updatesChannel,
    firebase: env.firebase,
    entra: env.entra,
    // Shared key for the silent dev auto-login (POST /v1/auth/dev-login).
    // Baked in ONLY for development/preview builds, and only when the
    // builder exports DRAGONFLY_DEV_LOGIN_KEY. Store builds
    // (play-internal/production) ALWAYS get null -- enforced by
    // scripts/verify-play-internal-config.mjs in CI.
    devLoginKey:
      APP_ENV === "development" || APP_ENV === "preview"
        ? (process.env.DRAGONFLY_DEV_LOGIN_KEY ?? null)
        : null,
    sanctuaryDiorama: SANCTUARY_DIORAMA,
    // Links this project to the existing EAS project (account
    // dragonflybrian) -- same id the sanctuary-3d dev client used, so
    // cloud builds reuse the established keystore and build history.
    eas: {
      projectId: "7dab1a21-6bd3-489d-86b7-59cf336fdb67",
    },
  },
};

export default config;
