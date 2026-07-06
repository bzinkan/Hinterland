/**
 * The 2.5D projection: island-space 3D positions (the same [x, y, z]
 * transforms seededLayout and the dressing scatter already produce) ->
 * flat diorama coordinates. This is the single place where "depth" becomes
 * "down-screen and slightly smaller", so every placed sprite, silhouette,
 * and hit rect agrees on where things sit.
 *
 * Convention (island-local, canvas units):
 *   - screenX grows with +x (island east).
 *   - screenY grows DOWN the screen; +z (toward the viewer) pushes a
 *     sprite down-screen, +y (up in the world) lifts it up-screen.
 *   - depth is the raw z, kept for painter sorting (draw far -> near).
 *   - scaleMul shrinks sprites slightly as they sit deeper (+z is NEARER,
 *     so it grows toward 1 and beyond-origin rows shrink), giving the
 *     gentle false-perspective the diorama look needs without a camera.
 *
 * Pure math -- no React, no Skia, no randomness.
 */

/** World-units -> canvas-units scale (1 island meter = 14 canvas units). */
export const S = 14;

/** How strongly +z (toward the viewer) pushes a sprite down-screen. */
export const K_DEPTH = 0.5;

/** How strongly +y (world up) lifts a sprite up-screen. */
export const K_HEIGHT = 0.8;

/** Per-unit-depth shrink factor for the false perspective. */
export const K_PERSP = 0.02;

/** A projected island-space point, in island-local canvas units. */
export type Projected = {
  /** Horizontal canvas offset from the island anchor. */
  x: number;
  /** Vertical canvas offset from the island anchor (positive = down). */
  y: number;
  /** Raw z, for painter sorting (smaller = farther back, drawn first). */
  depth: number;
  /** Multiply the sprite's own scale by this for false perspective. */
  scaleMul: number;
};

/**
 * Project an island-space position onto the diorama plane.
 *
 *   screenX  = x * S
 *   screenY  = z * S * K_DEPTH - y * S * K_HEIGHT
 *   depth    = z
 *   scaleMul = 1 - z * K_PERSP
 */
export function project(
  position: readonly [number, number, number],
): Projected {
  const [x, y, z] = position;
  return {
    x: x * S,
    y: z * S * K_DEPTH - y * S * K_HEIGHT,
    depth: z,
    scaleMul: 1 - z * K_PERSP,
  };
}
