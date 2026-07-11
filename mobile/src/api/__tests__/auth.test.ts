import {
  CurrentUserContractError,
  normalizeCurrentUser,
  parentSignup,
} from "@/src/api/auth";

jest.mock("@/src/auth/token", () => ({
  getBearerTokenSnapshot: jest.fn(async () => ({ token: "adult-token", generation: 0 })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("normalizeCurrentUser", () => {
  it("uses the canonical local user id", () => {
    expect(
      normalizeCurrentUser({
        id: "01J0LOCALUSER0000000000000",
        uid: "01J0LOCALUSER0000000000000",
        entra_oid: null,
        role: "kid",
        display_name: "Sparrow",
      }),
    ).toEqual({
      id: "01J0LOCALUSER0000000000000",
      entra_oid: null,
      role: "kid",
      display_name: "Sparrow",
    });
  });

  it("accepts uid as a one-release compatibility alias", () => {
    expect(
      normalizeCurrentUser({
        uid: "01J0LEGACYUSER000000000000",
        entra_oid: "entra-object-id",
        role: "parent",
        display_name: "Parent One",
      }).id,
    ).toBe("01J0LEGACYUSER000000000000");
  });

  it.each([
    null,
    undefined,
    "kid-1",
    42,
    [],
    { role: "kid", display_name: "Sparrow", entra_oid: null },
    { id: "kid-1", display_name: "Sparrow", entra_oid: null },
    { id: "kid-1", role: "kid", entra_oid: null },
    { id: "kid-1", role: "kid", display_name: "Sparrow" },
    { id: "kid-1", role: "kid", display_name: "Sparrow", entra_oid: 42 },
    { id: null, uid: "kid-1", role: "kid", display_name: "Sparrow", entra_oid: null },
    { id: "", uid: "kid-1", role: "kid", display_name: "Sparrow", entra_oid: null },
    {
      id: "kid-1",
      uid: "different-kid",
      role: "kid",
      display_name: "Sparrow",
      entra_oid: null,
    },
  ])("rejects an incomplete identity: %p", (value) => {
    expect(() => normalizeCurrentUser(value)).toThrow(CurrentUserContractError);
  });

  it("wraps property-access shape failures as contract errors", () => {
    const value = new Proxy(
      {},
      {
        get() {
          throw new TypeError("malformed response object");
        },
      },
    );

    expect(() => normalizeCurrentUser(value)).toThrow(CurrentUserContractError);
  });
});

describe("parent signup consent proof", () => {
  it("requires the receipt and private nonce in the authenticated request", async () => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        id: "parent-1",
        entra_oid: "entra-1",
        role: "parent",
        display_name: "Alex Adult",
      }),
    })) as unknown as typeof fetch;
    const proof = {
      receiptId: "consent-1",
      nonce: "cd".repeat(32),
      policyVersion: "2026-07-11-W1-INTERNAL",
    };

    await parentSignup("Alex Adult", proof);

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/auth/parent-signup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          display_name: "Alex Adult",
          consent_id: proof.receiptId,
          consent_nonce: proof.nonce,
        }),
      }),
    );
  });
});
