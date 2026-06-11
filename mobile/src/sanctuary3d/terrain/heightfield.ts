/**
 * Sculpted island terrain (L1 look-dev, ADR 0011). Pure, seeded, and
 * deterministic -- the same module produces both the render mesh arrays and
 * the `heightAt(x, z)` sampler used for element/grass placement, so nothing
 * ever floats or sinks.
 *
 * Shape language (BotW-flavored rolling island):
 *   - meadow: soft open rise (front-left)
 *   - woodland: raised back ridge
 *   - pond: carved depression with a flat water table
 *   - urban: small level plateau (front-right)
 *   - front edge: sheer cliff face (the soil cross-section lives there)
 *   - rim: island falls away to a rocky skirt below "sea level"
 *
 * No three imports; arrays feed a BufferGeometry in TerrainMesh.tsx.
 */

import { fnv1a32, mulberry32 } from "@/src/sanctuary3d/placement/seeds";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";

export const TERRAIN_SIZE = 26; // world units, square, centered on origin
export const TERRAIN_SEGMENTS = 96; // grid resolution per side
export const ISLAND_RADIUS = 9.2;
export const WATER_LEVEL = -0.18; // pond water surface height
const SEED = fnv1a32("dragonfly-sanctuary-island-v1");

// ---------------------------------------------------------------------------
// Seeded value noise (lattice + smoothstep), 3 octaves.
// ---------------------------------------------------------------------------

const latticeRng = mulberry32(SEED);
const LATTICE_SIZE = 64;
const lattice: number[] = [];
for (let i = 0; i < LATTICE_SIZE * LATTICE_SIZE; i++) {
  lattice.push(latticeRng());
}

