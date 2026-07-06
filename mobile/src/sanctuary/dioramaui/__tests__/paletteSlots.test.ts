import { SANCTUARY_PALETTE_SLOTS } from "@/src/sanctuary/art/islandLayers.gen";
import { scenePalette } from "@/src/sanctuary/diorama/season/palette";
import {
  paletteSlotHexes,
  satMatrix,
} from "@/src/sanctuary/dioramaui/paletteSlots";

const SEASONS = ["spring", "summer", "autumn", "winter"] as const;

describe("paletteSlotHexes", () => {
  it.each(SEASONS)("maps every slot to a valid hex for %s", (season) => {
    const hexes = paletteSlotHexes(scenePalette(season, "fresh"));
    for (const slot of SANCTUARY_PALETTE_SLOTS) {
      expect(hexes[slot]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }
    expect(Object.keys(hexes)).toHaveLength(SANCTUARY_PALETTE_SLOTS.length);
  });

  it("tracks the ScenePalette sources the art header names", () => {
    const palette = scenePalette("autumn", "fading");
    const hexes = paletteSlotHexes(palette);
    expect(hexes.horizon).toBe(palette.horizon);
    expect(hexes.glow).toBe(palette.sunColor);
    expect(hexes.green_mid).toBe(palette.ground);
  });

  it("changes with the season (token remap, not duplicate art)", () => {
    const spring = paletteSlotHexes(scenePalette("spring", "fresh"));
    const winter = paletteSlotHexes(scenePalette("winter", "still"));
    expect(spring.green_mid).not.toBe(winter.green_mid);
    expect(spring.horizon).not.toBe(winter.horizon);
  });
});

describe("satMatrix", () => {
  it("is the exact identity at s = 1", () => {
    expect(satMatrix(1)).toEqual([
      1, 0, 0, 0, 0,
      0, 1, 0, 0, 0,
      0, 0, 1, 0, 0,
      0, 0, 0, 1, 0,
    ]);
  });

  it("is a 4x5 matrix whose color rows stay luminance-preserving", () => {
    const m = satMatrix(0.12);
    expect(m).toHaveLength(20);
    for (const row of [0, 1, 2]) {
      const sum = m[row * 5] + m[row * 5 + 1] + m[row * 5 + 2];
      expect(sum).toBeCloseTo(1, 10);
    }
    // Alpha row untouched.
    expect(m.slice(15)).toEqual([0, 0, 0, 1, 0]);
  });

  it("applies the warm bias only when desaturated", () => {
    const dormant = satMatrix(0.12);
    expect(dormant[4]).toBeGreaterThan(0); // red offset warms
    expect(dormant[14]).toBeLessThan(0); // blue offset cools away
    expect(satMatrix(1)[4]).toBe(0);
  });
});
