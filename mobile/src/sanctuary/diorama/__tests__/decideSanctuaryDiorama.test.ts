import {
  decideSanctuaryDiorama,
  MAX_RENDER_CRASHES,
} from "@/src/sanctuary/diorama/decideSanctuaryDiorama";

const ON = {
  buildFlagEnabled: true,
  screenReaderEnabled: false,
  simpleViewPreferred: false,
  crashCount: 0,
};

describe("decideSanctuaryDiorama", () => {
  it("renders the diorama when the build flag is on and nothing objects", () => {
    expect(decideSanctuaryDiorama(ON)).toBe(true);
  });

  it("is hard-gated by the build flag", () => {
    expect(decideSanctuaryDiorama({ ...ON, buildFlagEnabled: false })).toBe(false);
  });

  it("falls back to the classic screen for screen-reader users", () => {
    expect(decideSanctuaryDiorama({ ...ON, screenReaderEnabled: true })).toBe(false);
  });

  it("respects the Simple view preference", () => {
    expect(decideSanctuaryDiorama({ ...ON, simpleViewPreferred: true })).toBe(false);
  });

  it("pins the classic screen once the crash latch reaches the limit", () => {
    expect(decideSanctuaryDiorama({ ...ON, crashCount: MAX_RENDER_CRASHES - 1 })).toBe(true);
    expect(decideSanctuaryDiorama({ ...ON, crashCount: MAX_RENDER_CRASHES })).toBe(false);
    expect(decideSanctuaryDiorama({ ...ON, crashCount: MAX_RENDER_CRASHES + 5 })).toBe(false);
  });

  it("runtime conditions can only turn the diorama off, never on", () => {
    expect(
      decideSanctuaryDiorama({
        buildFlagEnabled: false,
        screenReaderEnabled: false,
        simpleViewPreferred: false,
        crashCount: 0,
      }),
    ).toBe(false);
  });
});
