import {
  createObservation,
  presignPhoto,
  updateObservationIdentification,
} from "@/src/api/observations";

jest.mock("@/src/auth/token", () => ({
  getBearerToken: jest.fn(async () => "jwt-test-token"),
  getBearerTokenSnapshot: jest.fn(async () => ({
    token: "jwt-test-token",
    generation: 0,
  })),
  bearerTokenSnapshotIsCurrent: jest.fn(() => true),
}));

describe("observation idempotency contract", () => {
  beforeEach(() => {
    globalThis.fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    })) as unknown as typeof fetch;
  });

  it("sends one submission ULID to both presign and create", async () => {
    const key = "01JZ3FQ4X2Y7V8W9A0BCDEFGHJ";
    await presignPhoto(key);
    await createObservation(
      {
        photo_id: "photo-1",
        observed_at: "2026-07-09T12:00:00.000Z",
        geohash4: null,
        location_source: "none",
        identification_source: "unknown",
      },
      key,
    );

    expect(globalThis.fetch).toHaveBeenNthCalledWith(
      1,
      "http://jest.invalid/v1/photos/presign",
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": key }),
      }),
    );
    expect(globalThis.fetch).toHaveBeenNthCalledWith(
      2,
      "http://jest.invalid/v1/observations",
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": key }),
      }),
    );
  });

  it("sends optimistic identification corrections to the dedicated endpoint", async () => {
    await updateObservationIdentification("observation-1", {
      taxon_id: 3,
      source: "catalog",
      expected_revision: 2,
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://jest.invalid/v1/observations/observation-1/identification",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          taxon_id: 3,
          source: "catalog",
          expected_revision: 2,
        }),
      }),
    );
  });
});
