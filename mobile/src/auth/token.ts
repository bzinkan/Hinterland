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

const KEY = "dragonfly.bearer_token";

export async function getBearerToken(): Promise<string | null> {
  if (Platform.OS === "web") {
    return globalThis.localStorage?.getItem(KEY) ?? null;
  }
  return await SecureStore.getItemAsync(KEY);
}

export async function setBearerToken(token: string): Promise<void> {
  if (Platform.OS === "web") {
    globalThis.localStorage?.setItem(KEY, token);
    return;
  }
  await SecureStore.setItemAsync(KEY, token);
}

export async function clearBearerToken(): Promise<void> {
  if (Platform.OS === "web") {
    globalThis.localStorage?.removeItem(KEY);
    return;
  }
  await SecureStore.deleteItemAsync(KEY);
}
