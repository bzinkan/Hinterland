/**
 * One zone of the island: its placed elements, the mystery silhouette when
 * cued, special geometry for the off-terrain zones (sky puff, elsewhere
 * islet, soil cliff panel), and an invisible tap disc that dives the
 * camera into the zone. Ground coloring itself lives in the terrain's
 * vertex paint (terrainColors.ts) -- awake/dormant is painted, not patched.
 */

import React from "react";

import type { SanctuaryElementDto, SanctuaryZoneId } from "@/src/api/sanctuary";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import type { ZonePlan } from "@/src/sanctuary3d/scenePlan";
import { ElementModel } from "@/src/sanctuary3d/scene/ElementModel";
import { MysterySilhouette } from "@/src/sanctuary3d/scene/MysterySilhouette";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";
import {
  DORMANT_COLOR,
  ZONE_PLACEHOLDER_COLOR,
} from "@/src/sanctuary3d/scene/zoneColors";
import { heightAt } from "@/src/sanctuary3d/terrain/heightfield";

const TERRAIN_ZONES = new Set<SanctuaryZoneId>([
  "meadow",
  "woodland",
  "pond",
  "urban",
]);

export function ZoneGroup({
  plan,
  onInspect,
  onFocusZone,
}: {
  plan: ZonePlan;
  onInspect: (element: SanctuaryElementDto) => void;
  onFocusZone: (zone: SanctuaryZoneId) => void;
}) {
  const layout = ZONE_LAYOUT[plan.zoneId];
  const awake = plan.unlocked;
  const color = awake
    ? (ZONE_PLACEHOLDER_COLOR[plan.zoneId] ?? DORMANT_COLOR)
    : DORMANT_COLOR;
  const [cx, cy, cz] = layout.center;
  const focus = () => onFocusZone(plan.zoneId);

  return (
    <group>
      {/* Off-terrain zone geometry. */}
      {plan.zoneId === "sky" && awake ? (
        <mesh position={[cx, cy, cz]} onClick={(e) => { e.stopPropagation(); focus(); }}>
          <sphereGeometry args={[0.6, 10, 8]} />
          <meshToonMaterial color="#F4F7F8" gradientMap={toonRamp()} />
        </mesh>
      ) : null}
      {plan.zoneId === "elsewhere" ? (
        <mesh position={[cx, cy, cz]} onClick={(e) => { e.stopPropagation(); focus(); }}>
          <cylinderGeometry args={[layout.radius, layout.radius * 0.55, 0.5, 7]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      ) : null}
      {plan.zoneId === "soil" ? (
        <mesh
          position={[cx, cy, cz]}
          rotation={[-0.18, 0, 0]}
          onClick={(e) => { e.stopPropagation(); focus(); }}
        >
          <boxGeometry args={[layout.radius * 2, 1.2, 0.25]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      ) : null}

      {/* Invisible camera-dive tap disc on the terrain zones. */}
      {TERRAIN_ZONES.has(plan.zoneId) ? (
        <mesh
          position={[cx, heightAt(cx, cz) + 0.05, cz]}
          rotation={[-Math.PI / 2, 0, 0]}
          onClick={(e) => {
            e.stopPropagation();
            focus();
          }}
        >
          <circleGeometry args={[layout.radius, 14]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      ) : null}

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
