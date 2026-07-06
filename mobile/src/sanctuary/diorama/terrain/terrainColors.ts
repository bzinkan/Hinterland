/**
 * Paints the terrain's vertex colors from zone states (pure; recomputed
 * when the plan changes). This is where "color floods in" literally
 * happens: awake zones get the painterly sage/gold treatment, dormant
 * zones desaturate to the warm grey, and the invitation state sleeps the
 * whole island except a warm lit path into the meadow.
 *
 * BotW-flavored ground palette: muted sage greens rolling toward golden
 * highlands, grey-brown rock below the rim, dark pond bed.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { ZONE_LAYOUT } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import { WATER_LEVEL, type TerrainArrays } from "@/src/sanctuary/diorama/terrain/heightfield";

export type TerrainZoneState = {
  zoneId: SanctuaryZoneId;
  unlocked: boolean;
};

type Rgb = [number, number, number];

const hex = (h: string): Rgb => {
  const v = h.replace("#", "");
  return [
    parseInt(v.slice(0, 2), 16) / 255,
    parseInt(v.slice(2, 4), 16) / 255,
    parseInt(v.slice(4, 6), 16) / 255,
  ];
};

// Painterly ground ramp (low -> high ground).
const VALLEY_GREEN = hex("#6E9554");
const MID_SAGE = hex("#8AA86A");
const HIGH_GOLD = hex("#B5AE6A");
const ROCK = hex("#7A6A57");
const POND_BED = hex("#4A5E50");
const DORMANT = hex("#84867F");
const PATH_WARM = hex("#C9B27C");

const GROUND_ZONES: SanctuaryZoneId[] = ["meadow", "woodland", "pond", "urban"];

function lerp(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function smooth(t: number): number {
  const c = Math.min(1, Math.max(0, t));
  return c * c * (3 - 2 * c);
}

export function buildTerrainColors(
  arrays: TerrainArrays,
  zoneStates: TerrainZoneState[],
  isInvitationState: boolean,
): Float32Array {
  const { positions } = arrays;
  const colors = new Float32Array(positions.length);
  const stateByZone = new Map(zoneStates.map((s) => [s.zoneId, s.unlocked]));
  const meadow = ZONE_LAYOUT.meadow.center;

  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i];
    const y = positions[i + 1];
    const z = positions[i + 2];

    // Base color by elevation.
    let color: Rgb;
    if (y < WATER_LEVEL - 0.05) {
      color = y < -1.2 ? ROCK : POND_BED;
    } else {
      const t = smooth((y - 0.2) / 1.6);
      color = t < 0.5 ? lerp(VALLEY_GREEN, MID_SAGE, t * 2) : lerp(MID_SAGE, HIGH_GOLD, (t - 0.5) * 2);
    }

    // Dormancy: each dormant ground zone washes its region toward grey.
    let dormancy = 0;
    if (isInvitationState) {
      dormancy = 1;
    } else {
      for (const zoneId of GROUND_ZONES) {
        if (stateByZone.get(zoneId) !== false) continue;
        const c = ZONE_LAYOUT[zoneId].center;
        const r = ZONE_LAYOUT[zoneId].radius;
        const d = Math.hypot(x - c[0], z - c[2]);
        const w = smooth(1 - d / (r * 1.35));
        dormancy = Math.max(dormancy, w);
      }
    }
    if (y < WATER_LEVEL - 0.05) {
      dormancy *= 0.4; // rock keeps most of its character
    }
    color = lerp(color, DORMANT, dormancy * 0.85);

    // Invitation: one warm lit path from the front edge into the meadow.
    if (isInvitationState && y > WATER_LEVEL) {
      // Path = soft-edged segment from (−1.2, 7.5) to the meadow center.
      const ax = -1.2, az = 7.5;
      const bx = meadow[0], bz = meadow[2];
      const abx = bx - ax, abz = bz - az;
      const len2 = abx * abx + abz * abz;
      const t = Math.min(1, Math.max(0, ((x - ax) * abx + (z - az) * abz) / len2));
      const px = ax + abx * t, pz = az + abz * t;
      const dPath = Math.hypot(x - px, z - pz);
      const w = smooth(1 - dPath / 0.9);
      color = lerp(color, PATH_WARM, w * 0.8);
    }

    colors[i] = color[0];
    colors[i + 1] = color[1];
    colors[i + 2] = color[2];
  }

  return colors;
}
