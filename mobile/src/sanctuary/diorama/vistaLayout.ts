/**
 * The archipelago composition: where each zone's island sits on the wide
 * vista canvas, which parallax band it scrolls in, and how large it is
 * drawn. This is authored layout data (the "camera-ready" arrangement from
 * the 2.5D plan), not derived from the API -- pure constants, unit-tested.
 *
 * Canvas units are dp of a 390dp-wide reference screen. The vista is 2.5
 * screens wide (975 units) so horizontal panning reveals the archipelago;
 * the render layer scales canvas units to the actual device width.
 *
 * Bands (painter order back -> fore) drive both draw order and parallax:
 * far islands drift slower than the pan, foreground islands track it 1:1,
 * and the sky island barely moves at all.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";

/** Vista canvas size in canvas units (2.5 x 390dp wide, 2 x 390dp tall). */
export const VISTA_CANVAS = { width: 975, height: 780 } as const;

/** Depth band an island belongs to (also its painter tier, back first). */
export type IslandBand = "back" | "mid" | "fore";

export type IslandSlot = {
  /** Island anchor (its local origin) on the vista canvas, canvas units. */
  x: number;
  y: number;
  band: IslandBand;
  /** Multiplier applied to island-local coordinates when drawn in the vista. */
  islandScale: number;
};

/**
 * Authored island slots, per the plan: meadow front-center-left (fore),
 * woodland back-left (back), pond front-right (fore), sky top (mid),
 * soil low-front (fore), urban mid-right (mid), elsewhere far-back-left
 * (back, small).
 */
export const ISLAND_SLOTS: Record<SanctuaryZoneId, IslandSlot> = {
  meadow: { x: 330, y: 560, band: "fore", islandScale: 1.1 },
  woodland: { x: 180, y: 200, band: "back", islandScale: 0.9 },
  pond: { x: 700, y: 590, band: "fore", islandScale: 1.0 },
  sky: { x: 500, y: 90, band: "mid", islandScale: 0.7 },
  soil: { x: 480, y: 700, band: "fore", islandScale: 0.9 },
  urban: { x: 760, y: 340, band: "mid", islandScale: 0.85 },
  elsewhere: { x: 70, y: 120, band: "back", islandScale: 0.55 },
};

/**
 * Horizontal parallax per band, plus the sky island's own extra-slow
 * factor: fraction of the camera pan an island actually moves by.
 */
export const PARALLAX_FACTOR: Record<IslandBand | "sky", number> = {
  back: 0.35,
  mid: 0.7,
  fore: 1.0,
  sky: 0.1,
};

/**
 * Parallax factor for a zone's island: the sky island overrides its band
 * (it hangs in the far atmosphere), everything else follows its band.
 */
export function parallaxFor(zoneId: SanctuaryZoneId): number {
  if (zoneId === "sky") return PARALLAX_FACTOR.sky;
  return PARALLAX_FACTOR[ISLAND_SLOTS[zoneId].band];
}
