/**
 * ScenePlan -> VistaPlan: the renderer-ready description of the whole
 * archipelago. Each zone becomes an island pinned to its authored vista
 * slot, carrying painter-sorted 2.5D sprites (elements + tier dressing),
 * silhouette markers for cued dormant zones, and island-local hit rects
 * for tap targets.
 *
 * Everything here is pure and deterministic: the same ScenePlan (plus the
 * same souvenir list) always yields a deep-equal VistaPlan. No React, no
 * Skia, no randomness -- the render layer (D3) just draws what this says.
 *
 * Coordinates: sprite x/y are ISLAND-LOCAL canvas units (the projection of
 * the placement transform relative to the zone's anchor center). The vista
 * transform (slot position, islandScale, parallax, camera) is applied by
 * the renderer / hitTest, never baked in here.
 */

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { DRESSING_RULES, DRESSING_TRANSFORMS } from "@/src/sanctuary/diorama/scene/dressing";
import { ZONE_LAYOUT } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import { heightAt } from "@/src/sanctuary/diorama/terrain/heightfield";
import { project } from "@/src/sanctuary/diorama/projection";
import type { ScenePlan, ZonePlan } from "@/src/sanctuary/diorama/scenePlan";
import {
  ISLAND_SLOTS,
  type IslandBand,
  type IslandSlot,
} from "@/src/sanctuary/diorama/vistaLayout";

/** One drawable node on an island (element unlock, dressing, souvenir). */
export type PlacedSprite = {
  kind: "element" | "scenery" | "souvenir";
  /** Unique within the island: element_id, "ruleKey#i", or souvenir id. */
  key: string;
  /** Content icon key for elements; null for scenery and souvenirs. */
  iconKey: string | null;
  /** Island-local canvas units (see module header). */
  x: number;
  y: number;
  /** Final draw scale (placement variety x false perspective). */
  scale: number;
  /** Painter depth (raw island-space z; smaller = drawn first). */
  depth: number;
};

/** Island-local tap target for a sprite. */
export type HitRect = {
  /** Matches the sprite's `key`. */
  id: string;
  kind: PlacedSprite["kind"];
  /** Top-left corner, island-local canvas units. */
  x: number;
  y: number;
  w: number;
  h: number;
};

/** A cued-mystery marker on a dormant island (drawn as a soft shape). */
export type SilhouetteMarker = {
  x: number;
  y: number;
  scale: number;
};

export type IslandPlan = {
  zoneId: SanctuaryZoneId;
  slot: IslandSlot;
  /** True when the zone is still asleep (drawn desaturated, still tappable). */
  dormant: boolean;
  /** Threshold value: 0 | 1 | 3 | 5 | 10 | 20 | 50. */
  depthTier: number;
  /** Painter-sorted (back -> front) drawables. */
  sprites: PlacedSprite[];
  silhouettes: SilhouetteMarker[];
  /** Same painter order as `sprites` (interactive kinds only). */
  hitRects: HitRect[];
};

export type VistaPlan = {
  /** Islands sorted back -> front (band, then up-screen first). */
  islands: IslandPlan[];
  isInvitationState: boolean;
};

/** Minimum tap-target edge, canvas units (44dp touch-target floor). */
export const MIN_HIT_SIZE = 44;

/** Sprite half-extent at scale 1, canvas units (visual body estimate). */
export const SPRITE_HALF_EXTENT = 12;

/** Padding multiplier applied to the sprite extent before clamping. */
export const HIT_PADDING = 1.3;

/** Souvenirs draw smaller than elements: keepsakes, not inhabitants. */
export const SOUVENIR_SCALE = 0.75;

/** Souvenir ring radius, island-space meters (outside the element spiral). */
export const SOUVENIR_RING_RADIUS = 2.2;

/** Island-space anchor for a dormant zone's silhouette marker. */
const SILHOUETTE_ANCHOR: readonly [number, number, number] = [0, 0.6, 0];

/** Where the souvenir ring lives until D8/D9 assign souvenirs to zones. */
const SOUVENIR_HOME_ZONE: SanctuaryZoneId = "meadow";

const BAND_ORDER: Record<IslandBand, number> = { back: 0, mid: 1, fore: 2 };

/** Island-local projection of a world-space placement position. */
function projectLocal(
  zoneId: SanctuaryZoneId,
  position: readonly [number, number, number],
) {
  const [cx, cy, cz] = ZONE_LAYOUT[zoneId].center;
  return project([position[0] - cx, position[1] - cy, position[2] - cz]);
}

