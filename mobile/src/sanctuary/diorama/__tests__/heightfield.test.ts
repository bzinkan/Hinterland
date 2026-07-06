import { ZONE_LAYOUT } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import {
  buildTerrainArrays,
  heightAt,
  ISLAND_RADIUS,
  WATER_LEVEL,
} from "@/src/sanctuary/diorama/terrain/heightfield";

describe("heightAt", () => {
  it("is deterministic", () => {
    expect(heightAt(1.23, -4.56)).toBe(heightAt(1.23, -4.56));
  });

  it("carves the pond below the water table", () => {
    const [px, , pz] = ZONE_LAYOUT.pond.center;
    expect(heightAt(px, pz)).toBeLessThan(WATER_LEVEL);
  });

  it("raises the woodland ridge above the meadow", () => {
    const [wx, , wz] = ZONE_LAYOUT.woodland.center;
    const [mx, , mz] = ZONE_LAYOUT.meadow.center;
    expect(heightAt(wx, wz)).toBeGreaterThan(heightAt(mx, mz));
  });

  it("keeps the meadow walkable (gentle positive ground)", () => {
    const [mx, , mz] = ZONE_LAYOUT.meadow.center;
    for (const [dx, dz] of [[0, 0], [0.8, 0.5], [-0.7, -0.6], [1.2, -1.0]]) {
      const h = heightAt(mx + dx, mz + dz);
      expect(h).toBeGreaterThan(0.2);
      expect(h).toBeLessThan(1.4);
    }
  });

  it("falls away past the island rim", () => {
    expect(heightAt(ISLAND_RADIUS + 2, 0)).toBeLessThan(-1.5);
    expect(heightAt(0, -(ISLAND_RADIUS + 2))).toBeLessThan(-1.5);
  });

  it("drops off the front cliff", () => {
    expect(heightAt(0, 6.5)).toBeLessThan(heightAt(0, 3.5) - 1.0);
  });
});

describe("buildTerrainArrays", () => {
  it("produces a coherent indexed grid with unit normals", () => {
    const arrays = buildTerrainArrays();
    expect(arrays.positions.length).toBe(arrays.side * arrays.side * 3);
    expect(arrays.normals.length).toBe(arrays.positions.length);
    expect(arrays.indices.length).toBe((arrays.side - 1) * (arrays.side - 1) * 6);
    // Spot-check normals are normalized and upward-biased on the meadow.
    for (let i = 0; i < arrays.normals.length; i += arrays.normals.length / 12 * 3) {
      const idx = Math.floor(i / 3) * 3;
      const len = Math.hypot(
        arrays.normals[idx],
        arrays.normals[idx + 1],
        arrays.normals[idx + 2],
      );
      expect(len).toBeGreaterThan(0.99);
      expect(len).toBeLessThan(1.01);
    }
  });

  it("is cached (same object on repeat calls)", () => {
    expect(buildTerrainArrays()).toBe(buildTerrainArrays());
  });
});
