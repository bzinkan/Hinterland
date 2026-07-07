import { identifyPhoto } from "@/src/api/photos";

jest.mock("@/src/auth/token", () => ({
  getBearerToken: jest.fn(async () => "jwt-test-token"),
}));

describe("photo API", () => {
  beforeEach(() => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        photo_id: "photo-1",
        suggestions: [],
        cv_unavailable: false,
        no_matches: true,
      }),
    })) as unknown as typeof fetch;
  });

  it("identifies a pending photo before final observation save", async () => {
    const result = await identifyPhoto("photo-1");

    expect(result.no_matches).toBe(true);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/photos/photo-1/identify",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer jwt-test-token",
        }),
      }),
    );
  });
});
