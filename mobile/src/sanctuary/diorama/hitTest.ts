/**
 * Tap resolution for the diorama. Pure inverse of the render transform:
 * screen point -> viewport camera -> per-island slot + parallax -> island
 * -local units -> hit rects. The renderer and this module share ONE
 * forward transform (screenFromIslandLocal) so what you see is exactly
 * what you tap.
 *
 * Camera model (matches the D3 renderer):
 *   anchorX = (slot.x - panX * parallaxFor(zone)) * viewScale + viewX
 *   anchorY = slot.y * viewScale + viewY
 *   screen  = anchor + islandLocal * slot.islandScale * viewScale
 *
 * Modes:
 *   - "vista": returns island hits. Awake AND dormant islands are both
 *     tappable -- tapping a dormant island surfaces its mystery cue.
 *     Foreground islands win overlaps (checked front -> back).
 *   - "dive": restricted to the dived island; returns the TOPMOST sprite
 *     hit (reverse painter order), then silhouette markers, else null.
 *
 * No React, no Skia, no randomness.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { parallaxFor, type IslandSlot } from "@/src/sanctuary/diorama/vistaLayout";
import type { IslandPlan, VistaPlan } from "@/src/sanctuary/diorama/vistaPlan";

/** The render camera: screen offset, zoom, and horizontal vista pan. */
export type DioramaViewport = {
  /** Screen-space translation applied after scaling. */
  viewX: number;
  viewY: number;
  /** Zoom (vista 1.0; dives use the zoneFraming scale). */
  viewScale: number;
  /** Horizontal camera pan across the vista canvas, canvas units. */
  panX: number;
};

export type DioramaPoint = { x: number; y: number };

export type Hit =
  | { type: "island"; zoneId: SanctuaryZoneId }
  | { type: "sprite"; islandZoneId: SanctuaryZoneId; rectId: string }
  | { type: "silhouette"; zoneId: SanctuaryZoneId };

/**
 * Island tap radius in island-local canvas units (uniform before
 * islandScale; covers the element spiral plus the dressing skirt).
 */
export const ISLAND_HIT_RADIUS = 36;

/** Half-extent of a silhouette marker's implicit tap square. */
export const SILHOUETTE_HIT_HALF = 28;

/** Forward transform: island-local point -> screen (shared with D3). */
export function screenFromIslandLocal(
  zoneId: SanctuaryZoneId,
  slot: IslandSlot,
  viewport: DioramaViewport,
  local: DioramaPoint,
): DioramaPoint {
  const anchorX =
    (slot.x - viewport.panX * parallaxFor(zoneId)) * viewport.viewScale +
    viewport.viewX;
  const anchorY = slot.y * viewport.viewScale + viewport.viewY;
  return {
    x: anchorX + local.x * slot.islandScale * viewport.viewScale,
    y: anchorY + local.y * slot.islandScale * viewport.viewScale,
  };
}

/** Inverse transform: screen point -> island-local units. */
export function islandLocalFromScreen(
  zoneId: SanctuaryZoneId,
  slot: IslandSlot,
  viewport: DioramaViewport,
  point: DioramaPoint,
): DioramaPoint {
  const canvasX = (point.x - viewport.viewX) / viewport.viewScale;
  const canvasY = (point.y - viewport.viewY) / viewport.viewScale;
  return {
    x: (canvasX - slot.x + viewport.panX * parallaxFor(zoneId)) / slot.islandScale,
    y: (canvasY - slot.y) / slot.islandScale,
  };
}

function hitIsland(
  island: IslandPlan,
  viewport: DioramaViewport,
  point: DioramaPoint,
): boolean {
  const local = islandLocalFromScreen(island.zoneId, island.slot, viewport, point);
  return Math.hypot(local.x, local.y) <= ISLAND_HIT_RADIUS;
}

function hitDivedIsland(
  island: IslandPlan,
  viewport: DioramaViewport,
  point: DioramaPoint,
): Hit | null {
  const local = islandLocalFromScreen(island.zoneId, island.slot, viewport, point);

  // Topmost sprite first: hitRects share the sprites' painter order, so
  // walk them back-to-front reversed.
  for (let i = island.hitRects.length - 1; i >= 0; i--) {
    const rect = island.hitRects[i];
    if (
      local.x >= rect.x &&
      local.x <= rect.x + rect.w &&
      local.y >= rect.y &&
      local.y <= rect.y + rect.h
    ) {
      return { type: "sprite", islandZoneId: island.zoneId, rectId: rect.id };
    }
  }

  for (const marker of island.silhouettes) {
    if (
      Math.abs(local.x - marker.x) <= SILHOUETTE_HIT_HALF &&
      Math.abs(local.y - marker.y) <= SILHOUETTE_HIT_HALF
    ) {
      return { type: "silhouette", zoneId: island.zoneId };
    }
  }

  return null;
}

/**
 * Resolve a tap against the vista plan.
 *
 * @param vistaPlan  The current archipelago plan.
 * @param viewport   The render camera at tap time.
 * @param point      Tap location in screen coordinates.
 * @param mode       "vista" for the archipelago, "dive" inside one island.
 * @param divedZone  The dived island in "dive" mode (null -> no hit).
 */
export function hitTest(
  vistaPlan: VistaPlan,
  viewport: DioramaViewport,
  point: DioramaPoint,
  mode: "vista" | "dive",
  divedZone: SanctuaryZoneId | null,
): Hit | null {
  if (mode === "dive") {
    if (divedZone === null) return null;
    const island = vistaPlan.islands.find((i) => i.zoneId === divedZone);
    if (!island) return null;
    return hitDivedIsland(island, viewport, point);
  }

  // Vista: islands are painted back -> front, so test front -> back and
  // the first hit wins (foreground islands cover farther ones).
  for (let i = vistaPlan.islands.length - 1; i >= 0; i--) {
    const island = vistaPlan.islands[i];
    if (hitIsland(island, viewport, point)) {
      return { type: "island", zoneId: island.zoneId };
    }
  }
  return null;
}
