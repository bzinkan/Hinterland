/**
 * Bearer-token storage for the API client.
 *
 * Development/preview can paste a bearer token into Settings. Production
 * writes the same key from either MSAL on the parents web surface or the
 * Hinterland kid handoff exchange on native.
 *
 * Web build: SecureStore is no-op on web. Falls back to localStorage so the
 * web preview still works during development.
 */
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

import { rotateImperativeRequestBoundary } from "@/src/auth/requestBoundary";

const KEY = "dragonfly.bearer_token";

export type BearerTokenChange = "set" | "cleared";

const tokenListeners = new Set<(change: BearerTokenChange) => void>();
let tokenGeneration = 0;
let tokenMutationTail: Promise<void> = Promise.resolve();

export type BearerTokenSnapshot = {
  token: string | null;
  generation: number;
};

export function subscribeBearerTokenChanges(
  listener: (change: BearerTokenChange) => void,
): () => void {
  tokenListeners.add(listener);
  return () => tokenListeners.delete(listener);
}

function notifyTokenChange(change: BearerTokenChange): void {
  for (const listener of tokenListeners) listener(change);
}

export async function getBearerTokenSnapshot(): Promise<BearerTokenSnapshot> {
  // A mutation may be queued while a previous write is settling. Wait until
  // the tail observed here is still the current tail before reading storage.
  while (true) {
    const stableTail = tokenMutationTail;
    await stableTail;
    if (stableTail !== tokenMutationTail) continue;
    const generation = tokenGeneration;
    return { token: await readStoredBearerToken(), generation };
  }
}

export function bearerTokenSnapshotIsCurrent(
  snapshot: Pick<BearerTokenSnapshot, "generation">,
): boolean {
  return snapshot.generation === tokenGeneration;
}

export async function getBearerToken(): Promise<string | null> {
  while (true) {
    const snapshot = await getBearerTokenSnapshot();
    if (bearerTokenSnapshotIsCurrent(snapshot)) return snapshot.token;
  }
}

async function readStoredBearerToken(): Promise<string | null> {
  if (Platform.OS === "web") {
    return globalThis.localStorage?.getItem(KEY) ?? null;
  }
  return await SecureStore.getItemAsync(KEY);
}

export function setBearerToken(token: string): Promise<void> {
  return enqueueTokenMutation("set", async () => {
    if (Platform.OS === "web") {
      globalThis.localStorage?.setItem(KEY, token);
      return;
    }
    await SecureStore.setItemAsync(KEY, token);
  });
}

export function clearBearerToken(): Promise<void> {
  return enqueueTokenMutation("cleared", async () => {
    if (Platform.OS === "web") {
      globalThis.localStorage?.removeItem(KEY);
      return;
    }
    await SecureStore.deleteItemAsync(KEY);
  });
}

function enqueueTokenMutation(
  change: BearerTokenChange,
  write: () => Promise<void>,
): Promise<void> {
  invalidateAuthenticatedWrites();
  const previousTail = tokenMutationTail;
  const operation = previousTail.then(write);
  const result = operation.then(
    () => {
      // Also invalidate writes that started while secure storage was changing.
      invalidateAuthenticatedWrites();
      notifyTokenChange(change);
    },
    (error) => {
      invalidateAuthenticatedWrites();
      throw error;
    },
  );
  tokenMutationTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

function invalidateAuthenticatedWrites(): void {
  tokenGeneration += 1;
  rotateImperativeRequestBoundary();
}
