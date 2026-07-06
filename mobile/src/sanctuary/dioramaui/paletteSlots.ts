/**
 * ScenePalette + zone placeholder colors -> the 12 generated-art palette
 * slots (SANCTUARY_PALETTE_SLOTS), plus the dormant saturation matrix.
 * Pure data mapping -- the render layer feeds the result to svgCache and
 * never hand-picks hexes itself, so a season change is one palette swap.
 *
 * Slot sources follow the islandLayers.gen.ts header contract:
 *   horizon    tracks ScenePalette.horizon
 *   glow       tracks the ScenePalette.sunColor family
 *   green_mid  tracks ScenePalette.ground
 *   accent_*   track season.zone_accents at dive time (D6+); until then
 *              they hold the spring-neutral baseline
 * Slots without a ScenePalette source draw from the zone placeholder
 * palette (same hue families) or the generator's spring-neutral baseline.
 */

import type { SanctuaryPaletteSlot } from "@/src/sanctuary/art/islandLayers.gen";
import {
  ZONE_ACCENT_COLOR,
  ZONE_PLACEHOLDER_COLOR,
} from "@/src/sanctuary/diorama/scene/zoneColors";
import type { ScenePalette } from "@/src/sanctuary/diorama/season/palette";
import type { SlotHexes } from "@/src/sanctuary/dioramaui/svgCache";

/** Spring-neutral baseline for slots with no live palette source yet
 * (author/lib/tokens.mjs values, per the generated-art header). */
const BASELINE: Pick<
  Record<SanctuaryPaletteSlot, string>,
  "sand" | "earth_mid" | "accent_warm" | "accent_cool"
> = {
  sand: "#D9C79A",
  earth_mid: "#8A6B4A",
  accent_warm: "#E2793F",
  accent_cool: "#8C7BC9",
};

/** Concrete hex for every generated-art palette slot. */
export function paletteSlotHexes(palette: ScenePalette): SlotHexes {
  return {
    green_mid: palette.ground,
    green_deep: ZONE_ACCENT_COLOR.woodland,
    bark: ZONE_PLACEHOLDER_COLOR.soil,
    sand: BASELINE.sand,
    earth_mid: BASELINE.earth_mid,
    earth_deep: ZONE_ACCENT_COLOR.soil,
    water: ZONE_PLACEHOLDER_COLOR.pond,
    accent_warm: BASELINE.accent_warm,
    accent_cool: BASELINE.accent_cool,
    glow: palette.sunColor,
    cloud: ZONE_PLACEHOLDER_COLOR.sky,
    horizon: palette.horizon,
  };
}

/** Rec. 709 luma weights for the saturation matrix. */
const LUMA_R = 0.2126;
const LUMA_G = 0.7152;
const LUMA_B = 0.0722;

/**
 * 4x5 color matrix that lerps toward luminance grey: s = 1 is the exact
 * identity, s ~ 0.12 is the dormant "asleep, not locked" look. The offset
 * column adds a slight warm bias (scaled by 1 - s, so it vanishes at
 * identity) -- dormant islands read as dusty parchment, not corpse grey.
 * Worklet-safe: driven per-frame by the wake animation's shared value.
 */
export function satMatrix(s: number): number[] {
  "worklet";
  const inv = 1 - s;
  return [
    LUMA_R * inv + s, LUMA_G * inv,     LUMA_B * inv,     0, 0.032 * inv,
    LUMA_R * inv,     LUMA_G * inv + s, LUMA_B * inv,     0, 0.014 * inv,
    // `+ 0` normalizes -0 at s = 1 so the identity case is exact.
    LUMA_R * inv,     LUMA_G * inv,     LUMA_B * inv + s, 0, -0.02 * inv + 0,
    0,                0,                0,                1, 0,
  ];
}
