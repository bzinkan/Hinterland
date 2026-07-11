import {
  clearPendingParentConsentProof,
  generateParentConsentNonce,
  readPendingParentConsentProof,
  storePendingParentConsentProof,
  W1_CONSENT_POLICY_VERSION,
  type ParentConsentProof,
} from "@/src/auth/consentProof";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return Array.from(this.values.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

describe("parent consent proof", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "sessionStorage", {
      configurable: true,
      value: new MemoryStorage(),
    });
  });

  it("generates 32 Web Crypto bytes and persists only the exact tab proof", () => {
    const nonce = generateParentConsentNonce();
    expect(nonce).toMatch(/^[0-9a-f]{64}$/);
    const proof: ParentConsentProof = {
      receiptId: "consent-1",
      nonce,
      policyVersion: W1_CONSENT_POLICY_VERSION,
    };

    storePendingParentConsentProof(proof);

    expect(readPendingParentConsentProof()).toEqual(proof);
    expect(globalThis.sessionStorage.length).toBe(1);
  });

  it("clears only the exact proof whose linkage succeeded", () => {
    const first: ParentConsentProof = {
      receiptId: "consent-1",
      nonce: "01".repeat(32),
      policyVersion: W1_CONSENT_POLICY_VERSION,
    };
    const replacement: ParentConsentProof = {
      receiptId: "consent-2",
      nonce: "02".repeat(32),
      policyVersion: W1_CONSENT_POLICY_VERSION,
    };
    storePendingParentConsentProof(replacement);

    clearPendingParentConsentProof(first);
    expect(readPendingParentConsentProof()).toEqual(replacement);

    clearPendingParentConsentProof(replacement);
    expect(readPendingParentConsentProof()).toBeNull();
  });

  it("fails closed and discards malformed stored data", () => {
    globalThis.sessionStorage.setItem(
      "hinterland.parent_consent_proof.v1",
      JSON.stringify({ receiptId: "consent-1", nonce: "visible", policyVersion: "old" }),
    );

    expect(readPendingParentConsentProof()).toBeNull();
    expect(globalThis.sessionStorage.length).toBe(0);
  });
});
