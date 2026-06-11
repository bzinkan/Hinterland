/**
 * Typed access to the generated asset manifest. The scene never touches
 * assetManifest.gen.ts directly -- this wrapper is the seam that keeps a
 * missing model from ever crashing a render (the scene falls back to a
 * typed placeholder shape instead, per ADR 0011).
 */

import {
  SANCTUARY_ELEMENT_ASSETS,
  SANCTUARY_SCENERY_ASSETS,
  type SanctuaryElementAsset,
  type SanctuarySceneryAsset,
} from "@/src/sanctuary/assetManifest.gen";

/** Model for a content icon key, or null -> render the typed fallback. */
export function resolveElementAsset(
  iconKey: string,
): SanctuaryElementAsset | null {
  return SANCTUARY_ELEMENT_ASSETS[iconKey] ?? null;
}

/** All zone tier-dressing entries whose tierMin is met by `depthTier`. */
export function sceneryForZoneTier(
  zone: string,
  depthTier: number,
): Array<[name: string, asset: SanctuarySceneryAsset]> {
  return Object.entries(SANCTUARY_SCENERY_ASSETS).filter(
    ([, asset]) => asset.zone === zone && asset.tierMin <= depthTier,
  );
}

/** Every icon key the manifest currently models (for tests/diagnostics). */
export function modeledIconKeys(): string[] {
  return Object.keys(SANCTUARY_ELEMENT_ASSETS);
}

/** One scenery (tier-dressing) entry by manifest name, or null. */
export function getSceneryAsset(name: string): SanctuarySceneryAsset | null {
  return SANCTUARY_SCENERY_ASSETS[name] ?? null;
}

export type { SanctuaryElementAsset, SanctuarySceneryAsset };
