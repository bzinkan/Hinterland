/**
 * Typed access to the generated sprite manifest. The diorama never touches
 * sprites.gen.ts directly -- this wrapper is the seam that keeps a missing
 * sprite from ever crashing a render: an unmapped icon key resolves to a
 * typed fallback KIND (a deliberately simple shape the render layer can
 * draw in the zone's accent color) instead of throwing or blanking.
 *
 * Shape language of the fallback kinds (kid-legible at a glance):
 *   coarse        -> "dome"     (a living cluster)
 *   charismatic   -> "crystal"  (someone special lives here)
 *   relationship  -> "ring"     (two things connected)
 *   surprise      -> "trinket"  (a found treasure)
 *   signature     -> "landmark" (a tall marker)
 */

import type { SanctuaryElementType } from "@/src/api/sanctuary";
import {
  SANCTUARY_ELEMENT_SPRITES,
  SANCTUARY_SCENERY_SPRITES,
  type SanctuaryElementSprite,
  type SanctuaryScenerySprite,
} from "@/src/sanctuary/art/sprites.gen";

/** Renderer-agnostic fallback shapes for icon keys without a sprite yet. */
export type SpriteFallbackKind =
  | "dome"
  | "crystal"
  | "ring"
  | "trinket"
  | "landmark";

const FALLBACK_KIND: Record<SanctuaryElementType, SpriteFallbackKind> = {
  coarse: "dome",
  charismatic: "crystal",
  relationship: "ring",
  surprise: "trinket",
  signature: "landmark",
};

/**
 * What the plan carries for each element: either a real atlas sprite or
 * the typed fallback kind derived from the element type. Never null,
 * never throws -- a content/manifest gap degrades to a simple shape.
 */
export type SpriteResolution =
  | { kind: "sprite"; spriteKey: string; sprite: SanctuaryElementSprite }
  | { kind: "fallback"; fallback: SpriteFallbackKind };

/** Sprite for a content icon key, or the typed fallback kind. */
export function resolveElementSprite(
  iconKey: string,
  elementType: SanctuaryElementType,
): SpriteResolution {
  const sprite = SANCTUARY_ELEMENT_SPRITES[iconKey];
  if (sprite) return { kind: "sprite", spriteKey: iconKey, sprite };
  return { kind: "fallback", fallback: FALLBACK_KIND[elementType] };
}

/** All zone tier-dressing entries whose tierMin is met by `depthTier`. */
export function sceneryForZoneTier(
  zone: string,
  depthTier: number,
): Array<[name: string, sprite: SanctuaryScenerySprite]> {
  return Object.entries(SANCTUARY_SCENERY_SPRITES).filter(
    ([, sprite]) => sprite.zone === zone && sprite.tierMin <= depthTier,
  );
}

/** Every icon key the manifest currently draws (for tests/diagnostics). */
export function modeledIconKeys(): string[] {
  return Object.keys(SANCTUARY_ELEMENT_SPRITES);
}

/** One scenery (tier-dressing) entry by manifest name, or null. */
export function getScenerySprite(name: string): SanctuaryScenerySprite | null {
  return SANCTUARY_SCENERY_SPRITES[name] ?? null;
}

export type { SanctuaryElementSprite, SanctuaryScenerySprite };
