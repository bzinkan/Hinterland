/**
 * Jest global setup. src/config/env.ts validates `Constants.expoConfig.extra`
 * at import time (and throws when absent), so any test that transitively
 * touches the API layer needs a populated expo-constants mock. Values are
 * inert placeholders -- no test talks to a real backend.
 */

jest.mock("expo-constants", () => ({
  __esModule: true,
  default: {
    expoConfig: {
      extra: {
        appEnv: "development",
        apiBaseUrl: "http://jest.invalid",
        updatesChannel: "test",
        firebase: { apiKey: "test", authDomain: "test", projectId: "test" },
        entra: { clientId: "test", authority: "test", redirectUri: "test" },
        devLoginKey: null,
      },
    },
  },
}));
