import type { ExpoConfig } from "expo/config";

type AppEnv = "development" | "preview" | "production";

const APP_ENV: AppEnv =
  (process.env.APP_ENV as AppEnv | undefined) ?? "development";

type EnvConfig = {
  apiBaseUrl: string;
  bundleIdSuffix: string;
  updatesChannel: string;
};

const ENV: Record<AppEnv, EnvConfig> = {
  development: {
    apiBaseUrl: "https://api.dragonfly-app.net",
    bundleIdSuffix: ".dev",
    updatesChannel: "development",
  },
  preview: {
    apiBaseUrl: "https://api.staging.dragonfly-app.net",
    bundleIdSuffix: ".staging",
    updatesChannel: "preview",
  },
  production: {
    apiBaseUrl: "https://api.dragonfly-app.net",
    bundleIdSuffix: "",
    updatesChannel: "production",
  },
};

const env = ENV[APP_ENV];

const config: ExpoConfig = {
  name: APP_ENV === "production" ? "Dragonfly" : `Dragonfly (${APP_ENV})`,
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
          "Dragonfly uses your camera to take photos of plants and animals you find.",
        recordAudioAndroid: false,
      },
    ],
    [
      "expo-image-picker",
      {
        photosPermission:
          "Dragonfly uses your photo library so you can pick a photo of a plant or animal you found.",
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
  },
};

export default config;
