import Constants from "expo-constants";

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

type Extra = {
  appEnv: "development" | "preview" | "production";
  apiBaseUrl: string;
  updatesChannel: string;
  firebase: FirebaseConfig;
  entra: EntraConfig;
  /**
   * Shared key for POST /v1/auth/dev-login (silent dev auto-login).
   * app.config.ts bakes a value ONLY for development/preview builds;
   * play-internal/production store builds ALWAYS carry null.
   */
  devLoginKey: string | null;
};

const extra = Constants.expoConfig?.extra as Extra | undefined;

if (!extra?.apiBaseUrl) {
  throw new Error(
    "expo config `extra.apiBaseUrl` is missing. Check app.config.ts and APP_ENV.",
  );
}

if (!extra.firebase?.apiKey) {
  throw new Error(
    "expo config `extra.firebase` is missing. Check app.config.ts and APP_ENV.",
  );
}

if (!extra.entra?.clientId) {
  throw new Error(
    "expo config `extra.entra` is missing. Check app.config.ts and APP_ENV.",
  );
}

export const env: Extra = {
  appEnv: extra.appEnv,
  apiBaseUrl: extra.apiBaseUrl,
  updatesChannel: extra.updatesChannel,
  firebase: extra.firebase,
  entra: extra.entra,
  // Normalize to string | null. Older configs may omit the field, and
  // Expo's public-config serialization turns a literal `null` in extra
  // into `{}` -- anything that is not a non-empty string means "no key".
  devLoginKey:
    typeof extra.devLoginKey === "string" && extra.devLoginKey.length > 0
      ? extra.devLoginKey
      : null,
};
