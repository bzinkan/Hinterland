/**
 * Typed fallback geometry for elements whose icon key has no manifest model
 * yet (placeholders.json). A content/manifest gap must never crash or blank
 * the scene -- it renders as a small, deliberately simple shape in the
 * zone's accent color. As asset milestones land these disappear naturally.
 *
 * Shape language (kid-legible at a glance):
 *   coarse        -> low dome (a living cluster)
 *   charismatic   -> upright octahedron (someone special lives here)
 *   relationship  -> ring (two things connected)
 *   surprise      -> small tetrahedron (a found trinket)
 *   signature     -> tall cone (a landmark)
 */

import React from "react";

import type { SanctuaryElementType } from "@/src/api/sanctuary";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";

export function FallbackShape({
  elementType,
  color,
}: {
  elementType: SanctuaryElementType;
  color: string;
}) {
  switch (elementType) {
    case "charismatic":
      return (
        <mesh position={[0, 0.35, 0]}>
          <octahedronGeometry args={[0.28]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      );
    case "relationship":
      return (
        <mesh position={[0, 0.25, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.22, 0.07, 8, 16]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      );
    case "surprise":
      return (
        <mesh position={[0, 0.16, 0]}>
          <tetrahedronGeometry args={[0.2]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      );
    case "signature":
      return (
        <mesh position={[0, 0.45, 0]}>
          <coneGeometry args={[0.22, 0.9, 6]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      );
    case "coarse":
    default:
      return (
        <mesh position={[0, 0.12, 0]} scale={[1, 0.55, 1]}>
          <sphereGeometry args={[0.3, 10, 8]} />
          <meshToonMaterial color={color} gradientMap={toonRamp()} />
        </mesh>
      );
  }
}