function hitRectFor(sprite: PlacedSprite): HitRect {
  const edge = Math.max(
    MIN_HIT_SIZE,
    SPRITE_HALF_EXTENT * 2 * sprite.scale * HIT_PADDING,
  );
  return {
    id: sprite.key,
    kind: sprite.kind,
    x: sprite.x - edge / 2,
    y: sprite.y - edge / 2,
    w: edge,
    h: edge,
  };
}

function buildIsland(
  zone: ZonePlan,
  souvenirIds: readonly string[],
): IslandPlan {
  const sprites: PlacedSprite[] = [];

  // Element unlocks: the seeded placement projected island-local.
  for (const placed of zone.elements) {
    const p = projectLocal(zone.zoneId, placed.transform.position);
    sprites.push({
      kind: "element",
      key: placed.element.element_id,
      iconKey: placed.element.icon,
      x: p.x,
      y: p.y,
      scale: placed.transform.scale * p.scaleMul,
      depth: p.depth,
    });
  }

  // Tier dressing: this zone's scatter rules, gated by the crossed tier.
  // ("shore" rules belong to the island base art and return with D5.)
  for (const rule of DRESSING_RULES) {
    if (rule.zone !== zone.zoneId) continue;
    if (rule.tierMin > zone.depthTier) continue;
    const transforms = DRESSING_TRANSFORMS.get(rule.key) ?? [];
    transforms.forEach((transform, i) => {
      const p = projectLocal(zone.zoneId, transform.position);
      sprites.push({
        kind: "scenery",
        key: `${rule.key}#${i}`,
        iconKey: null,
        x: p.x,
        y: p.y,
        scale: transform.scale * p.scaleMul,
        depth: p.depth,
      });
    });
  }

  // Souvenir ring: evenly spaced on its own circle, independent of the
  // element spiral so adding a souvenir never moves an inhabitant.
  if (zone.zoneId === SOUVENIR_HOME_ZONE) {
    const [cx, , cz] = ZONE_LAYOUT[zone.zoneId].center;
    souvenirIds.forEach((id, i) => {
      const angle = (i / souvenirIds.length) * Math.PI * 2 - Math.PI / 2;
      const dx = Math.cos(angle) * SOUVENIR_RING_RADIUS;
      const dz = Math.sin(angle) * SOUVENIR_RING_RADIUS;
      const y = heightAt(cx + dx, cz + dz);
      const p = project([dx, y, dz]);
      sprites.push({
        kind: "souvenir",
        key: id,
        iconKey: null,
        x: p.x,
        y: p.y,
        scale: SOUVENIR_SCALE * p.scaleMul,
        depth: p.depth,
      });
    });
  }

  // Painter sort: far (small z) first; key tiebreak keeps it total.
  sprites.sort((a, b) => a.depth - b.depth || a.key.localeCompare(b.key));

  return {
    zoneId: zone.zoneId,
    slot: ISLAND_SLOTS[zone.zoneId],
    dormant: !zone.unlocked,
    depthTier: zone.depthTier,
    sprites,
    silhouettes: zone.silhouette
      ? [
          {
            x: project(SILHOUETTE_ANCHOR).x,
            y: project(SILHOUETTE_ANCHOR).y,
            scale: 1,
          },
        ]
      : [],
    // Scenery is scenery: only elements and souvenirs are tappable.
    hitRects: sprites
      .filter((s) => s.kind !== "scenery")
      .map(hitRectFor),
  };
}

/**
 * Build the archipelago plan.
 *
 * @param plan       The pure scene plan (buildScenePlan output).
 * @param souvenirs  Souvenir ids to place on the home-island ring. Ids
 *                   only for now -- the D8/D9 souvenir milestones extend
 *                   this input with zone/species data without changing
 *                   the plan shape.
 */
export function buildVistaPlan(
  plan: ScenePlan,
  souvenirs: readonly string[] = [],
): VistaPlan {
  const islands = plan.zones
    .map((zone) => buildIsland(zone, souvenirs))
    .sort((a, b) => {
      const band = BAND_ORDER[a.slot.band] - BAND_ORDER[b.slot.band];
      if (band !== 0) return band;
      if (a.slot.y !== b.slot.y) return a.slot.y - b.slot.y;
      return a.zoneId.localeCompare(b.zoneId);
    });

  return {
    islands,
    isInvitationState: plan.isInvitationState,
  };
}
