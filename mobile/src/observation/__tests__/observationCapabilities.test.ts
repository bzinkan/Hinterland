import { photoHelperIsEnabled } from "@/src/observation/useObservationCapabilities";

describe("photo-helper capability", () => {
  it("enables only an explicit true flag", () => {
    expect(
      photoHelperIsEnabled({
        name: "Hinterland",
        env: "dev",
        release_lane: "local",
        version: "test",
        capabilities: { observation: { photo_helper_enabled: true } },
      }),
    ).toBe(true);
  });

  it("fails closed for false, absent, and unavailable metadata", () => {
    expect(
      photoHelperIsEnabled({
        name: "Hinterland",
        env: "dev",
        release_lane: "local",
        version: "test",
        capabilities: { observation: { photo_helper_enabled: false } },
      }),
    ).toBe(false);
    expect(
      photoHelperIsEnabled({
        name: "Hinterland",
        env: "dev",
        release_lane: "local",
        version: "test",
      }),
    ).toBe(false);
    expect(photoHelperIsEnabled(null)).toBe(false);
  });
});
