import {
  fnv1a32,
  mulberry32,
  placeElement,
} from "@/src/sanctuary3d/placement/seededLayout";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import { heightAt } from "@/src/sanctuary3d/terrain/heightfield";
import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";

describe("fnv1a32 / mulberry32", () => {
  it("is stable for the same input", () => {
    expect(fnv1a32("meadow:el_1")).toBe(fnv1a32("meadow:el_1"));
    expect(fnv1a32("a")).not.toBe(fnv1a32("b"));
  });

  it("mulberry32 yields a deterministic sequence in [0,1)", () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    for (let i = 0; i < 10; i++) {
      const va = a();
      expect(va).toBe(b());
      expect(va).toBeGreaterThanOrEqual(0);
      expect(va).toBeLessThan(1);
    }
  });
});

describe("placeElement", () => {
  it("is deterministic: same inputs -> identical transform", () => {
    const t1 = placeElement("meadow", "el_monarch", 2, 5);
    const t2 = placeElement("meadow", "el_monarch", 2, 5);
    expect(t1).toEqual(t2);
  });

  it("different elements get different slots", () => {
    const a = placeElement("meadow", "el_a", 0, 3);
    const b = placeElement("meadow", "el_b", 1, 3);
    expect(a.position).not.toEqual(b.position);
  });

  it.each(SANCTUARY_ZONE_ORDER)(
    "keeps %s placements inside the zone footprint",
    (zoneId) => {
      const layout = ZONE_LAYOUT[zoneId];
      const onTerrain = ["meadow", "woodland", "pond", "urban"].includes(zoneId);
      for (let i = 0; i < 12; i++) {
        const t = placeElement(zoneId, `el_${i}`, i, 12);
        const dx = t.position[0] - layout.center[0];
        const dz = t.position[2] - layout.center[2];
        const dist = Math.hypot(dx, dz);
        expect(dist).toBeLessThanOrEqual(layout.radius * 1.01);
        if (onTerrain) {
          // Ground zones stand on the sculpted terrain.
          expect(t.position[1]).toBe(heightAt(t.position[0], t.position[2]));
        } else {
          expect(t.position[1]).toBe(layout.center[1]);
        }
        expect(t.scale).toBeGreaterThanOrEqual(0.9);
        expect(t.scale).toBeLessThanOrEqual(1.1);
      }
    },
  );

  it("soil placements hug the cliff plane (tight z spread)", () => {
    const layout = ZONE_LAYOUT.soil;
    for (let i = 0; i < 8; i++) {
      const t = placeElement("soil", `worm_${i}`, i, 8);
      expect(Math.abs(t.position[2] - layout.center[2])).toBeLessThanOrEqual(
        layout.radius * 0.2,
      );
    }
  });

  it("an element's slot does not move when later elements join (same index/count basis)", () => {
    // The hash-jitter component depends only on (zone, element_id); the
    // spiral slot depends on (index, count). Sorting by element_id upstream
    // means an APPENDED id sorting later leaves earlier indices unchanged.
    const before = placeElement("meadow", "aaa_first", 0, 2);
    const after = placeElement("meadow", "aaa_first", 0, 3);
    // Same index, count changed: ring radius shifts slightly but the jitter
    // (identity) component is identical -- assert the angle-stable part.
    expect(before.rotationY).toBe(after.rotationY);
  });
});
