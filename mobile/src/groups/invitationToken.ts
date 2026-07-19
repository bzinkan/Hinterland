const STORAGE_KEY = "hinterland.group_invitation.v1";
const TOKEN_MAX_AGE_MS = 72 * 60 * 60 * 1000;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{40,128}$/;
const CANONICAL_PARENT_ORIGIN = "https://parents.thehinterlandguide.app";

type StoredInvitation = {
  token: string;
  capturedAt: number;
};

export function captureInvitationTokenFromFragment(): string | null {
  if (typeof window === "undefined") return null;
  const hash = window.location.hash;
  if (!hash) return readPendingInvitationToken();

  const params = new URLSearchParams(hash.slice(1));
  const candidate = params.get("token");
  // Scrub before any request or user interaction, even when the fragment is malformed.
  window.history.replaceState(
    null,
    document.title,
    `${window.location.pathname}${window.location.search}`,
  );
  if (!candidate || !TOKEN_PATTERN.test(candidate)) {
    clearPendingInvitationToken();
    return null;
  }

  const value: StoredInvitation = { token: candidate, capturedAt: Date.now() };
  // A failed overwrite must not leave a different adult's older invitation
  // available in this tab. Clear first, then persist the newly captured link.
  clearPendingInvitationToken();
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    return candidate;
  } catch {
    clearPendingInvitationToken();
    return null;
  }
}

export function readPendingInvitationToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredInvitation>;
    if (
      typeof parsed.token !== "string" ||
      !TOKEN_PATTERN.test(parsed.token) ||
      typeof parsed.capturedAt !== "number" ||
      !Number.isFinite(parsed.capturedAt) ||
      parsed.capturedAt > Date.now() ||
      Date.now() - parsed.capturedAt > TOKEN_MAX_AGE_MS
    ) {
      clearPendingInvitationToken();
      return null;
    }
    return parsed.token;
  } catch {
    clearPendingInvitationToken();
    return null;
  }
}

export function clearPendingInvitationToken(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // An unavailable storage API is already fail-closed.
  }
}

export function validateInvitationUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    if (
      url.origin !== CANONICAL_PARENT_ORIGIN ||
      url.protocol !== "https:" ||
      url.pathname !== "/group-invite" ||
      url.search !== ""
    ) {
      return false;
    }
    const params = new URLSearchParams(url.hash.slice(1));
    const token = params.get("token") ?? "";
    return TOKEN_PATTERN.test(token) && url.hash === `#token=${token}`;
  } catch {
    return false;
  }
}

export async function copyInvitationUrl(value: string): Promise<void> {
  if (!validateInvitationUrl(value) || typeof navigator === "undefined") {
    throw new Error("Invitation link is unavailable");
  }
  if (!navigator.clipboard?.writeText) {
    throw new Error("Clipboard is unavailable");
  }
  await navigator.clipboard.writeText(value);
}
