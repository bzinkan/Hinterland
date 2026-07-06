import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import {
  ISLAND_SLOTS,
  PARALLAX_FACTOR,
  parallaxFor,
  VISTA_CANVAS,
} from "@/src/sanctuary/diorama/vistaLayout";

describe("vista layout", () => {
  it("defines a slot for all 7 zones", () => {
    expect(Object.keys(ISLAND_SLOTS).sort()).toEqual(
      [...SANCTUARY_ZONE_ORDER].sort(),
    );
  });

  it("is a 2.5-screen-wide canvas in 390dp reference units", () => {
    expect(VISTA_CANVAS.width).toBe(975);
    expect(VISTA_CANVAS.height).toBe(780);
  });

  it.each(SANCTUARY_ZONE_ORDER)("keeps the %s slot on the canvas", (zoneId) => {
    const slot = ISLAND_SLOTS[zoneId];
    expect(slot.x).toBeGreaterThanOrEqual(0);
    expect(slot.x).toBeLessThanOrEqual(VISTA_CANVAS.width);
    expect(slot.y).toBeGreaterThanOrEqual(0);
    expect(slot.y).toBeLessThanOrEqual(VISTA_CANVAS.height);
    expect(slot.islandScale).toBeGreaterThan(0);
  });

  it.each(SANCTUARY_ZONE_ORDER)("gives %s a valid band", (zoneId) => {
    expect(["back", "mid", "fore"]).toContain(ISLAND_SLOTS[zoneId].band);
  });

  it("composes the archipelago per the plan", () => {
    expect(ISLAND_SLOTS.meadow.band).toBe("fore");
    expect(ISLAND_SLOTS.woodland.band).toBe("back");
    expect(ISLAND_SLOTS.pond.band).toBe("fore");
    expect(ISLAND_SLOTS.sky.band).toBe("mid");
    expect(ISLAND_SLOTS.soil.band).toBe("fore");
    expect(ISLAND_SLOTS.urban.band).toBe("mid");
    expect(ISLAND_SLOTS.elsewhere.band).toBe("back");
    // Elsewhere is the small far islet.
    expect(ISLAND_SLOTS.elsewhere.islandScale).toBeLessThan(
      ISLAND_SLOTS.meadow.islandScale,
    );
  });

  it("parallax factors match the authored bands", () => {
    expect(PARALLAX_FACTOR.back).toBe(0.35);
    expect(PARALLAX_FACTOR.mid).toBe(0.7);
    expect(PARALLAX_FACTOR.fore).toBe(1.0);
    expect(PARALLAX_FACTOR.sky).toBe(0.1);
  });

  it("parallaxFor follows the band except for the sky island", () => {
    expect(parallaxFor("meadow")).toBe(1.0);
    expect(parallaxFor("woodland")).toBe(0.35);
    expect(parallaxFor("urban")).toBe(0.7);
    expect(parallaxFor("sky")).toBe(0.1);
  });
});
