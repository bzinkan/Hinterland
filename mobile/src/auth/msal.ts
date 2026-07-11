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

import {
  clearBearerToken,
  getBearerToken,
  setBearerToken,
} from "@/src/auth/token";
import { MsalSessionController } from "@/src/auth/msalSession";
import { env } from "@/src/config/env";

// metro.config.js's resolveRequest shim fixes the @azure/msal-common/
// browser exports-map path, so we can now import msal-browser lazily
// inside getMsal() without bundle-time failures.
type MsalModule = typeof import("@azure/msal-browser");
type PublicClientApplicationType = InstanceType<MsalModule["PublicClientApplication"]>;

let msalApp: PublicClientApplicationType | null = null;
let initPromise: Promise<PublicClientApplicationType | null> | null = null;
let listenerAttached = false;
let sessionController: MsalSessionController | null = null;

const ENTRA_SCOPES = ["api://hinterland-api/user.access"];

export type SignedInAdultProfile = {
  suggestedDisplayName: string;
};

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

function getSessionController(
  ms: PublicClientApplicationType,
): MsalSessionController {
  if (!sessionController) {
    sessionController = new MsalSessionController(
      ms,
      {
        get: getBearerToken,
        set: setBearerToken,
        clear: clearBearerToken,
      },
      ENTRA_SCOPES,
    );
  }
  return sessionController;
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
    const session = getSessionController(ms);

    // Replay any pending redirect from a sign-in round-trip first and make its
    // exact account authoritative before looking at the broader MSAL cache.
    try {
      const redirectResult = await ms.handleRedirectPromise();
      if (redirectResult?.account) {
        session.activateAccount(redirectResult.account);
      }
    } catch {
      // Ignore -- the next acquireTokenSilent attempt will surface real issues.
    }

    const { EventType } = await import("@azure/msal-browser");
    ms.addEventCallback((evt) => {
      // Token-acquisition events are deliberately ignored: reacting to an
      // ACQUIRE_TOKEN_SUCCESS by acquiring again creates a feedback loop.
      if (
        evt.eventType !== EventType.LOGIN_SUCCESS &&
        evt.eventType !== EventType.ACTIVE_ACCOUNT_CHANGED &&
        evt.eventType !== EventType.LOGOUT_SUCCESS
      ) {
        return;
      }
      void session.handleEvent(evt.eventType, evt.payload, EventType);
    });

    // Attach the listener before acquisition so an account switch during a
    // slow silent token request cannot be missed. syncCachedAccount() starts
    // from MSAL's explicit active account, including the redirect selection.
    await session.syncCachedAccount();
  })();
}

/**
 * Return the signed-in adult's editable display-name suggestion after an API
 * token is safely available. The email/username is intentionally not exposed
 * to the component or used as a display name.
 */
export async function getSignedInAdultProfile(): Promise<SignedInAdultProfile | null> {
  const ms = await getMsal();
  if (!ms) return null;
  const account = await getSessionController(ms).acquireCurrentAccount(false);
  if (!account) return null;
  return { suggestedDisplayName: suggestAdultDisplayName(account.name) };
}

/**
 * Republish a freshly acquired token after parent-signup. This intentional
 * token-change signal makes AuthSessionCoordinator rerun canonical /v1/me;
 * callers never receive or render the token itself.
 */
export async function refreshCurrentAdultSession(): Promise<void> {
  const ms = await getMsal();
  if (!ms) throw new Error("Microsoft sign-in is no longer available.");
  const account = await getSessionController(ms).acquireCurrentAccount(true);
  if (!account) throw new Error("Microsoft sign-in is no longer available.");
}

export function suggestAdultDisplayName(name: string | undefined): string {
  const compact = (name ?? "").trim().replace(/\s+/g, " ");
  return Array.from(compact).slice(0, 80).join("");
}

export async function signIn(): Promise<void> {
  const ms = await getMsal();
  if (!ms) return;
  // A parents web browser may be shared by several adults. Always require an
  // explicit interactive account choice instead of allowing Entra/MSAL to
  // reuse a different cached adult silently.
  await ms.loginRedirect({ scopes: ENTRA_SCOPES, prompt: "select_account" });
}

export async function signOut(): Promise<void> {
  const ms = await getMsal();
  if (!ms) {
    await clearBearerToken();
    return;
  }
  await getSessionController(ms).beginLogout();
  // With no account argument MSAL clears every cached account. Passing only
  // the active account would leave another adult cached and eligible for an
  // unintended automatic session after the redirect.
  await ms.logoutRedirect();
}
