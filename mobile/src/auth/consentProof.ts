export const W1_CONSENT_POLICY_VERSION = "2026-07-11-W1-INTERNAL";

const CONSENT_PROOF_KEY = "hinterland.parent_consent_proof.v1";
const NONCE_BYTE_LENGTH = 32;

export type ParentConsentProof = {
  receiptId: string;
  nonce: string;
  policyVersion: string;
};

export class ParentConsentProofUnavailableError extends Error {
  constructor() {
    super("A secure temporary parent-consent proof is unavailable");
    this.name = "ParentConsentProofUnavailableError";
  }
}

/** Generate the one-time proof with browser Web Crypto, never Math.random. */
export function generateParentConsentNonce(): string {
  const crypto = globalThis.crypto;
  if (!crypto || typeof crypto.getRandomValues !== "function") {
    throw new ParentConsentProofUnavailableError();
  }
  const bytes = new Uint8Array(NONCE_BYTE_LENGTH);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Keep the proof only in this browser tab. sessionStorage survives the MSAL
 * redirect while avoiding durable, cross-tab, or native-device persistence.
 */
export function storePendingParentConsentProof(proof: ParentConsentProof): void {
  if (!isParentConsentProof(proof)) throw new ParentConsentProofUnavailableError();
  const storage = requireSessionStorage();
  try {
    storage.setItem(CONSENT_PROOF_KEY, JSON.stringify(proof));
  } catch {
    throw new ParentConsentProofUnavailableError();
  }
}

export function readPendingParentConsentProof(): ParentConsentProof | null {
  const storage = getSessionStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(CONSENT_PROOF_KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!isParentConsentProof(value)) {
      storage.removeItem(CONSENT_PROOF_KEY);
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

/** Clear only the exact proof whose server linkage just succeeded. */
export function clearPendingParentConsentProof(proof: ParentConsentProof): void {
  const storage = requireSessionStorage();
  try {
    const current = readPendingParentConsentProof();
    if (current && sameProof(current, proof)) storage.removeItem(CONSENT_PROOF_KEY);
  } catch {
    throw new ParentConsentProofUnavailableError();
  }
}

function isParentConsentProof(value: unknown): value is ParentConsentProof {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proof = value as Partial<ParentConsentProof>;
  return (
    typeof proof.receiptId === "string" &&
    proof.receiptId.length > 0 &&
    typeof proof.nonce === "string" &&
    /^[0-9a-f]{64}$/.test(proof.nonce) &&
    proof.policyVersion === W1_CONSENT_POLICY_VERSION
  );
}

function sameProof(left: ParentConsentProof, right: ParentConsentProof): boolean {
  return (
    left.receiptId === right.receiptId &&
    left.nonce === right.nonce &&
    left.policyVersion === right.policyVersion
  );
}

function getSessionStorage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

function requireSessionStorage(): Storage {
  const storage = getSessionStorage();
  if (!storage) throw new ParentConsentProofUnavailableError();
  return storage;
}
