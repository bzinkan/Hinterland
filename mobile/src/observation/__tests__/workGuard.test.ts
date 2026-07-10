import { observationWorkIsCurrent } from "@/src/observation/workGuard";

describe("observation async work guard", () => {
  const expected = {
    ownerUserId: "kid-1",
    submissionKey: "01JTEST0000000000000000000",
    generation: 4,
    geohash4: "dr5r",
  };

  it("rejects late location work after an account switch", () => {
    expect(
      observationWorkIsCurrent(expected, {
        ...expected,
        ownerUserId: "kid-2",
      }),
    ).toBe(false);
  });

  it("rejects reverse-geocode work after No location changes generation", () => {
    expect(
      observationWorkIsCurrent(expected, {
        ...expected,
        generation: 5,
        geohash4: null,
      }),
    ).toBe(false);
  });

  it("rejects an aborted request even when the draft identity still matches", () => {
    const controller = new AbortController();
    controller.abort();
    expect(observationWorkIsCurrent(expected, expected, controller.signal)).toBe(false);
  });
});
