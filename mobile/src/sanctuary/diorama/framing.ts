/**
 * Camera framings for the two diorama modes: the whole-archipelago vista
 * and the single-island dive. A framing names the canvas point to center
 * in the viewport and the zoom to show it at; the render layer animates
 * between framings (D6) but never computes them -- these stay pure and
 * unit-tested.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { ISLAND_SLOTS, VISTA_CANVAS } from "@/src/sanctuary/diorama/vistaLayout";

/** A camera target: center this canvas point at this zoom. */
export type Framing = {
  x: number;
  y: number;
  scale: number;
};

/** Zoom applied when diving into an island (unless overridden below). */
export const DEFAULT_DIVE_SCALE = 2.4;

/**
 * Per-zone dive-zoom overrides. Small or sparse islands zoom in harder so
 * a dive always fills the screen with the island, not empty sky.
 */
const DIVE_SCALE_OVERRIDES: Partial<Record<SanctuaryZoneId, number>> = {
  // The elsewhere islet is authored small (islandScale 0.55): lean in.
  elsewhere: 3.2,
};

/** The vista framing: whole canvas centered at 1:1 zoom. */
export function vistaFraming(): Framing {
  return {
    x: VISTA_CANVAS.width / 2,
    y: VISTA_CANVAS.height / 2,
    scale: 1,
  };
}

/** The dive framing for a zone: its island slot centered, zoomed in. */
export function zoneFraming(zoneId: SanctuaryZoneId): Framing {
  const slot = ISLAND_SLOTS[zoneId];
  return {
    x: slot.x,
    y: slot.y,
    scale: DIVE_SCALE_OVERRIDES[zoneId] ?? DEFAULT_DIVE_SCALE,
  };
}
