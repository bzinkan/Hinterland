import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import {
  DEFAULT_DIVE_SCALE,
  vistaFraming,
  zoneFraming,
} from "@/src/sanctuary/diorama/framing";
import { ISLAND_SLOTS, VISTA_CANVAS } from "@/src/sanctuary/diorama/vistaLayout";

describe("vistaFraming", () => {
  it("centers the canvas at 1:1 zoom", () => {
    expect(vistaFraming()).toEqual({
      x: VISTA_CANVAS.width / 2,
      y: VISTA_CANVAS.height / 2,
      scale: 1,
    });
  });
});

describe("zoneFraming", () => {
  it.each(SANCTUARY_ZONE_ORDER)("returns finite framing for %s", (zoneId) => {
    const framing = zoneFraming(zoneId);
    expect(Number.isFinite(framing.x)).toBe(true);
    expect(Number.isFinite(framing.y)).toBe(true);
    expect(Number.isFinite(framing.scale)).toBe(true);
    expect(framing.scale).toBeGreaterThan(1);
  });

  it.each(SANCTUARY_ZONE_ORDER)("centers the %s island slot", (zoneId) => {
    const framing = zoneFraming(zoneId);
    expect(framing.x).toBe(ISLAND_SLOTS[zoneId].x);
    expect(framing.y).toBe(ISLAND_SLOTS[zoneId].y);
  });

  it("uses the default dive zoom unless a zone overrides it", () => {
    expect(DEFAULT_DIVE_SCALE).toBe(2.4);
    expect(zoneFraming("meadow").scale).toBe(DEFAULT_DIVE_SCALE);
    expect(zoneFraming("pond").scale).toBe(DEFAULT_DIVE_SCALE);
    // The small elsewhere islet leans in harder.
    expect(zoneFraming("elsewhere").scale).toBeGreaterThan(DEFAULT_DIVE_SCALE);
  });
});
