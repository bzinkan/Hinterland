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
import type { Configuration } from "@azure/msal-browser";
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

type ParentEntraConfiguration = {
  clientId: string;
  authority: string;
  redirectUri: string;
};

export type SignedInAdultProfile = {
  suggestedDisplayName: string;
};

type ParentRedirectClient = Pick<
  PublicClientApplicationType,
  "clearCache" | "handleRedirectPromise"
>;

type ParentRedirectSession = Pick<
  MsalSessionController,
  "activateAccount" | "beginLogout" | "syncCachedAccount"
>;

export type ParentRedirectOutcome =
  | "none"
  | "handled"
  | "callback_pending"
  | "failed";

type RedirectFailureNavigation = () => void;
type ParentCallbackRouteCheck = () => boolean;

function isParentCallbackRoute(): boolean {
  return (
    typeof window !== "undefined" &&
    window.location.pathname === "/auth/callback"
  );
}

function scrubFailedParentCallback(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/auth/callback") return;
  // handleRedirectPromise already rejected, so retaining the provider response
  // no longer helps recovery. Remove it without leaving the loading-only route.
  window.history.replaceState(null, document.title, "/auth/callback");
}

function replaceFailedParentCallback(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/auth/callback") return;
  // Remove the provider response from browser history only after both local
  // identity stores are cleared. The sign-in screen exposes no provider error
  // detail and requires an explicit new account choice.
  window.location.replace("/sign-in");
}

async function failParentRedirect(
  ms: ParentRedirectClient,
  session: ParentRedirectSession,
  navigateAfterFailure: RedirectFailureNavigation,
  scrubAfterFailure: RedirectFailureNavigation,
): Promise<"failed"> {
  try {
    scrubAfterFailure();
  } catch {
    // URL cleanup must never prevent identity cleanup.
  }
  let identityCleared = true;
  try {
    await session.beginLogout();
  } catch {
    identityCleared = false;
  }
  try {
    await ms.clearCache();
  } catch {
    identityCleared = false;
  }
  if (identityCleared) {
    try {
      navigateAfterFailure();
    } catch {
      // Identity is already cleared; staying on the callback is fail closed.
    }
  }
  return "failed";
}

export function createParentMsalConfiguration(
  entra: ParentEntraConfiguration = env.entra,
): Configuration {
  return {
    auth: {
      clientId: entra.clientId,
      authority: entra.authority,
      knownAuthorities: [new URL(entra.authority).host],
      redirectUri: entra.redirectUri,
    },
    cache: {
      cacheLocation: "localStorage",
    },
  };
}

export async function consumeParentRedirect(
  ms: ParentRedirectClient,
  session: ParentRedirectSession,
  navigateAfterFailure: RedirectFailureNavigation = replaceFailedParentCallback,
  scrubAfterFailure: RedirectFailureNavigation = scrubFailedParentCallback,
  callbackRouteCheck: ParentCallbackRouteCheck = isParentCallbackRoute,
): Promise<ParentRedirectOutcome> {
  // The dedicated callback route is loading-only. Consume the response before
  // consulting the broader account cache, then return to the exact parent
  // setup page that initiated login so its tab-scoped consent proof survives.
  try {
    const redirectResult = await ms.handleRedirectPromise({
      navigateToLoginRequestUrl: true,
    });
    if (redirectResult?.account) {
      session.activateAccount(redirectResult.account);
      return "handled";
    }
    // On the real callback, MSAL normally caches the response, starts a
    // no-history navigation back to the initiating page, and resolves null as
    // the old page unloads. A direct/reloaded empty callback must also remain
    // inert. In both cases, do not clear MSAL's pending response and do not
    // select a stale cached adult on this page.
    if (callbackRouteCheck()) {
      return "callback_pending";
    }
    return "none";
  } catch {
    // A failed callback in a shared browser must never fall through to a
    // previous adult's sole cached account. Scrub the OAuth response, clear
    // both the app bearer and complete MSAL cache, and skip cached-account sync
    // for this boot. If either clear fails, remain on the loading-only callback
    // route rather than navigating unsafely.
    return await failParentRedirect(
      ms,
      session,
      navigateAfterFailure,
      scrubAfterFailure,
    );
  }
}

export async function bootstrapParentSession(
  ms: ParentRedirectClient,
  session: ParentRedirectSession,
  beforeCachedAccountSync: () => void | Promise<void>,
  navigateAfterFailure?: RedirectFailureNavigation,
  scrubAfterFailure?: RedirectFailureNavigation,
  callbackRouteCheck?: ParentCallbackRouteCheck,
): Promise<ParentRedirectOutcome> {
  const outcome = await consumeParentRedirect(
    ms,
    session,
    navigateAfterFailure,
    scrubAfterFailure,
    callbackRouteCheck,
  );
  if (outcome === "failed" || outcome === "callback_pending") return outcome;
  await beforeCachedAccountSync();
  await session.syncCachedAccount();
  return outcome;
}

function isWeb(): boolean {
  return Platform.OS === "web";
}

export async function getMsal(): Promise<PublicClientApplicationType | null> {
  if (!isWeb()) return null;
  if (msalApp) return msalApp;
  if (initPromise) return initPromise;

  initPromise = (async () => {
    const { PublicClientApplication } = await import("@azure/msal-browser");
    const ms = new PublicClientApplication(createParentMsalConfiguration());
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
    await bootstrapParentSession(ms, session, async () => {
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
    });
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
  // reuse a different cached adult silently. Clearing the published bearer
  // first activates AuthSessionCoordinator's account-change boundary: it
  // cancels requests and removes every user-scoped query before account
  // selection leaves this page.
  await clearBearerToken();
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
