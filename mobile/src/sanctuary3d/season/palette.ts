/**
 * Season -> 3D scene palette. The API ships a season word and a
 * `background_tone` word (fresh | warm | fading | still); this module maps
 * them to sky/fog/light colors. Pure lookup -- unit-testable, no three
 * imports (colors are hex strings consumed by the scene layer).
 *
 * Art direction (L1): Breath-of-the-Wild-flavored painterly atmosphere --
 * soft blue zenith dissolving to a pale warm horizon, hazy distance fog
 * matched to the horizon, warm low sun, muted sage ground.
 *
 * The seasonal MATERIAL palettes (recoloring zone models) live in the asset
 * pipeline (scripts/sanctuary_assets/palette/) and arrive with asset
 * milestone A8; this file only governs atmosphere.
 */

import type { SanctuarySeason } from "@/src/api/sanctuary";

export type ScenePalette = {
  /** Sky dome zenith color. */
  skyTop: string;
  /** Sky dome horizon color (also the canvas clear fallback). */
  horizon: string;
  /** Distance fog color (kept near the horizon for a soft far edge). */
  fog: string;
  /** Hemisphere light sky color. */
  hemiSky: string;
  /** Hemisphere light ground bounce color. */
  hemiGround: string;
  /** Directional sun intensity. */
  sunIntensity: number;
  /** Warm sun tint. */
  sunColor: string;
  /** Base ground tint for non-terrain accents. */
  ground: string;
};

const SEASON_BASE: Record<SanctuarySeason, ScenePalette> = {
  spring: {
    skyTop: "#6FA8D8",
    horizon: "#E4EAE0",
    fog: "#CBD8D2",
    hemiSky: "#D8E8F2",
    hemiGround: "#7C9B62",
    sunIntensity: 1.5,
    sunColor: "#FFE9C4",
    ground: "#8AA86A",
  },
  summer: {
    skyTop: "#5E9FD4",
    horizon: "#DFE8D6",
    fog: "#C5D6C6",
    hemiSky: "#CFE3F2",
    hemiGround: "#6F934F",
    sunIntensity: 1.7,
    sunColor: "#FFEFC9",
    ground: "#7FA85C",
  },
  autumn: {
    skyTop: "#8AA0C2",
    horizon: "#E8E0D0",
    fog: "#D8CFC2",
    hemiSky: "#DCDDE6",
    hemiGround: "#8A7448",
    sunIntensity: 1.35,
    sunColor: "#FFD9A8",
    ground: "#A98E4F",
  },
  winter: {
    skyTop: "#93AEC4",
    horizon: "#EAEEF0",
    fog: "#DAE1E6",
    hemiSky: "#E0E7EC",
    hemiGround: "#7E8A90",
    sunIntensity: 1.15,
    sunColor: "#F2E9DC",
    ground: "#9FA8A4",
  },
};

/**
 * Tone adjustments layered on the season base. Unknown tones fall back to
 * the season base unchanged (the wire type is a plain string).
 */
const TONE_OVERRIDES: Record<string, Partial<ScenePalette>> = {
  fresh: { sunIntensity: 1.6 },
  warm: { skyTop: "#7AA6C9", horizon: "#EBE4CE", sunIntensity: 1.75, sunColor: "#FFE2A8" },
  fading: { skyTop: "#9A9DBE", horizon: "#E7DCD2", sunIntensity: 1.25 },
  still: { skyTop: "#8FA6B8", horizon: "#E6EBEC", sunIntensity: 1.05 },
};

export function scenePalette(
  season: SanctuarySeason,
  backgroundTone: string,
): ScenePalette {
  const base = SEASON_BASE[season];
  const override = TONE_OVERRIDES[backgroundTone] ?? {};
  return { ...base, ...override };
}
