/**
 * Minimal geohash encoder for expedition relevance -- pure, no deps.
 *
 * 4 characters resolve to a grid square of roughly 20 by 40 km, COARSER
 * than the coarse (approximate) Android location the app already uses.
 * Raw lat/lng never leaves the device for this feature; the 4-char cell
 * id is the only location datum sent to the backend.
 */

const BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz";
const PRECISION = 4;

/**
 * Encode a coordinate as a standard 4-character geohash. Inputs are
 * clamped to the valid lat/lng ranges, so the poles and the
 * antimeridian still produce a well-formed cell.
 */
export function encodeGeohash4(lat: number, lng: number): string {
  const clampedLat = Math.min(90, Math.max(-90, lat));
  const clampedLng = Math.min(180, Math.max(-180, lng));

  let latLo = -90;
  let latHi = 90;
  let lngLo = -180;
  let lngHi = 180;

  let hash = "";
  let charBits = 0;
  let bitCount = 0;
  // Standard geohash bit interleaving starts with longitude.
  let isLngBit = true;

  while (hash.length < PRECISION) {
    if (isLngBit) {
      const mid = (lngLo + lngHi) / 2;
      if (clampedLng >= mid) {
        charBits = charBits * 2 + 1;
        lngLo = mid;
      } else {
        charBits = charBits * 2;
        lngHi = mid;
      }
    } else {
      const mid = (latLo + latHi) / 2;
      if (clampedLat >= mid) {
        charBits = charBits * 2 + 1;
        latLo = mid;
      } else {
        charBits = charBits * 2;
        latHi = mid;
      }
    }
    isLngBit = !isLngBit;
    bitCount += 1;
    if (bitCount === 5) {
      hash += BASE32[charBits];
      charBits = 0;
      bitCount = 0;
    }
  }
  return hash;
}
