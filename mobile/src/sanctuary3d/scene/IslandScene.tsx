/**
 * The whole island, composed from a ScenePlan: gradient sky, sculpted
 * terrain (vertex-painted by zone state), wind-swept grass, the zones'
 * inhabitants, and the rocky underside that sells "floating island".
 * Pure render of plan data -- all decisions were made in scenePlan.ts.
 */

import React from "react";

import type { SanctuaryElementDto, SanctuaryZoneId } from "@/src/api/sanctuary";
import type { ScenePlan } from "@/src/sanctuary3d/scenePlan";
import { GrassField } from "@/src/sanctuary3d/scene/GrassField";
import { IslandDressing } from "@/src/sanctuary3d/scene/IslandDressing";
import { SkyDome } from "@/src/sanctuary3d/scene/SkyDome";
import { TerrainMesh } from "@/src/sanctuary3d/scene/TerrainMesh";
import { ZoneGroup } from "@/src/sanctuary3d/scene/ZoneGroup";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";

export function IslandScene({
  plan,
  onInspect,
  onFocusZone,
}: {
  plan: ScenePlan;
  onInspect: (element: SanctuaryElementDto) => void;
  onFocusZone: (zone: SanctuaryZoneId) => void;
}) {
  const meadowDormant =
    plan.isInvitationState ||
    !(plan.zones.find((z) => z.zoneId === "meadow")?.unlocked ?? false);

  return (
    <group>
      <SkyDome topColor={plan.palette.skyTop} horizonColor={plan.palette.horizon} />
      <TerrainMesh plan={plan} />
      <GrassField dormant={meadowDormant} />
      <IslandDressing plan={plan} />
      {/* Rocky underside taper: closes the floating-island silhouette.
          Kept tight and shadow-dark so it never competes with the frame. */}
      <mesh position={[0, -3.9, 0]}>
        <coneGeometry args={[6.2, 3.0, 9]} />
        <meshToonMaterial color="#574C40" gradientMap={toonRamp()} />
      </mesh>
      {plan.zones.map((zone) => (
        <ZoneGroup
          key={zone.zoneId}
          plan={zone}
          onInspect={onInspect}
          onFocusZone={onFocusZone}
        />
      ))}
    </group>
  );
}
