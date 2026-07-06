/**
 * Full decision matrix for the silent dev auto-login gate.
 *
 * shouldAttemptDevLogin is deliberately a pure function in its own module,
 * so every appEnv x token-present x key-present combination is testable
 * with zero mocking (no network, no SecureStore, no expo-constants).
 */
import { shouldAttemptDevLogin } from "../devLoginPolicy";

const APP_ENVS = ["development", "preview", "production", "play-internal"] as const;
const PRE_PRODUCTION = new Set<string>(["development", "preview"]);
const KEYS: readonly (string | null | undefined)[] = [
  null,
  undefined,
  "",
  "shared-dev-key",
];

describe("shouldAttemptDevLogin", () => {
  for (const appEnv of APP_ENVS) {
    for (const hasToken of [true, false]) {
      for (const key of KEYS) {
        const expected =
          PRE_PRODUCTION.has(appEnv) && !hasToken && key === "shared-dev-key";
        const label = `appEnv=${appEnv} hasToken=${hasToken} key=${JSON.stringify(
          key,
        )} -> ${expected}`;
        it(label, () => {
          expect(shouldAttemptDevLogin(appEnv, hasToken, key)).toBe(expected);
        });
      }
    }
  }

  it("store envs never attempt, even with a key present and no token", () => {
    expect(shouldAttemptDevLogin("production", false, "shared-dev-key")).toBe(false);
    expect(shouldAttemptDevLogin("play-internal", false, "shared-dev-key")).toBe(false);
  });

  it("an existing token always wins in pre-production envs", () => {
    expect(shouldAttemptDevLogin("development", true, "shared-dev-key")).toBe(false);
    expect(shouldAttemptDevLogin("preview", true, "shared-dev-key")).toBe(false);
  });

  it("a missing or empty key never attempts, even in development", () => {
    expect(shouldAttemptDevLogin("development", false, null)).toBe(false);
    expect(shouldAttemptDevLogin("development", false, undefined)).toBe(false);
    expect(shouldAttemptDevLogin("development", false, "")).toBe(false);
  });

  it("unknown appEnv values fail closed", () => {
    expect(shouldAttemptDevLogin("staging", false, "shared-dev-key")).toBe(false);
    expect(shouldAttemptDevLogin("", false, "shared-dev-key")).toBe(false);
  });
});
