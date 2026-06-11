/**
 * Mystery cue rendered as a dark, faintly translucent shape at a dormant
 * zone's center -- "something lives here, come wake it." The cue's TEXT
 * stays in the Quiet Corners panel (docs/sanctuary.md §10: no answer
 * leakage on the model). Until zone art lands, the silhouette is a simple
 * per-zone form in the dormant palette.
 */

import React from "react";

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import { SILHOUETTE_COLOR } from "@/src/sanctuary3d/scene/zoneColors";
import { heightAt } from "@/src/sanctuary3d/terrain/heightfield";

const TERRAIN_ZONES = new Set<SanctuaryZoneId>([
  "meadow",
  "woodland",
  "pond",
  "urban",
]);

export function MysterySilhouette({ zoneId }: { zoneId: SanctuaryZoneId }) {
  const [cx, cy, cz] = ZONE_LAYOUT[zoneId].center;
  const y = TERRAIN_ZONES.has(zoneId) ? heightAt(cx, cz) : cy;
  return (
    <group position={[cx, y, cz]}>
      <mesh position={[0, 0.5, 0]}>
        {zoneId === "pond" ? (
          // Lily-pad disc for the pond...
          <cylinderGeometry args={[0.4, 0.4, 0.06, 12]} />
        ) : zoneId === "sky" ? (
          // ...cloud wisp for the sky...
          <sphereGeometry args={[0.4, 8, 6]} />
        ) : (
          // ...and a tall tapering form (grass/tree/post) everywhere else.
          <coneGeometry args={[0.28, 1.0, 7]} />
        )}
        <meshLambertMaterial color={SILHOUETTE_COLOR} transparent opacity={0.45} />
      </mesh>
    </group>
  );
}
