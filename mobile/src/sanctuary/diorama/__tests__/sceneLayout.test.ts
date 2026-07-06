import {
  BACKDROP_ASPECT,
  BAND_BOTTOM_FRACTION,
  bandDrawWidth,
  bandRect,
  GROUND_REGION,
  INTERIM_GROUND_TOP_FRACTION,
  interimIslandRect,
  SCENE_BANDS,
  SCENE_PARALLAX,
  SCENE_WIDTH_FACTOR,
  sceneMetrics,
} from "@/src/sanctuary/diorama/sceneLayout";

const VIEWPORTS = [
  [360, 740],
  [390, 800],
  [412, 892],
] as const;

describe("scene layout", () => {
  it("orders band parallax back-to-front (fore outruns the camera)", () => {
    expect(SCENE_PARALLAX.sky).toBeLessThan(SCENE_PARALLAX.far);
    expect(SCENE_PARALLAX.far).toBeLessThan(SCENE_PARALLAX.mid);
    expect(SCENE_PARALLAX.mid).toBeLessThan(SCENE_PARALLAX.ground);
    expect(SCENE_PARALLAX.ground).toBe(1);
    expect(SCENE_PARALLAX.fore).toBeGreaterThan(1);
  });

  it("makes the scene ~1.6 screens wide", () => {
    const m = sceneMetrics(390, 800);
    expect(m.sceneWidth).toBe(Math.round(390 * SCENE_WIDTH_FACTOR));
    expect(m.maxPan).toBe(m.sceneWidth - 390);
    expect(SCENE_WIDTH_FACTOR).toBeGreaterThanOrEqual(1.5);
    expect(SCENE_WIDTH_FACTOR).toBeLessThanOrEqual(1.7);
  });

  it.each(VIEWPORTS)(
    "covers the %dx%d viewport at both pan extremes for every band",
    (w, h) => {
      const m = sceneMetrics(w, h);
      for (const band of SCENE_BANDS) {
        const width = bandDrawWidth(m, band);
        const p = SCENE_PARALLAX[band];
        // pan 0: band spans [0, width] and width >= w.
        expect(width).toBeGreaterThanOrEqual(w);
        // pan maxPan: band spans [-maxPan*p, width - maxPan*p]; the right
        // edge must still reach the viewport's right edge.
        expect(width - m.maxPan * p).toBeGreaterThanOrEqual(w - 1e-9);
        // ...and the left edge must not have entered the viewport.
        expect(-m.maxPan * p).toBeLessThanOrEqual(0);
      }
    },
  );

  it("the ground band spans exactly the scene width", () => {
    const m = sceneMetrics(390, 800);
    expect(bandDrawWidth(m, "ground")).toBeCloseTo(m.sceneWidth, 6);
  });

  it("anchors band bottoms down-screen in painter order", () => {
    expect(BAND_BOTTOM_FRACTION.far).toBeLessThan(BAND_BOTTOM_FRACTION.mid);
    expect(BAND_BOTTOM_FRACTION.mid).toBeLessThan(BAND_BOTTOM_FRACTION.ground);
    // Ground + fore overshoot the screen bottom (no hairline gaps).
    expect(BAND_BOTTOM_FRACTION.ground).toBeGreaterThanOrEqual(1);
    expect(BAND_BOTTOM_FRACTION.fore).toBeGreaterThanOrEqual(1);
  });

  it.each(VIEWPORTS)("places band rects consistently on %dx%d", (w, h) => {
    const m = sceneMetrics(w, h);
    for (const band of SCENE_BANDS) {
      const r = bandRect(m, band);
      expect(r.x).toBe(0);
      expect(r.height).toBeCloseTo(r.width * BACKDROP_ASPECT, 6);
      expect(r.y + r.height).toBeCloseTo(h * BAND_BOTTOM_FRACTION[band], 6);
    }
  });

  it("keeps the ground sprite region on-screen and inside the ground band", () => {
    expect(GROUND_REGION.top).toBeGreaterThan(0);
    expect(GROUND_REGION.top).toBeLessThan(GROUND_REGION.bottom);
    expect(GROUND_REGION.bottom).toBeLessThan(1);
    for (const [w, h] of VIEWPORTS) {
      const m = sceneMetrics(w, h);
      const ground = bandRect(m, "ground");
      // Sprites must stand on painted ground, not float above its crest.
      expect(h * GROUND_REGION.top).toBeGreaterThanOrEqual(ground.y);
    }
  });

  it("centers the interim island in the scene above the ground gradient", () => {
    const m = sceneMetrics(390, 800);
    const r = interimIslandRect(m);
    expect(r.x + r.width / 2).toBeCloseTo(m.sceneWidth / 2, 6);
    expect(r.width).toBeLessThanOrEqual(m.w);
    // The island's base overlaps the interim ground so it reads seated.
    expect(r.y + r.height).toBeGreaterThan(m.h * INTERIM_GROUND_TOP_FRACTION);
    expect(r.y).toBeGreaterThan(0);
  });
});
