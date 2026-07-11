import { recordConsent, W1_CONSENT_POLICY_VERSION } from "@/src/api/consent";

jest.mock("@/src/auth/token", () => ({
  getBearerTokenSnapshot: jest.fn(async () => ({ token: null, generation: 0 })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("W1 consent contract", () => {
  it("submits the exact policy version displayed by the web build", async () => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        id: "01J0CONSENT000000000000000",
        recorded_at: "2026-07-11T12:00:00Z",
        policy_version: W1_CONSENT_POLICY_VERSION,
      }),
    })) as unknown as typeof fetch;

    const nonce = "ab".repeat(32);
    await recordConsent("parent@example.com", nonce);

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/auth/consent",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          email: "parent@example.com",
          policy_version: W1_CONSENT_POLICY_VERSION,
          consent_nonce: nonce,
        }),
      }),
    );
  });
});
