/**
 * Authored camera framings (L1 look-dev): the default low vista and one
 * dive-in view per zone. Heights are sampled off the terrain so the camera
 * always hovers a believable eye-height above the ground. Pure data.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import type { Vec3 } from "@/src/sanctuary3d/placement/zoneAnchors";
import { heightAt } from "@/src/sanctuary3d/terrain/heightfield";

export type CameraView = {
  position: Vec3;
  lookAt: Vec3;
};

function ground(x: number, z: number, lift: number): Vec3 {
  return [x, Math.max(heightAt(x, z), -0.2) + lift, z];
}

/** Default framing: hovering just off the island's southwest shoulder,
 * meadow in the foreground, woodland ridge mid-frame, sky and horizon
 * haze above -- the ridge-top vista. */
export const VISTA_VIEW: CameraView = {
  position: [-8.6, 3.1, 10.4],
  lookAt: [1.4, 0.3, -2.6],
};

export const ZONE_VIEWS: Record<SanctuaryZoneId, CameraView> = {
  meadow: {
    position: ground(-4.4, 4.6, 1.0),
    lookAt: ground(-2.0, 1.2, 0.4),
  },
  woodland: {
    position: ground(-1.0, 0.8, 1.3),
    lookAt: ground(0.6, -2.8, 0.9),
  },
  pond: {
    position: ground(0.4, 2.8, 0.9),
    lookAt: [2.6, -0.1, 0.2],
  },
  urban: {
    position: ground(-2.4, 5.2, 1.1),
    lookAt: ground(0.0, 3.4, 0.5),
  },
  soil: {
    // Out in front of the cliff face, looking into the cross-section.
    position: [0.4, -0.2, 8.6],
    lookAt: [0, -1.0, 4.4],
  },
  sky: {
    position: [1.2, 3.2, 7.6],
    lookAt: [0, 4.4, -1.0],
  },
  elsewhere: {
    position: [-2.4, 2.0, -0.6],
    lookAt: [-5.4, 1.5, -3.6],
  },
};
