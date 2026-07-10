import { create } from "zustand";
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

import type { CurrentUser } from "@/src/api/auth";

const SESSION_USER_KEY = "hinterland.session_user.v1";

type AuthSession =
  | { status: "initializing"; user: null }
  | { status: "anonymous"; user: null }
  | { status: "authenticated"; user: CurrentUser };

type AuthSessionActions = {
  setInitializing: () => void;
  setAnonymous: () => void;
  setAuthenticated: (user: CurrentUser) => void;
};

export const useAuthSession = create<AuthSession & AuthSessionActions>((set) => ({
  status: "initializing",
  user: null,
  setInitializing: () => set({ status: "initializing", user: null }),
  setAnonymous: () => set({ status: "anonymous", user: null }),
  setAuthenticated: (user) => set({ status: "authenticated", user }),
}));

export async function getPersistedSessionUser(token: string): Promise<CurrentUser | null> {
  const raw =
    Platform.OS === "web"
      ? (globalThis.localStorage?.getItem(SESSION_USER_KEY) ?? null)
      : await SecureStore.getItemAsync(SESSION_USER_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<CurrentUser> & {
      token_fingerprint?: unknown;
    };
    if (value.token_fingerprint !== (await fingerprintToken(token))) return null;
    if (
      typeof value.id === "string" &&
      typeof value.role === "string" &&
      typeof value.display_name === "string"
    ) {
      return {
        id: value.id,
        role: value.role,
        display_name: value.display_name,
        entra_oid: typeof value.entra_oid === "string" ? value.entra_oid : null,
      };
    }
  } catch {
    // Invalid snapshots are treated as signed out.
  }
  return null;
}

export async function persistSessionUser(user: CurrentUser, token: string): Promise<void> {
  const value = JSON.stringify({
    ...user,
    token_fingerprint: await fingerprintToken(token),
  });
  if (Platform.OS === "web") {
    globalThis.localStorage?.setItem(SESSION_USER_KEY, value);
  } else {
    await SecureStore.setItemAsync(SESSION_USER_KEY, value);
  }
}

export async function clearPersistedSessionUser(): Promise<void> {
  if (Platform.OS === "web") {
    globalThis.localStorage?.removeItem(SESSION_USER_KEY);
  } else {
    await SecureStore.deleteItemAsync(SESSION_USER_KEY);
  }
}

async function fingerprintToken(token: string): Promise<string> {
  return Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, token);
}
