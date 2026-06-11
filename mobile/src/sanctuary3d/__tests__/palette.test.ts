import type { SanctuarySeason } from "@/src/api/sanctuary";
import { scenePalette } from "@/src/sanctuary3d/season/palette";

const SEASONS: SanctuarySeason[] = ["spring", "summer", "autumn", "winter"];
const KNOWN_TONES = ["fresh", "warm", "fading", "still"];
const HEX = /^#[0-9A-Fa-f]{6}$/;

describe("scenePalette", () => {
  it.each(SEASONS)("returns a complete palette for %s with any tone", (season) => {
    for (const tone of [...KNOWN_TONES, "unknown-tone", ""]) {
      const p = scenePalette(season, tone);
      expect(p.skyTop).toMatch(HEX);
      expect(p.horizon).toMatch(HEX);
      expect(p.fog).toMatch(HEX);
      expect(p.hemiSky).toMatch(HEX);
      expect(p.hemiGround).toMatch(HEX);
      expect(p.sunColor).toMatch(HEX);
      expect(p.ground).toMatch(HEX);
      expect(p.sunIntensity).toBeGreaterThan(0);
    }
  });

  it("unknown tones fall back to the season base unchanged", () => {
    const base = scenePalette("spring", "definitely-not-a-tone");
    const fresh = scenePalette("spring", "fresh");
    expect(base.skyTop).toBe(scenePalette("spring", "").skyTop);
    expect(fresh.sunIntensity).not.toBe(base.sunIntensity);
  });

  it("seasons are visually distinct (different skies)", () => {
    const skies = new Set(SEASONS.map((s) => scenePalette(s, "").skyTop));
    expect(skies.size).toBe(SEASONS.length);
  });

  it("tone overrides apply on top of the season base", () => {
    const still = scenePalette("summer", "still");
    const base = scenePalette("summer", "");
    expect(still.sunIntensity).toBeLessThan(base.sunIntensity);
    expect(still.ground).toBe(base.ground); // untouched fields pass through
  });
});
