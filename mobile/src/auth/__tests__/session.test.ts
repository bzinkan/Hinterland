jest.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digestStringAsync: jest.fn(async (_algorithm: string, value: string) => `hash:${value}`),
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
}));

jest.mock("react-native", () => ({ Platform: { OS: "web" } }));

import {
  clearPersistedSessionUser,
  getPersistedSessionUser,
  persistSessionUser,
} from "@/src/auth/session";

const values = new Map<string, string>();

beforeAll(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    },
  });
});

beforeEach(() => values.clear());

describe("persisted session identity boundary", () => {
  const user = {
    id: "01USER0000000000000000000",
    role: "kid",
    display_name: "Scout",
    entra_oid: null,
  };

  it("restores an offline identity only for the same bearer token", async () => {
    await persistSessionUser(user, "token-a");

    await expect(getPersistedSessionUser("token-a")).resolves.toEqual(user);
    await expect(getPersistedSessionUser("token-b")).resolves.toBeNull();
  });

  it("removes the snapshot on sign-out", async () => {
    await persistSessionUser(user, "token-a");
    await clearPersistedSessionUser();

    await expect(getPersistedSessionUser("token-a")).resolves.toBeNull();
  });
});
