/**
 * THE canonical palette-slot vocabulary (ADR 0012 palette-token tinting).
 * Every color in generated art is a `{{slot}}` placeholder naming one of
 * these twelve slots -- no literal hex ever ships in an SVG. Seasonal and
 * dormant looks are token remaps of the same slots, never duplicate art.
 *
 * This file is the single source of truth: the builder (svg.mjs) refuses
 * unknown slots at generate time, the linter re-checks committed files,
 * and build_manifest.mjs mirrors the list into the generated TS manifest
 * (SANCTUARY_PALETTE_SLOTS) for the renderer's substitution map.
 */

export const PALETTE_SLOTS = [
  "green_mid",
  "green_deep",
  "bark",
  "sand",
  "earth_mid",
  "earth_deep",
  "water",
  "accent_warm",
  "accent_cool",
  "glow",
  "cloud",
  "horizon",
];

/**
 * Season-neutral baseline hexes (spring-flavored), used by preview.mjs and
 * documented in the generated manifest as the mapping anchor to the app's
 * season/zone palettes:
 *   horizon      tracks ScenePalette.horizon (season/palette.ts)
 *   glow         tracks the ScenePalette.sunColor family
 *   green_mid    tracks ScenePalette.ground / hemiGround
 *   accent_*     track SanctuarySnapshotDto.season.zone_accents at dive time
 * Everything else is a stable material color the season remap may nudge.
 */
export const DEFAULT_SLOT_HEX = {
  green_mid: "#7FA85C",
  green_deep: "#4F7A3C",
  bark: "#6E4F35",
  sand: "#D9C79A",
  earth_mid: "#8A6B4A",
  earth_deep: "#5B4531",
  water: "#6FAFC9",
  accent_warm: "#E2793F",
  accent_cool: "#8C7BC9",
  glow: "#FFE9A8",
  cloud: "#F4F1E8",
  horizon: "#E4EAE0",
};

/** `{{slot}}` placeholder for a known slot; throws on vocabulary misses. */
export function token(slot) {
  if (!PALETTE_SLOTS.includes(slot)) {
    throw new Error(`unknown palette slot '${slot}' (see author/lib/tokens.mjs)`);
  }
  return `{{${slot}}}`;
}
