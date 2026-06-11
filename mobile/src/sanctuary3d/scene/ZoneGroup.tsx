/**
 * One zone of the island: ground patch (awake color vs dormant grey),
 * placed elements, and the mystery silhouette when cued. Geometry is the
 * M1 placeholder treatment until the authored island mesh lands (A3) --
 * the ZonePlan contract this renders from is final.
 */

import React from "react";

import type { SanctuaryElementDto } from "@/src/api/sanctuary";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import type { ZonePlan } from "@/src/sanctuary3d/scenePlan";
import { ElementModel } from "@/src/sanctuary3d/scene/ElementModel";
import { MysterySilhouette } from "@/src/sanctuary3d/scene/MysterySilhouette";
import {
  DORMANT_COLOR,
  ZONE_PLACEHOLDER_COLOR,
} from "@/src/sanctuary3d/scene/zoneColors";

export function ZoneGroup({
  plan,
  groundColor,
  onInspect,
}: {
  plan: ZonePlan;
  groundColor: string;
  onInspect: (element: SanctuaryElementDto) => void;
}) {
  const layout = ZONE_LAYOUT[plan.zoneId];
  const awake = plan.unlocked;
  const color = awake
    ? (ZONE_PLACEHOLDER_COLOR[plan.zoneId] ?? groundColor)
    : DORMANT_COLOR;
  const [cx, cy, cz] = layout.center;

  return (
    <group>
      <ZonePatch
        zoneId={plan.zoneId}
        color={color}
        center={[cx, cy, cz]}
        radius={layout.radius}
        awake={awake}
      />
      {plan.elements.map((placed) => (
        <ElementModel
          key={placed.element.element_id}
          placed={placed}
          onInspect={onInspect}
        />
      ))}
      {plan.silhouette ? <MysterySilhouette zoneId={plan.zoneId} /> : null}
    </group>
  );
}

function ZonePatch({
  zoneId,
  color,
  center,
  radius,
  awake,
}: {
  zoneId: string;
  color: string;
  center: readonly [number, number, number];
  radius: number;
  awake: boolean;
}) {
  const [cx, cy, cz] = center;
  if (zoneId === "sky") {
    // Sky zone: a cloud puff overhead once awake; nothing while dormant
    // (an empty sky reads fine -- the silhouette covers the cue case).
    return awake ? (
      <mesh position={[cx, cy, cz]}>
        <sphereGeometry args={[0.6, 10, 8]} />
        <meshLambertMaterial color={color} />
      </mesh>
    ) : null;
  }
  if (zoneId === "elsewhere") {
    // Detached floating islet.
    return (
      <mesh position={[cx, cy, cz]}>
        <cylinderGeometry args={[radius, radius * 0.55, 0.5, 7]} />
        <meshLambertMaterial color={color} />
      </mesh>
    );
  }
  if (zoneId === "soil") {
    // Front cliff cross-section panel.
    return (
      <mesh position={[cx, cy, cz]} rotation={[-0.32, 0, 0]}>
        <boxGeometry args={[radius * 2, 1.1, 0.2]} />
        <meshLambertMaterial color={color} />
      </mesh>
    );
  }
  // Ground zones: a slightly raised patch at the zone center.
  return (
    <mesh position={[cx, 0.02, cz]} rotation={[-Math.PI / 2, 0, 0]}>
      <circleGeometry args={[radius, 16]} />
      <meshLambertMaterial color={color} />
    </mesh>
  );
}