function latticeAt(ix: number, iz: number): number {
  const x = ((ix % LATTICE_SIZE) + LATTICE_SIZE) % LATTICE_SIZE;
  const z = ((iz % LATTICE_SIZE) + LATTICE_SIZE) % LATTICE_SIZE;
  return lattice[z * LATTICE_SIZE + x];
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

function valueNoise(x: number, z: number): number {
  const ix = Math.floor(x);
  const iz = Math.floor(z);
  const fx = smooth(x - ix);
  const fz = smooth(z - iz);
  const a = latticeAt(ix, iz);
  const b = latticeAt(ix + 1, iz);
  const c = latticeAt(ix, iz + 1);
  const d = latticeAt(ix + 1, iz + 1);
  return (
    a * (1 - fx) * (1 - fz) +
    b * fx * (1 - fz) +
    c * (1 - fx) * fz +
    d * fx * fz
  );
}

/** Fractal noise in [0, 1]. */
function fbm(x: number, z: number): number {
  return (
    0.5 * valueNoise(x * 0.35, z * 0.35) +
    0.3 * valueNoise(x * 0.8 + 13.7, z * 0.8 + 7.3) +
    0.2 * valueNoise(x * 1.9 + 41.1, z * 1.9 + 23.9)
  );
}

// ---------------------------------------------------------------------------
// Island shaping
// ---------------------------------------------------------------------------

function dist(x: number, z: number, cx: number, cz: number): number {
  return Math.hypot(x - cx, z - cz);
}

/** 1 inside, 0 outside, smooth between (inner -> outer radius). */
function falloff(d: number, inner: number, outer: number): number {
  if (d <= inner) return 1;
  if (d >= outer) return 0;
  return smooth(1 - (d - inner) / (outer - inner));
}

/**
 * Terrain height at world (x, z). Continuous; used by the mesh builder,
 * element placement, grass scattering, and the camera rig alike.
 */
export function heightAt(x: number, z: number): number {
  const meadow = ZONE_LAYOUT.meadow.center;
  const woodland = ZONE_LAYOUT.woodland.center;
  const pond = ZONE_LAYOUT.pond.center;
  const urban = ZONE_LAYOUT.urban.center;

  const r = Math.hypot(x, z);

  // Base rolling ground.
  let h = 0.55 + (fbm(x, z) - 0.5) * 1.5;

  // Woodland back ridge.
  h += 1.15 * falloff(dist(x, z, woodland[0], woodland[2]), 0.4, 3.4);

  // Meadow: gentle open rise, flattened toward a friendly slope.
  const meadowW = falloff(dist(x, z, meadow[0], meadow[2]), 0.3, 2.6);
  h = h * (1 - meadowW * 0.65) + (0.62 + 0.1 * fbm(x * 2, z * 2)) * meadowW * 0.65;

  // Urban plateau: small level terrace.
  const urbanW = falloff(dist(x, z, urban[0], urban[2]), 0.2, 1.7);
  h = h * (1 - urbanW * 0.85) + 0.5 * urbanW * 0.85;

  // Pond: carve a basin below the water table.
  const pondW = falloff(dist(x, z, pond[0], pond[2]), 0.1, 2.0);
  h = h * (1 - pondW) + (WATER_LEVEL - 0.5) * pondW;

  // Front cliff: sheer drop past the urban terrace (the soil face).
  if (z > 4.0) {
    h -= (z - 4.0) * 2.4;
  }

  // Island rim: fall away to the rocky skirt.
  const rim = falloff(r, ISLAND_RADIUS * 0.72, ISLAND_RADIUS);
  h = h * rim + -2.6 * (1 - rim);

  return h;
}

// ---------------------------------------------------------------------------
// Mesh arrays
// ---------------------------------------------------------------------------

export type TerrainArrays = {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  /** Vertex count per side (segments + 1). */
  side: number;
};

let cachedArrays: TerrainArrays | null = null;

export function buildTerrainArrays(): TerrainArrays {
  if (cachedArrays) return cachedArrays;
  const side = TERRAIN_SEGMENTS + 1;
  const positions = new Float32Array(side * side * 3);
  const normals = new Float32Array(side * side * 3);
  const indices = new Uint32Array(TERRAIN_SEGMENTS * TERRAIN_SEGMENTS * 6);

  const half = TERRAIN_SIZE / 2;
  const step = TERRAIN_SIZE / TERRAIN_SEGMENTS;

  for (let gz = 0; gz < side; gz++) {
    for (let gx = 0; gx < side; gx++) {
      const i = (gz * side + gx) * 3;
      const x = -half + gx * step;
      const z = -half + gz * step;
      positions[i] = x;
      positions[i + 1] = heightAt(x, z);
      positions[i + 2] = z;
    }
  }

  // Normals via central differences on the continuous height function
  // (smoother than face-averaged normals at this grid density).
  const eps = step * 0.75;
  for (let gz = 0; gz < side; gz++) {
    for (let gx = 0; gx < side; gx++) {
      const i = (gz * side + gx) * 3;
      const x = positions[i];
      const z = positions[i + 2];
      const hx = heightAt(x + eps, z) - heightAt(x - eps, z);
      const hz = heightAt(x, z + eps) - heightAt(x, z - eps);
      // Normal of surface y = h(x, z): (-dh/dx, 1, -dh/dz) normalized.
      const nx = -hx / (2 * eps);
      const nz = -hz / (2 * eps);
      const len = Math.hypot(nx, 1, nz);
      normals[i] = nx / len;
      normals[i + 1] = 1 / len;
      normals[i + 2] = nz / len;
    }
  }

  let t = 0;
  for (let gz = 0; gz < TERRAIN_SEGMENTS; gz++) {
    for (let gx = 0; gx < TERRAIN_SEGMENTS; gx++) {
      const a = gz * side + gx;
      const b = a + 1;
      const c = a + side;
      const d = c + 1;
      indices[t++] = a;
      indices[t++] = c;
      indices[t++] = b;
      indices[t++] = b;
      indices[t++] = c;
      indices[t++] = d;
    }
  }

  cachedArrays = { positions, normals, indices, side };
  return cachedArrays;
}
