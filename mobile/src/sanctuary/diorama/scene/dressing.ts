/**
 * Island dressing: seeded scatter rules that place vegetation/rock sprites
 * (sprite-manifest scenery entries) onto the terrain. This is what turns
 * "flat shapes on a hill" into a place -- many small overlapping props
 * following simple ecological rules.
 *
 * Pure and deterministic (computed once at module load): same build,
 * same island, every visit. Tier gating happens at plan time against each
 * rule's `tierMin` (mirrored from the sprite manifest until the atlas
 * lands) -- the transforms here are the full dressed set.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { fnv1a32, mulberry32 } from "@/src/sanctuary/diorama/placement/seeds";
import { ZONE_LAYOUT } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import type { ElementTransform } from "@/src/sanctuary/diorama/placement/seededLayout";
import { heightAt, ISLAND_RADIUS, WATER_LEVEL } from "@/src/sanctuary/diorama/terrain/heightfield";

export type DressingRule = {
  /** Sprite-manifest scenery key (assets.json entry name). */
  key: string;
  /** Zone footprint to scatter in, or "shore" = the island's rim ring. */
  zone: SanctuaryZoneId | "shore";
  count: number;
  /**
   * Renders once the zone's depth_tier reaches this threshold value.
   * Mirrors the sprite manifest's `tierMin`; lives on the rule until the
   * generated atlas (sprites.gen.ts) carries it again.
   */
  tierMin: number;
  /** Acceptable terrain height band. */
  minH: number;
  maxH: number;
  /** Per-instance scale variance range. */
  scale: [number, number];
};

export const DRESSING_RULES: DressingRule[] = [
  // Woodland ridge: a real treeline (mixed deciduous + pine).
  { key: "tree-default", zone: "woodland", count: 7, tierMin: 1, minH: 0.4, maxH: 2.4, scale: [0.85, 1.2] },
  { key: "tree-oak", zone: "woodland", count: 5, tierMin: 1, minH: 0.4, maxH: 2.4, scale: [0.9, 1.3] },
  { key: "tree-thin", zone: "woodland", count: 4, tierMin: 1, minH: 0.4, maxH: 2.4, scale: [0.8, 1.1] },
  { key: "pine-round", zone: "woodland", count: 5, tierMin: 1, minH: 0.6, maxH: 2.6, scale: [0.85, 1.15] },
  { key: "tree-detailed", zone: "woodland", count: 4, tierMin: 3, minH: 0.4, maxH: 2.4, scale: [0.9, 1.2] },
  { key: "pine-tall", zone: "woodland", count: 4, tierMin: 3, minH: 0.8, maxH: 2.8, scale: [0.9, 1.25] },
  { key: "stump-round", zone: "woodland", count: 2, tierMin: 3, minH: 0.4, maxH: 2.0, scale: [0.9, 1.1] },
  { key: "mushroom-group", zone: "woodland", count: 3, tierMin: 5, minH: 0.4, maxH: 1.8, scale: [0.8, 1.1] },
  // Meadow accents: bushes always, flower drifts as the zone deepens.
  { key: "bush", zone: "meadow", count: 4, tierMin: 1, minH: 0.25, maxH: 1.2, scale: [0.8, 1.2] },
  { key: "bush-large", zone: "meadow", count: 3, tierMin: 3, minH: 0.25, maxH: 1.2, scale: [0.85, 1.15] },
  { key: "flower-purple", zone: "meadow", count: 9, tierMin: 3, minH: 0.3, maxH: 1.1, scale: [0.8, 1.25] },
  { key: "flower-yellow", zone: "meadow", count: 9, tierMin: 3, minH: 0.3, maxH: 1.1, scale: [0.8, 1.25] },
  { key: "flower-red", zone: "meadow", count: 7, tierMin: 5, minH: 0.3, maxH: 1.1, scale: [0.8, 1.25] },
  // Shore geology: rocks on the rim ring, present from day one.
  { key: "rock-large-a", zone: "shore", count: 4, tierMin: 1, minH: -0.4, maxH: 1.6, scale: [0.8, 1.4] },
  { key: "rock-large-c", zone: "shore", count: 3, tierMin: 1, minH: -0.4, maxH: 1.6, scale: [0.8, 1.3] },
  { key: "rock-tall-b", zone: "shore", count: 3, tierMin: 1, minH: -0.2, maxH: 1.6, scale: [0.8, 1.2] },
  { key: "rock-small-a", zone: "shore", count: 9, tierMin: 1, minH: -0.4, maxH: 1.8, scale: [0.7, 1.5] },
  { key: "rock-small-d", zone: "shore", count: 9, tierMin: 1, minH: -0.4, maxH: 1.8, scale: [0.7, 1.5] },
];

function scatterFor(rule: DressingRule): ElementTransform[] {
  const rng = mulberry32(fnv1a32(`dressing:${rule.key}`));
  const pond = ZONE_LAYOUT.pond;
  const out: ElementTransform[] = [];
  let attempts = 0;
  while (out.length < rule.count && attempts < rule.count * 40) {
    attempts++;
    let x: number;
    let z: number;
    if (rule.zone === "shore") {
      // Rim ring between the grass edge and the drop-off.
      const angle = rng() * Math.PI * 2;
      const radius = ISLAND_RADIUS * (0.62 + rng() * 0.16);
      x = Math.cos(angle) * radius;
      z = Math.sin(angle) * radius;
    } else {
      const layout = ZONE_LAYOUT[rule.zone];
      const angle = rng() * Math.PI * 2;
      const radius = layout.radius * Math.sqrt(rng()) * 1.15;
      x = layout.center[0] + Math.cos(angle) * radius;
      z = layout.center[2] + Math.sin(angle) * radius;
    }
    if (z > 3.7) continue; // cliff lip
    const y = heightAt(x, z);
    if (y < rule.minH || y > rule.maxH) continue;
    if (y < WATER_LEVEL + 0.1) continue;
    if (Math.hypot(x - pond.center[0], z - pond.center[2]) < pond.radius * 1.1) continue;
    out.push({
      position: [x, y, z],
      rotationY: rng() * Math.PI * 2,
      scale: rule.scale[0] + rng() * (rule.scale[1] - rule.scale[0]),
    });
  }
  return out;
}

/** Precomputed once: rule key -> transforms. Deterministic per build. */
export const DRESSING_TRANSFORMS: ReadonlyMap<string, ElementTransform[]> =
  new Map(DRESSING_RULES.map((rule) => [rule.key, scatterFor(rule)]));
