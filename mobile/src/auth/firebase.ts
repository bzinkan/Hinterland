/**
 * Firebase Web SDK bootstrap + auth-state plumbing.
 *
 * Init runs once at module load. The `onIdTokenChanged` listener is
 * what wires Firebase -> our bearer-token storage:
 *   - sign-in / token refresh -> setBearerToken(idToken)
 *   - sign-out                -> clearBearerToken()
 *
 * Firebase refreshes ID tokens roughly hourly on its own, so apiRequest
 * always reads a fresh token from storage without us having to plumb
 * refresh logic through every call site.
 */
import { getApps, initializeApp, type FirebaseApp } from "firebase/app";
import { getAuth, onIdTokenChanged, type Auth, type User } from "firebase/auth";

import { clearBearerToken, setBearerToken } from "@/src/auth/token";
import { env } from "@/src/config/env";

let app: FirebaseApp | null = null;
let auth: Auth | null = null;
let listenerAttached = false;
let sawFirebaseUser = false;

export function getFirebaseApp(): FirebaseApp {
  if (app) return app;
  app = getApps()[0] ?? initializeApp(env.firebase);
  return app;
}

export function getFirebaseAuth(): Auth {
  if (auth) return auth;
  auth = getAuth(getFirebaseApp());
  return auth;
}

/**
 * Idempotent. Call once at app boot. Subsequent calls are no-ops.
 * Safe to call before any sign-in -- it just won't fire until the user
 * signs in.
 */
export function ensureTokenSync(): void {
  if (listenerAttached) return;
  listenerAttached = true;
  onIdTokenChanged(getFirebaseAuth(), (user: User | null) => {
    void syncToken(user);
  });
}

async function syncToken(user: User | null): Promise<void> {
  if (!user) {
    // The listener fires with null at every boot before any sign-in.
    // Only a real signed-in -> signed-out transition may clear storage:
    // the stored token is not necessarily Firebase's (kid session JWT,
    // dev auto-login, pasted dev token), and boot-clearing wiped those
    // on every dev/preview launch.
    if (sawFirebaseUser) {
      sawFirebaseUser = false;
      await clearBearerToken();
    }
    return;
  }
  sawFirebaseUser = true;
  const token = await user.getIdToken();
  await setBearerToken(token);
}
