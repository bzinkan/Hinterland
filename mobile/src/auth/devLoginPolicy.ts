/**
 * Pure gate matrix for the silent dev auto-login (no imports on purpose,
 * so jest can exercise every combination without mocking network, storage,
 * or expo-constants).
 *
 * Attempt dev login ONLY when all three hold:
 *   1. the build is a pre-production one (appEnv development or preview --
 *      play-internal and production store builds never attempt, even if a
 *      key were somehow present);
 *   2. no bearer token is already stored (a real kid session, a pasted dev
 *      token, or a previous dev login always wins);
 *   3. a non-empty dev-login key was baked into the build config.
 */
export function shouldAttemptDevLogin(
  appEnv: string,
  hasToken: boolean,
  key: string | null | undefined,
): boolean {
  if (appEnv !== "development" && appEnv !== "preview") {
    return false;
  }
  if (hasToken) {
    return false;
  }
  return typeof key === "string" && key.length > 0;
}
