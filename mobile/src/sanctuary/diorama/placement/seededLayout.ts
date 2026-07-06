/**
 * Deterministic element placement (docs/sanctuary.md §10: deterministic
 * scene; no Math.random in the render path).
 *
 * Same (zone, element_id, index, count) always yields the same transform:
 * elements sit on a golden-angle spiral inside the zone footprint (even
 * spacing without collision checks) with a small hash-seeded jitter so the
 * arrangement reads organic, not gridded. A new unlock changes only its own
 * slot; existing inhabitants never move ("my monarch lives there").
 *
 * Ground-zone Y comes from the terrain heightfield so elements stand ON
 * the sculpted island; sky/elsewhere/soil keep their authored heights.
 *
 * Pure data + math -- no renderer imports, fully unit-testable.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { fnv1a32, mulberry32 } from "@/src/sanctuary/diorama/placement/seeds";
import { ZONE_LAYOUT, type Vec3 } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import { heightAt } from "@/src/sanctuary/diorama/terrain/heightfield";

export { fnv1a32, mulberry32 } from "@/src/sanctuary/diorama/placement/seeds";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // ~2.39996 rad

/** Zones whose elements stand on the sculpted terrain surface. */
const TERRAIN_ZONES = new Set<SanctuaryZoneId>([
  "meadow",
  "woodland",
  "pond",
  "urban",
]);

export type ElementTransform = {
  position: Vec3;
  rotationY: number;
  /** Visual-variety scale in [0.9, 1.1] (semantic scale belongs to the manifest). */
  scale: number;
};

/**
 * Place element `index` of `count` in a zone. Sorting the zone's elements
 * by element_id BEFORE calling keeps (index -> element) stable for a given
 * set; the per-element hash jitter keeps a slot stable even if the set
 * around it grows.
 */
export function placeElement(
  zoneId: SanctuaryZoneId,
  elementId: string,
  index: number,
  count: number,
): ElementTransform {
  const layout = ZONE_LAYOUT[zoneId];
  const rng = mulberry32(fnv1a32(`${zoneId}:${elementId}`));

  // Golden-angle spiral: radius grows with sqrt(index) for even density,
  // capped inside the zone footprint with a margin for the sprite itself.
  const usable = layout.radius * 0.8;
  const ringRadius =
    count <= 1 ? 0 : usable * Math.sqrt((index + 0.5) / count);
  const angle = index * GOLDEN_ANGLE + rng() * 0.5;

  // Hash jitter: small offset so the spiral doesn't read as a pattern.
  const jitterR = rng() * layout.radius * 0.12;
  const jitterA = rng() * Math.PI * 2;

  let dx = Math.cos(angle) * ringRadius + Math.cos(jitterA) * jitterR;
  let dz = Math.sin(angle) * ringRadius + Math.sin(jitterA) * jitterR;

  // The soil zone is a cliff cross-section: spread along the cliff (x),
  // stay tight front-to-back (z).
  if (zoneId === "soil") {
    dz *= 0.15;
  }

  const [cx, cy, cz] = layout.center;
  const x = cx + dx;
  const z = cz + dz;
  const y = TERRAIN_ZONES.has(zoneId) ? heightAt(x, z) : cy;

  return {
    position: [x, y, z],
    rotationY: rng() * Math.PI * 2,
    scale: 0.9 + rng() * 0.2,
  };
}
