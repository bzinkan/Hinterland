import { CurrentUserContractError, normalizeCurrentUser } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import { canRestoreOfflineIdentity } from "@/src/auth/AuthSessionCoordinator";

describe("offline identity restoration policy", () => {
  it("never restores a persisted owner after a malformed /v1/me 200", () => {
    expect(canRestoreOfflineIdentity(new CurrentUserContractError())).toBe(false);
  });

  it("classifies a null /v1/me body as a contract failure, never a transport outage", () => {
    let error: unknown;
    try {
      normalizeCurrentUser(null);
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(CurrentUserContractError);
    expect(error).not.toBeInstanceOf(TypeError);
    expect(canRestoreOfflineIdentity(error)).toBe(false);
  });

  it.each([401, 403, 404, 409, 422])("rejects authoritative HTTP %s responses", (status) => {
    expect(canRestoreOfflineIdentity(new ApiError(status, null, "rejected"))).toBe(false);
  });

  it.each([408, 429, 500, 503])("allows a token-bound snapshot for retryable %s", (status) => {
    expect(canRestoreOfflineIdentity(new ApiError(status, null, "retry"))).toBe(true);
  });

  it("allows a token-bound snapshot for a fetch transport failure", () => {
    expect(canRestoreOfflineIdentity(new TypeError("Network request failed"))).toBe(true);
  });
});
