import Constants from "expo-constants";

type EntraConfig = {
  clientId: string;
  authority: string;
  redirectUri: string;
  scope: string;
};

export type AppEnvironment =
  | "development"
  | "preview"
  | "production"
  | "play-internal"
  | "adult-qa";

export type ReleaseLane =
  | "local"
  | "w1_internal"
  | "adult_qa"
  | "production";

export type Extra = {
  appEnv: AppEnvironment;
  apiBaseUrl: string;
  parentWebBaseUrl: string;
  updatesChannel: string;
  expectedApiLane: ReleaseLane;
  enforceReleaseLane: boolean;
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

if (!extra.entra?.clientId) {
  throw new Error(
    "expo config `extra.entra` is missing. Check app.config.ts and APP_ENV.",
  );
}

if (!extra.entra.scope) {
  throw new Error(
    "expo config `extra.entra.scope` is missing. Check app.config.ts and APP_ENV.",
  );
}

if (!extra.parentWebBaseUrl) {
  throw new Error(
    "expo config `extra.parentWebBaseUrl` is missing. Check app.config.ts and APP_ENV.",
  );
}

if (!extra.expectedApiLane) {
  throw new Error(
    "expo config `extra.expectedApiLane` is missing. Check app.config.ts and APP_ENV.",
  );
}

export const env: Extra = {
  appEnv: extra.appEnv,
  apiBaseUrl: extra.apiBaseUrl,
  parentWebBaseUrl: extra.parentWebBaseUrl,
  updatesChannel: extra.updatesChannel,
  expectedApiLane: extra.expectedApiLane,
  enforceReleaseLane: extra.enforceReleaseLane === true,
  entra: extra.entra,
  // Normalize to string | null. Older configs may omit the field, and
  // Expo's public-config serialization turns a literal `null` in extra
  // into `{}` -- anything that is not a non-empty string means "no key".
  devLoginKey:
    typeof extra.devLoginKey === "string" && extra.devLoginKey.length > 0
      ? extra.devLoginKey
      : null,
};
