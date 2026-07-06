/**
 * Palette-token substitution + parsed-SVG memoization for the diorama
 * render layer (D4). The generated art (islandLayers.gen.ts /
 * sprites.gen.ts) carries `{{slot}}` placeholders; this module swaps them
 * for concrete hexes and caches the parsed result so a season/dormant
 * remap costs one parse per distinct (asset, palette) pair, not one per
 * frame.
 *
 * Deliberately Skia-free: `substitute` is pure string work, and
 * `createSvgCache` takes the parser as an argument (the screen passes
 * `Skia.SVG.MakeFromString`), so everything here is jest-testable without
 * the native module.
 */

import {
  SANCTUARY_PALETTE_SLOTS,
  type SanctuaryPaletteSlot,
} from "@/src/sanctuary/art/islandLayers.gen";
import { fnv1a32 } from "@/src/sanctuary/diorama/placement/seeds";

/** Concrete hex per palette slot (see paletteSlots.ts for the mapping). */
export type SlotHexes = Record<SanctuaryPaletteSlot, string>;

/** Default parsed-SVG cache capacity (distinct asset x palette pairs). */
export const SVG_CACHE_CAPACITY = 64;

/**
 * Replace every `{{slot}}` placeholder with its hex. Throws in dev when
 * any `{{` survives substitution -- an unreplaced token would otherwise
 * fail the SVG parse silently (Skia returns null and the layer simply
 * vanishes), which is exactly the kind of bug the spike must surface.
 */
export function substitute(svg: string, slots: SlotHexes): string {
  let out = svg;
  for (const slot of SANCTUARY_PALETTE_SLOTS) {
    out = out.split(`{{${slot}}}`).join(slots[slot]);
  }
  if (__DEV__ && out.includes("{{")) {
    const leftovers = out.match(/\{\{[^}]*\}\}/g) ?? ["{{?}}"];
    throw new Error(
      `svgCache.substitute: unreplaced palette tokens ${[
        ...new Set(leftovers),
      ].join(", ")} -- the art references a slot outside SANCTUARY_PALETTE_SLOTS`,
    );
  }
  return out;
}

export type SvgCache<T> = {
  /**
   * Parsed SVG for (asset key, palette), memoized. `key` must be unique
   * per asset (e.g. "layer:meadow:mid" or "sprite:bush"); the palette
   * hexes are hashed into the cache key so a season remap re-parses
   * instead of serving stale tints. Null parses are cached too -- a bad
   * asset should not be re-parsed every frame.
   */
  makeSvg(key: string, svg: string, slots: SlotHexes): T | null;
  /** Current entry count (tests/diagnostics). */
  size(): number;
};

/**
 * Small LRU over parsed SVGs. `parse` is injected (Skia.SVG.MakeFromString
 * on device, any fake under jest). Eviction drops the least-recently-used
 * entry; evicted SkSVGs are left to the GC rather than disposed eagerly so
 * an in-flight frame can never draw a freed object (revisit with the D7
 * thumbnail pass if cache pressure ever materializes).
 */
export function createSvgCache<T>(
  parse: (svg: string) => T | null,
  capacity: number = SVG_CACHE_CAPACITY,
): SvgCache<T> {
  const entries = new Map<string, T | null>();

  function cacheKey(key: string, slots: SlotHexes): string {
    const paletteHash = fnv1a32(
      SANCTUARY_PALETTE_SLOTS.map((slot) => slots[slot]).join("|"),
    );
    return `${key}@${paletteHash.toString(16)}`;
  }

  return {
    makeSvg(key, svg, slots) {
      const ck = cacheKey(key, slots);
      if (entries.has(ck)) {
        const hit = entries.get(ck) ?? null;
        // Refresh recency: Map iteration order is insertion order.
        entries.delete(ck);
        entries.set(ck, hit);
        return hit;
      }
      const made = parse(substitute(svg, slots));
      entries.set(ck, made);
      if (entries.size > capacity) {
        const oldest = entries.keys().next().value;
        if (oldest !== undefined) entries.delete(oldest);
      }
      return made;
    },
    size: () => entries.size,
  };
}
