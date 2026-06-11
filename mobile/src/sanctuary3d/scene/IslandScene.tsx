/**
 * The whole island, composed from a ScenePlan. Pure render of plan data --
 * all decisions (what's awake, where things sit, what's silhouetted) were
 * made in scenePlan.ts.
 */

import React from "react";

import type { SanctuaryElementDto } from "@/src/api/sanctuary";
import type { ScenePlan } from "@/src/sanctuary3d/scenePlan";
import { ZoneGroup } from "@/src/sanctuary3d/scene/ZoneGroup";
import { DORMANT_COLOR } from "@/src/sanctuary3d/scene/zoneColors";

export function IslandScene({
  plan,
  onInspect,
}: {
  plan: ScenePlan;
  onInspect: (element: SanctuaryElementDto) => void;
}) {
  // Invitation state: the whole island sleeps in warm grey -- asleep, not
  // locked (no padlocks, no meters). Color floods in per zone as tiers wake.
  const baseColor = plan.isInvitationState ? DORMANT_COLOR : plan.palette.ground;

  return (
    <group>
      {/* Island base: low cylinder + rock taper so it reads as floating. */}
      <mesh position={[0, -0.6, 0]}>
        <cylinderGeometry args={[5.6, 4.2, 1.2, 9]} />
        <meshLambertMaterial color={baseColor} />
      </mesh>
      <mesh position={[0, -1.8, 0]}>
        <coneGeometry args={[4.2, 2.4, 9]} />
        <meshLambertMaterial color="#6B5B49" />
      </mesh>
      {plan.zones.map((zone) => (
        <ZoneGroup
          key={zone.zoneId}
          plan={zone}
          groundColor={baseColor}
          onInspect={onInspect}
        />
      ))}
    </group>
  );
}
