/**
 * Island-local sprites -> biome-scene ground band (composition pivot,
 * ADR 0012 addendum). The seeded placement pipeline (seededLayout ->
 * projection -> vistaPlan) stays the single authority for WHERE things
 * live; this module only remaps its island-local output onto the scene's
 * ground plane:
 *
 *   - x: the painted-plateau domain [-ISLAND_ART_HALF_WIDTH, +half] maps
 *     linearly onto the ground band's usable width, so relative spread is
 *     preserved exactly (an affine map).
 *   - y: the island-local vertical domain (projection depth/height range)
 *     maps linearly onto the GROUND_REGION rows of the screen, preserving
 *     the projected depth ordering.
 *   - scale: one uniform unitScale (dest width / source width) so relative
 *     sprite sizes survive the remap unchanged.
 *   - painter order: the input order (already painter-sorted by the plan)
 *     is preserved verbatim.
 *
 * Hit targets are screen-space-sized here (scene dp == screen dp on the
 * ground band, parallax 1.0): every interactive rect is at least 44dp and
 * capped so a big sprite cannot blanket its neighbors.
 *
 * Pure math -- no React, no Skia, no randomness. Unit-tested.
 */

import { ISLAND_ART_HALF_WIDTH } from "@/src/sanctuary/diorama/artFit";
import {
  GROUND_REGION,
  SCENE_PARALLAX,
  type SceneMetrics,
} from "@/src/sanctuary/diorama/sceneLayout";
import type { IslandPlan, PlacedSprite } from "@/src/sanctuary/diorama/vistaPlan";
import { SPRITE_HALF_EXTENT } from "@/src/sanctuary/diorama/vistaPlan";

/**
 * Island-local vertical domain the remap normalizes from. Derived from the
 * projection conventions (projection.ts: y = z*S*K_DEPTH - y*S*K_HEIGHT
 * over the zone footprints); inputs outside it clamp to the region edge
 * instead of leaving the ground band.
 */
export const LOCAL_Y_DOMAIN = { min: -36, max: 24 } as const;

/** Horizontal margin inside the scene width kept clear of sprites. */
export const GROUND_MARGIN_FRACTION = 0.08;

/** Interactive rect edge bounds, screen dp (44dp floor, capped). */
export const SCENE_MIN_HIT = 44;
export const SCENE_MAX_HIT = 96;

/** Padding multiplier on the visual body before the min/max clamp. */
export const SCENE_HIT_PADDING = 1.3;

/** Silhouette tap half-extent bounds, screen dp. */
export const SILHOUETTE_HIT_HALF_MIN = 22;
export const SILHOUETTE_HIT_HALF_MAX = 90;

/** Island-local silhouette hit half-extent (hitTest.ts kept 28 units). */
const SILHOUETTE_LOCAL_HALF = 28;

/** One drawable on the scene's ground band, scene-space dp. */
export type ScenePlacedSprite = {
  kind: PlacedSprite["kind"];
  key: string;
  iconKey: string | null;
  /** Foot position in ground-band scene coordinates, dp. */
  x: number;
  y: number;
  /** Final draw scale (placement scale x unitScale). */
  scale: number;
};

/** Scene-space tap target (ground-band coordinates, dp). */
export type SceneHitRect = {
  id: string;
  kind: "element" | "souvenir" | "silhouette";
  x: number;
  y: number;
  w: number;
  h: number;
};

export type SceneSilhouette = {
  x: number;
  y: number;
  scale: number;
};

/** The renderer-ready ground plan for one biome scene. */
export type GroundPlan = {
  /** Painter order preserved from the island plan. */
  sprites: ScenePlacedSprite[];
  /** Painter order, interactive kinds only (scenery is scenery). */
  hitRects: SceneHitRect[];
  silhouettes: SceneSilhouette[];
  silhouetteRects: SceneHitRect[];
  /** Island-local unit -> scene dp factor (sceneArt draws with this). */
  unitScale: number;
};

const clamp = (v: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, v));

