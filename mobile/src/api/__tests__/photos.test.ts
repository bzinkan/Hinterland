import { abandonPhotoReservation, getPhotoUrl } from "@/src/api/photos";

jest.mock("@/src/auth/token", () => ({
  getBearerToken: jest.fn(async () => "jwt-test-token"),
  getBearerTokenSnapshot: jest.fn(async () => ({
    token: "jwt-test-token",
    generation: 0,
  })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("photo API", () => {
  beforeEach(() => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        photo_id: "photo-1",
        url: "https://photos.invalid/photo-1",
        expires_at: "2026-07-09T12:05:00.000Z",
      }),
    })) as unknown as typeof fetch;
  });

  it("requests a server-authorized photo URL", async () => {
    const result = await getPhotoUrl("photo-1");

    expect(result.photo_id).toBe("photo-1");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/photos/photo-1/url",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer jwt-test-token",
        }),
      }),
    );
  });

  it("abandons an unattached reservation through the owner endpoint", async () => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 204,
    })) as unknown as typeof fetch;

    await abandonPhotoReservation("photo-1");

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/photos/photo-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
