import { reissueKidHandoff } from "@/src/api/groups";

jest.mock("@/src/auth/token", () => ({
  getBearerTokenSnapshot: jest.fn(async () => ({
    token: "adult-token",
    generation: 0,
  })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("kid handoff reissue contract", () => {
  it("posts to the exact owner-scoped existing-kid route without a body", async () => {
    const responseBody = {
      id: "kid-1",
      display_name: "Sparrow",
      age_band: "9-10",
      handoff_token: "one-time-token",
      expires_at: "2026-07-14T23:15:00Z",
    };
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => responseBody,
    })) as unknown as typeof fetch;

    await expect(reissueKidHandoff("group-1", "kid-1")).resolves.toEqual(
      responseBody,
    );

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/groups/group-1/kids/kid-1/handoff",
      expect.objectContaining({
        method: "POST",
        body: undefined,
        headers: expect.objectContaining({
          Accept: "application/json",
          Authorization: "Bearer adult-token",
        }),
      }),
    );
  });
});
