/**
 * Full-bleed biome scene layout (composition pivot, ADR 0012 addendum).
 *
 * A biome scene is ONE zone rendered as a layered 2.5D backdrop spanning
 * the whole screen: sky behind everything, then FAR / MID / GROUND art
 * bands and an optional FORE framing band. The scene is wider than the
 * screen; a horizontal camera pan (0..maxPan, screen dp) slides each band
 * by its parallax factor.
 *
 * Coverage invariant (unit-tested): a band drawn at width
 * `w + maxPan * parallax` with translateX `-panX * parallax` covers the
 * viewport [0, w] for every legal pan -- at pan 0 its left edge sits at 0,
 * at maxPan its right edge lands exactly at w. That single formula is why
 * the fore band (parallax > 1, the near-frame feel) never exposes an edge
 * despite outrunning the camera.
 *
 * Units are screen dp (no reference-screen rescale: bands are sized from
 * the live viewport, unlike the retired vista's 390dp canvas units).
 * Pure data + math -- no React, no Skia, unit-tested.
 */

/** Painter-ordered scene bands (back first). "sky" is drawn separately. */
export type SceneBand = "far" | "mid" | "ground" | "fore";

export const SCENE_BANDS: readonly SceneBand[] = [
  "far",
  "mid",
  "ground",
  "fore",
];

/** Scene width as a multiple of the screen width (~1.6 screens). */
export const SCENE_WIDTH_FACTOR = 1.6;

/**
 * Horizontal parallax per band: fraction of the camera pan the band moves
 * by. Ground tracks the camera 1:1 (sprites live there); fore outruns it
 * for the near-frame feel; sky barely drifts.
 */
export const SCENE_PARALLAX: Record<SceneBand | "sky", number> = {
  sky: 0.06,
  far: 0.2,
  mid: 0.5,
  ground: 1.0,
  fore: 1.3,
};

/** Backdrop band art canvas (backdrops.gen.ts contract), px. */
export const BACKDROP_ART_PX = { width: 1024, height: 640 } as const;

/** Backdrop draw height per unit width (the art's aspect). */
export const BACKDROP_ASPECT = BACKDROP_ART_PX.height / BACKDROP_ART_PX.width;

/**
 * Where each band's BOTTOM edge sits, as a fraction of screen height.
 * Ground and fore overshoot slightly so the painted base never exposes a
 * hairline at the screen bottom.
 */
export const BAND_BOTTOM_FRACTION: Record<SceneBand, number> = {
  // Taste pass i1: horizon raised — portrait phones showed ~55% empty
  // sky; far/mid sit higher so the ridgeline lands near 40-45%.
  far: 0.62,
  mid: 0.8,
  ground: 1.02,
  fore: 1.05,
};

/**
 * Vertical region of the screen where ground sprites stand (fractions of
 * screen height). Top edge sits below the ground band's crest line;
 * bottom leaves room for the fore framing accents.
 */
export const GROUND_REGION = { top: 0.62, bottom: 0.94 } as const;

/** Everything band placement derives from one measured viewport. */
export type SceneMetrics = {
  /** Viewport, screen dp. */
  w: number;
  h: number;
  /** Ground-band scene width (w * SCENE_WIDTH_FACTOR), dp. */
  sceneWidth: number;
  /** Camera pan range: panX in [0, maxPan]. */
  maxPan: number;
};

export function sceneMetrics(w: number, h: number): SceneMetrics {
  const sceneWidth = Math.round(w * SCENE_WIDTH_FACTOR);
  return { w, h, sceneWidth, maxPan: sceneWidth - w };
}

/** Draw width for a band: exact viewport coverage across the pan range. */
export function bandDrawWidth(metrics: SceneMetrics, band: SceneBand): number {
  return metrics.w + metrics.maxPan * SCENE_PARALLAX[band];
}

/** Placement rect for a backdrop band at pan 0 (translate by -pan*p). */
export function bandRect(
  metrics: SceneMetrics,
  band: SceneBand,
): { x: number; y: number; width: number; height: number } {
  const width = bandDrawWidth(metrics, band);
  const height = width * BACKDROP_ASPECT;
  const bottom = metrics.h * BAND_BOTTOM_FRACTION[band];
  return { x: 0, y: bottom - height, width, height };
}

/**
 * Interim centerpiece placement for zones without backdrop art yet: their
 * existing 512x384 island layer art, centered in ground-band space over a
 * simple ground gradient (the documented migration interim). The island
 * sits at the horizon like a distant landform; inhabitants live on the
 * ground plane in front of it.
 */
export const INTERIM_ISLAND = {
  /** Island art draw width as a fraction of the screen width. */
  widthFraction: 0.9,
  /** Island art bottom edge, fraction of screen height. */
  bottomFraction: 0.7,
  /** Island layer art aspect (512x384). */
  aspect: 384 / 512,
} as const;

export function interimIslandRect(metrics: SceneMetrics): {
  x: number;
  y: number;
  width: number;
  height: number;
} {
  const width = metrics.w * INTERIM_ISLAND.widthFraction;
  const height = width * INTERIM_ISLAND.aspect;
  return {
    x: metrics.sceneWidth / 2 - width / 2,
    y: metrics.h * INTERIM_ISLAND.bottomFraction - height,
    width,
    height,
  };
}

/** Top edge of the interim ground gradient, fraction of screen height. */
export const INTERIM_GROUND_TOP_FRACTION = 0.55;
