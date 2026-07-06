/**
 * Silent dev auto-login for pre-production builds.
 *
 * When a development/preview build boots with no stored bearer token and a
 * dev-login key baked into its config, it exchanges the key for a sandbox
 * kid session via POST /v1/auth/dev-login and stores the session token
 * exactly like the kid-exchange flow does.
 *
 * Store builds are untouched twice over: app.config.ts bakes
 * devLoginKey=null for play-internal/production, and shouldAttemptDevLogin
 * refuses any appEnv outside development/preview regardless of the key.
 * The backend adds its own fail-closed stack (default-off flag + shared
 * key + unconditional 404 on prod).
 *
 * Failures are swallowed on purpose -- the existing signed-out UX (dev
 * token paste in Settings, QR handoff) stays fully intact.
 */
import { devLogin } from "@/src/api/auth";
import { shouldAttemptDevLogin } from "@/src/auth/devLoginPolicy";
import { getBearerToken, setBearerToken } from "@/src/auth/token";
import { env } from "@/src/config/env";

export { shouldAttemptDevLogin } from "@/src/auth/devLoginPolicy";

/**
 * Fire-and-forget boot hook. No-ops unless the full gate matrix passes;
 * never throws.
 */
export async function ensureDevSession(): Promise<void> {
  try {
    const key = env.devLoginKey;
    const existing = await getBearerToken();
    if (key == null || !shouldAttemptDevLogin(env.appEnv, existing != null, key)) {
      return;
    }
    const response = await devLogin(key);
    await setBearerToken(response.session_token);
  } catch (err) {
    if (__DEV__) {
      console.warn("[devSession] silent dev login failed:", err);
    }
  }
}
