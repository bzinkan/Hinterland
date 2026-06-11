/**
 * The sculpted island mesh. Geometry is built once from the deterministic
 * heightfield; vertex colors repaint when zone states change (the "color
 * floods in" mechanic). Toon-ramp shading + scene fog do the painterly
 * lifting; no textures.
 *
 * Also renders the pond water table: a flat toon-blue disc at WATER_LEVEL
 * sitting in the carved basin (shimmer animation lands in M3).
 */

import React, { useEffect, useMemo } from "react";
import * as THREE from "three";

import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import type { ScenePlan } from "@/src/sanctuary3d/scenePlan";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";
import {
  buildTerrainArrays,
  WATER_LEVEL,
} from "@/src/sanctuary3d/terrain/heightfield";
import { buildTerrainColors } from "@/src/sanctuary3d/terrain/terrainColors";

export function TerrainMesh({ plan }: { plan: ScenePlan }) {
  const arrays = useMemo(() => buildTerrainArrays(), []);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(arrays.positions, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(arrays.normals, 3));
    geo.setIndex(new THREE.BufferAttribute(arrays.indices, 1));
    // Color attribute allocated once; painted in the effect below.
    geo.setAttribute(
      "color",
      new THREE.BufferAttribute(new Float32Array(arrays.positions.length), 3),
    );
    return geo;
  }, [arrays]);

  // Repaint vertex colors when zone states change.
  const zoneStateKey = plan.zones
    .map((z) => `${z.zoneId}:${z.unlocked ? 1 : 0}`)
    .join(",");
  useEffect(() => {
    const colors = buildTerrainColors(
      arrays,
      plan.zones.map((z) => ({ zoneId: z.zoneId, unlocked: z.unlocked })),
      plan.isInvitationState,
    );
    const attribute = geometry.getAttribute("color") as THREE.BufferAttribute;
    (attribute.array as Float32Array).set(colors);
    attribute.needsUpdate = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneStateKey, plan.isInvitationState, geometry, arrays]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  const pond = ZONE_LAYOUT.pond.center;

  return (
    <group>
      <mesh geometry={geometry}>
        <meshToonMaterial vertexColors gradientMap={toonRamp()} />
      </mesh>
      {/* Pond water table. */}
      <mesh
        position={[pond[0], WATER_LEVEL, pond[2]]}
        rotation={[-Math.PI / 2, 0, 0]}
      >
        <circleGeometry args={[ZONE_LAYOUT.pond.radius * 1.05, 24]} />
        <meshBasicMaterial color="#7FB8C4" transparent opacity={0.9} />
      </mesh>
    </group>
  );
}
