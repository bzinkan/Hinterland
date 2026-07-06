/**
 * Biome chooser card models (composition pivot, ADR 0012 addendum). Pure
 * derivation from the Sanctuary snapshot: one card per zone in the
 * canonical order, carrying everything the native chooser list renders --
 * title, awake/dormant state, depth-tier ladder position, mystery-cue
 * count, palette-derived colors (dormant cards desaturate through the
 * same satMatrix the scene art bakes with), and a full TalkBack label.
 *
 * No React, no Skia, no randomness -- the same snapshot always yields
 * deep-equal cards.
 */

import type {
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import {
  ZONE_ACCENT_COLOR,
  ZONE_PLACEHOLDER_COLOR,
} from "@/src/sanctuary/diorama/scene/zoneColors";
import {
  applyColorMatrixToHex,
  DORMANT_SAT,
  mixHex,
  satMatrix,
} from "@/src/sanctuary/dioramaui/paletteSlots";

/** Depth-tier threshold ladder (docs/sanctuary.md tier semantics). */
export const TIER_LADDER = [0, 1, 3, 5, 10, 20, 50] as const;

export type BiomeCardModel = {
  zoneId: SanctuaryZoneId;
  title: string;
  dormant: boolean;
  /** Highest crossed threshold VALUE (0 | 1 | 3 | 5 | 10 | 20 | 50). */
  depthTier: number;
  observationCount: number;
  /** Ladder steps crossed (0..6): index of depthTier in TIER_LADDER. */
  tierIndex: number;
  /** Mystery cues waiting in this zone (dormant hint badge). */
  cueCount: number;
  /** Card colors, already dormant-desaturated for sleeping zones. */
  colors: {
    background: string;
    accent: string;
  };
  a11yLabel: string;
};

/** Card background: the zone tint lifted toward paper so text stays legible. */
const CARD_LIFT = 0.62;

export function deriveBiomeCards(
  snapshot: SanctuarySnapshotDto,
): BiomeCardModel[] {
  const zoneById = new Map(snapshot.zones.map((z) => [z.zone_id, z] as const));
  const cueCounts = new Map<SanctuaryZoneId, number>();
  for (const cue of snapshot.mystery_cues) {
    cueCounts.set(cue.zone_id, (cueCounts.get(cue.zone_id) ?? 0) + 1);
  }
  const dormantMatrix = satMatrix(DORMANT_SAT);

  return SANCTUARY_ZONE_ORDER.flatMap((zoneId) => {
    const zone = zoneById.get(zoneId);
    if (!zone) return [];

    const dormant = !zone.unlocked;
    const cueCount = dormant ? cueCounts.get(zoneId) ?? 0 : 0;
    const tierIndex = Math.max(
      0,
      TIER_LADDER.indexOf(zone.depth_tier as (typeof TIER_LADDER)[number]),
    );

    let background = mixHex(ZONE_PLACEHOLDER_COLOR[zoneId], "#FFFFFF", CARD_LIFT);
    let accent = ZONE_ACCENT_COLOR[zoneId];
    if (dormant) {
      background = applyColorMatrixToHex(background, dormantMatrix);
      accent = applyColorMatrixToHex(accent, dormantMatrix);
    }

    const a11yLabel = dormant
      ? cueCount > 0
        ? `${zone.title}, still sleeping, ${cueCount} ${
            cueCount === 1 ? "mystery" : "mysteries"
          } waiting`
        : `${zone.title}, still sleeping`
      : `${zone.title}, awake, depth ${zone.depth_tier}`;

    return [
      {
        zoneId,
        title: zone.title,
        dormant,
        depthTier: zone.depth_tier,
        observationCount: zone.observation_count,
        tierIndex,
        cueCount,
        colors: { background, accent },
        a11yLabel,
      },
    ];
  });
}
