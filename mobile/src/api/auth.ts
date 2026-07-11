import { apiRequest } from "@/src/api/client";
import type { ParentConsentProof } from "@/src/auth/consentProof";

export type UserResponse = {
  id: string;
  /**
   * Entra External Identities object id for adult accounts.
   * Null for kid accounts (kids have no Entra identity).
   */
  entra_oid: string | null;
  role: string;
  display_name: string;
};

export type CurrentUser = {
  id: string;
  entra_oid: string | null;
  role: string;
  display_name: string;
};

type CurrentUserWire = {
  id?: unknown;
  uid?: unknown;
  entra_oid?: unknown;
  role?: unknown;
  display_name?: unknown;
};

export class CurrentUserContractError extends Error {
  constructor() {
    super("GET /v1/me omitted canonical identity fields");
    this.name = "CurrentUserContractError";
  }
}

/**
 * Fail closed when the server does not provide a canonical, owner-scoped
 * identity. `uid` remains an accepted compatibility alias for one release,
 * but display name and role must still be explicit server values.
 */
export function normalizeCurrentUser(value: unknown): CurrentUser {
  try {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new CurrentUserContractError();
    }

    const wire = value as CurrentUserWire;
    const hasId = Object.prototype.hasOwnProperty.call(wire, "id");
    const hasEntraOid = Object.prototype.hasOwnProperty.call(wire, "entra_oid");
    const uid = typeof wire.uid === "string" && wire.uid.length > 0 ? wire.uid : null;
    const id = hasId
      ? typeof wire.id === "string" && wire.id.length > 0
        ? wire.id
        : null
      : uid;
    const displayName =
      typeof wire.display_name === "string" && wire.display_name.length > 0
        ? wire.display_name
        : null;
    const role = typeof wire.role === "string" && wire.role.length > 0 ? wire.role : null;
    const entraOid = !hasEntraOid
      ? undefined
      : wire.entra_oid === null
        ? null
        : typeof wire.entra_oid === "string" && wire.entra_oid.length > 0
          ? wire.entra_oid
          : undefined;

    if (
      !id ||
      (hasId && uid != null && uid !== id) ||
      !displayName ||
      !role ||
      entraOid === undefined
    ) {
      throw new CurrentUserContractError();
    }
    return { id, display_name: displayName, role, entra_oid: entraOid };
  } catch (error) {
    if (error instanceof CurrentUserContractError) throw error;
    throw new CurrentUserContractError();
  }
}

export function parentSignup(
  displayName: string,
  proof: ParentConsentProof,
): Promise<UserResponse> {
  return apiRequest<UserResponse>("/v1/auth/parent-signup", {
    method: "POST",
    body: {
      display_name: displayName,
      consent_id: proof.receiptId,
      consent_nonce: proof.nonce,
    },
  });
}

export async function getMe(signal?: AbortSignal): Promise<CurrentUser> {
  const value = await apiRequest<unknown>("/v1/me", { signal });
  return normalizeCurrentUser(value);
}

export type AccountDeletionResponse = {
  status: "deletion_requested";
  user_id: string;
  requested_at: string;
};

export function requestAccountDeletion(
  signal?: AbortSignal,
): Promise<AccountDeletionResponse> {
  return apiRequest<AccountDeletionResponse>("/v1/me", {
    method: "DELETE",
    signal,
  });
}

export type KidExchangeResponse = {
  session_token: string;
  expires_at: string;
  user: {
    id: string;
    role: string;
    display_name: string;
  };
};

/**
 * Exchange a kid handoff JWT (15-min single-use) for a 30-day session JWT.
 * Public endpoint -- the handoff token IS the auth.
 */
export function kidExchange(handoffToken: string): Promise<KidExchangeResponse> {
  return apiRequest<KidExchangeResponse>("/v1/auth/kid-exchange", {
    method: "POST",
    body: { handoff_token: handoffToken },
    unauthenticated: true,
  });
}

/**
 * Silent dev auto-login for pre-production builds. The shared key is the
 * auth; the backend 404s unless the deployment explicitly enables the
 * route (and always 404s on prod). Returns the same shape as kidExchange
 * so callers reuse the existing response type.
 */
export function devLogin(key: string): Promise<KidExchangeResponse> {
  return apiRequest<KidExchangeResponse>("/v1/auth/dev-login", {
    method: "POST",
    headers: { "X-Dev-Login-Key": key },
    unauthenticated: true,
  });
}