/** Remap one island plan's sprites/silhouettes onto the scene ground. */
export function remapIslandToGround(
  island: IslandPlan,
  metrics: SceneMetrics,
): GroundPlan {
  const destLeft = metrics.sceneWidth * GROUND_MARGIN_FRACTION;
  const destRight = metrics.sceneWidth * (1 - GROUND_MARGIN_FRACTION);
  const destTop = metrics.h * GROUND_REGION.top;
  const destBottom = metrics.h * GROUND_REGION.bottom;

  const srcWidth = 2 * ISLAND_ART_HALF_WIDTH;
  const srcHeight = LOCAL_Y_DOMAIN.max - LOCAL_Y_DOMAIN.min;
  const unitScale = (destRight - destLeft) / srcWidth;

  const mapX = (lx: number): number =>
    destLeft +
    ((clamp(lx, -ISLAND_ART_HALF_WIDTH, ISLAND_ART_HALF_WIDTH) +
      ISLAND_ART_HALF_WIDTH) /
      srcWidth) *
      (destRight - destLeft);
  const mapY = (ly: number): number =>
    destTop +
    ((clamp(ly, LOCAL_Y_DOMAIN.min, LOCAL_Y_DOMAIN.max) - LOCAL_Y_DOMAIN.min) /
      srcHeight) *
      (destBottom - destTop);

  const sprites: ScenePlacedSprite[] = island.sprites.map((s) => ({
    kind: s.kind,
    key: s.key,
    iconKey: s.iconKey,
    x: mapX(s.x),
    y: mapY(s.y),
    scale: s.scale * unitScale,
  }));

  const hitRects: SceneHitRect[] = sprites
    .filter((s) => s.kind !== "scenery")
    .map((s) => {
      const body = 2 * SPRITE_HALF_EXTENT * s.scale * SCENE_HIT_PADDING;
      const edge = clamp(body, SCENE_MIN_HIT, SCENE_MAX_HIT);
      return {
        id: s.key,
        kind: s.kind as "element" | "souvenir",
        x: s.x - edge / 2,
        y: s.y - edge / 2,
        w: edge,
        h: edge,
      };
    });

  const silhouettes: SceneSilhouette[] = island.silhouettes.map((m) => ({
    x: mapX(m.x),
    y: mapY(m.y),
    scale: m.scale * unitScale,
  }));
  const silhouetteRects: SceneHitRect[] = silhouettes.map((m, i) => {
    const half = clamp(
      SILHOUETTE_LOCAL_HALF * unitScale,
      SILHOUETTE_HIT_HALF_MIN,
      SILHOUETTE_HIT_HALF_MAX,
    );
    return {
      id: `silhouette#${i}`,
      kind: "silhouette",
      x: m.x - half,
      y: m.y - half,
      w: half * 2,
      h: half * 2,
    };
  });

  return { sprites, hitRects, silhouettes, silhouetteRects, unitScale };
}

export type GroundHit =
  | { type: "sprite"; rectId: string }
  | { type: "silhouette" };

/**
 * Resolve a tap against the ground plan. `point` is screen dp; the ground
 * band pans 1:1 with the camera, so scene x = screen x + panX and there is
 * no vertical offset (bands only translate horizontally). Topmost sprite
 * wins (hitRects share the sprites' painter order, walked reversed), then
 * silhouettes, else null.
 */
export function groundHitTest(
  plan: GroundPlan,
  point: { x: number; y: number },
  panX: number,
): GroundHit | null {
  const sx = point.x + panX * SCENE_PARALLAX.ground;
  const sy = point.y;
  for (let i = plan.hitRects.length - 1; i >= 0; i--) {
    const r = plan.hitRects[i];
    if (sx >= r.x && sx <= r.x + r.w && sy >= r.y && sy <= r.y + r.h) {
      return { type: "sprite", rectId: r.id };
    }
  }
  for (const r of plan.silhouetteRects) {
    if (sx >= r.x && sx <= r.x + r.w && sy >= r.y && sy <= r.y + r.h) {
      return { type: "silhouette" };
    }
  }
  return null;
}
