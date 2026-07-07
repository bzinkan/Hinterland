/**
 * Microsoft Entra External Identities (MSAL.js) bootstrap + auth-state
 * plumbing for the parents web surface.
 *
 * Native parent setup is web-first for the W1 pilot because
 * `@azure/msal-browser` is web-only. Kid sign-in does NOT use MSAL on any
 * platform: `/v1/auth/kid-exchange` issues a Hinterland-signed session JWT
 * directly after the QR handoff.
 *
 * Usage shape:
 *   - `ensureTokenSync()` is called once at app boot; on every MSAL
 *     account/token change it writes the access token to bearer storage.
 *   - `getMsal()` returns the lazily-initialized PublicClientApplication.
 *   - `signIn()` / `signOut()` drive the hosted user-flow redirects.
 *
 * On iOS / Android, `Platform.OS === "web"` is false and these helpers
 * are no-ops so the bundle still imports cleanly.
 */
import { Platform } from "react-native";

import { clearBearerToken, setBearerToken } from "@/src/auth/token";
import { env } from "@/src/config/env";

// metro.config.js's resolveRequest shim fixes the @azure/msal-common/
// browser exports-map path, so we can now import msal-browser lazily
// inside getMsal() without bundle-time failures.
type MsalModule = typeof import("@azure/msal-browser");
type PublicClientApplicationType = InstanceType<MsalModule["PublicClientApplication"]>;
type AccountInfo = import("@azure/msal-browser").AccountInfo;

let msalApp: PublicClientApplicationType | null = null;
let initPromise: Promise<PublicClientApplicationType | null> | null = null;
let listenerAttached = false;

const ENTRA_SCOPES = ["api://hinterland-api/user.access"];

function isWeb(): boolean {
  return Platform.OS === "web";
}

export async function getMsal(): Promise<PublicClientApplicationType | null> {
  if (!isWeb()) return null;
  if (msalApp) return msalApp;
  if (initPromise) return initPromise;

  initPromise = (async () => {
    const { PublicClientApplication } = await import("@azure/msal-browser");
    const ms = new PublicClientApplication({
      auth: {
        clientId: env.entra.clientId,
        authority: env.entra.authority,
        knownAuthorities: [new URL(env.entra.authority).host],
        redirectUri: env.entra.redirectUri,
      },
      cache: {
        cacheLocation: "localStorage",
      },
    });
    await ms.initialize();
    msalApp = ms;
    return ms;
  })();

  return initPromise;
}

async function syncToken(account: AccountInfo | null): Promise<void> {
  const ms = await getMsal();
  if (!ms) return;
  if (!account) {
    await clearBearerToken();
    return;
  }
  try {
    const result = await ms.acquireTokenSilent({
      account,
      scopes: ENTRA_SCOPES,
    });
    await setBearerToken(result.accessToken);
  } catch {
    // Silent acquisition failed -- user needs to re-sign-in. Clear the
    // bearer so protected calls 401 cleanly instead of using a stale
    // token.
    await clearBearerToken();
  }
}

/**
 * Idempotent. Call once at app boot. On web only.
 */
export function ensureTokenSync(): void {
  if (!isWeb()) return;
  if (listenerAttached) return;
  listenerAttached = true;

  void (async () => {
    const ms = await getMsal();
    if (!ms) return;

    // Replay any pending redirect from a sign-in round-trip first.
    try {
      const redirectResult = await ms.handleRedirectPromise();
      if (redirectResult?.account) {
        await syncToken(redirectResult.account);
      }
    } catch {
      // Ignore -- the next acquireTokenSilent attempt will surface real issues.
    }

    const initial = ms.getAllAccounts()[0] ?? null;
    await syncToken(initial);

    ms.addEventCallback((evt) => {
      // LOGIN_SUCCESS + ACQUIRE_TOKEN_SUCCESS + LOGOUT_SUCCESS, etc.
      // We don't switch on the literal event type -- any change to the
      // active account triggers a token sync.
      const account = ms.getAllAccounts()[0] ?? null;
      void syncToken(account);
    });
  })();
}

export async function signIn(): Promise<void> {
  const ms = await getMsal();
  if (!ms) return;
  await ms.loginRedirect({ scopes: ENTRA_SCOPES });
}

export async function signOut(): Promise<void> {
  const ms = await getMsal();
  if (!ms) {
    await clearBearerToken();
    return;
  }
  const account = ms.getAllAccounts()[0];
  if (account) {
    await ms.logoutRedirect({ account });
  } else {
    await clearBearerToken();
  }
}
