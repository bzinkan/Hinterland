/**
 * Shared toon gradient ramp (BotW-soft: few steps, gentle transitions via
 * linear filtering). Generated once -- no texture assets, no fetch.
 */

import * as THREE from "three";

let cached: THREE.DataTexture | null = null;

export function toonRamp(): THREE.DataTexture {
  if (cached) return cached;
  // 4 luminance steps, soft-banded by LinearFilter.
  const steps = [120, 168, 215, 255];
  const data = new Uint8Array(steps.length * 4);
  steps.forEach((v, i) => {
    data[i * 4] = v;
    data[i * 4 + 1] = v;
    data[i * 4 + 2] = v;
    data[i * 4 + 3] = 255;
  });
  const texture = new THREE.DataTexture(data, steps.length, 1, THREE.RGBAFormat);
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  cached = texture;
  return texture;
}
