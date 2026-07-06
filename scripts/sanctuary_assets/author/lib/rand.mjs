/**
 * Deterministic hashing + PRNG for the art generators. Ported verbatim from
 * mobile/src/sanctuary/diorama/placement/seeds.ts so pipeline seeds share
 * the app's stability guarantees: same recipe, same label, same bytes --
 * on every platform, in every rerun (the CI determinism gate depends on it).
 */

/** FNV-1a 32-bit string hash (stable across platforms/sessions). */
export function fnv1a32(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/** mulberry32 PRNG: tiny, deterministic, good enough for visual jitter. */
export function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Seeded RNG for a stable label (e.g. "layer:meadow:base"). */
export function rngFor(label) {
  return mulberry32(fnv1a32(label));
}
