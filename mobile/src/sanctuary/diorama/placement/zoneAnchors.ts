/**
 * Island-space zone layout (ADR 0011 / docs/sanctuary.md §10 scene notes):
 * meadow front-left, woodland back, pond center-right, urban front-right,
 * soil = the visible cliff cross-section on the island's front face,
 * sky = above the island, elsewhere = a small detached floating islet.
 *
 * Units are meters in island space; the island base is ~10 m across,
 * centered at the origin, top surface at y = 0. These centers anchor the
 * placeholder zones and remain the zone origins once the authored island
 * art lands -- the island is drawn to fit this layout, not the other way
 * around. In the 2.5D diorama each zone becomes its own vista island; the
 * anchors keep per-zone placement math (spiral slots, jitter caps) stable.
 *
 * Pure data: no renderer imports.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";

export type Vec3 = readonly [number, number, number];

export type ZoneLayout = {
  center: Vec3;
  /** Approximate radius of the zone's footprint (for placement jitter caps). */
  radius: number;
};

export const ZONE_LAYOUT: Record<SanctuaryZoneId, ZoneLayout> = {
  meadow: { center: [-2.2, 0, 2.0], radius: 1.8 },
  woodland: { center: [0.4, 0, -2.4], radius: 2.2 },
  pond: { center: [2.4, 0, 0.4], radius: 1.4 },
  urban: { center: [-0.2, 0, 3.4], radius: 1.2 },
  // The soil cross-section lives on the island's front cliff face.
  soil: { center: [0, -1.2, 4.6], radius: 1.6 },
  // Sky elements orbit above the island center.
  sky: { center: [0, 4.2, 0], radius: 3.0 },
  // Detached dreamlike islet, off to the back-left, slowly bobbing.
  elsewhere: { center: [-5.4, 1.4, -3.6], radius: 0.9 },
};
