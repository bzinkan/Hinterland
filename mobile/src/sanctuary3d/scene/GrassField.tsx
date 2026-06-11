/**
 * Instanced wind-swept grass -- the single strongest "open world" signal
 * (the Zelda meadow). Tapered triangle blades, base->tip color gradient,
 * per-instance tint variance, and a vertex-shader sway injected into
 * MeshToonMaterial via onBeforeCompile (keeps toon shading, fog, and
 * instancing support for free; works on expo-gl and WebGL alike).
 *
 * Placement is seeded and deterministic: blades scatter across grassy
 * ground, densest in the meadow, never in the pond/urban/cliff areas.
 */

import React, { useMemo, useRef } from "react";
import * as THREE from "three";

import { fnv1a32, mulberry32 } from "@/src/sanctuary3d/placement/seeds";
import { ZONE_LAYOUT } from "@/src/sanctuary3d/placement/zoneAnchors";
import { useFrame } from "@/src/sanctuary3d/r3f";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";
import { heightAt, WATER_LEVEL } from "@/src/sanctuary3d/terrain/heightfield";

const BLADE_HEIGHT = 0.24;
const COUNT_TARGET = 2400;

type GrassInstance = { x: number; y: number; z: number; yaw: number; scale: number; tint: number };

function scatterBlades(): GrassInstance[] {
  const rng = mulberry32(fnv1a32("sanctuary-grass-v1"));
  const meadow = ZONE_LAYOUT.meadow;
  const pond = ZONE_LAYOUT.pond;
  const urban = ZONE_LAYOUT.urban;
  const out: GrassInstance[] = [];
  let attempts = 0;
  while (out.length < COUNT_TARGET && attempts < COUNT_TARGET * 8) {
    attempts++;
    const x = (rng() - 0.5) * 17;
    const z = (rng() - 0.5) * 17;
    const y = heightAt(x, z);
    if (y < WATER_LEVEL + 0.12 || y > 2.1) continue; // water, basin, high rock
    if (z > 3.7) continue; // cliff lip
    if (Math.hypot(x - pond.center[0], z - pond.center[2]) < pond.radius * 1.15) continue;
    if (Math.hypot(x - urban.center[0], z - urban.center[2]) < urban.radius * 0.9) continue;
    const inMeadow =
      Math.hypot(x - meadow.center[0], z - meadow.center[2]) < meadow.radius * 1.35;
    if (!inMeadow && rng() > 0.22) continue; // meadow is densest; elsewhere sparse so the terrain shape reads
    out.push({
      x,
      y,
      z,
      yaw: rng() * Math.PI * 2,
      scale: 0.5 + rng() * 0.45,
      tint: 0.82 + rng() * 0.3,
    });
  }
  return out;
}

export function GrassField({ dormant }: { dormant: boolean }) {
  const shaderRef = useRef<{ uniforms: { uTime: { value: number } } } | null>(null);

  const { geometry, material, instances } = useMemo(() => {
    // One tapered blade: 3 vertices, base->tip color ramp baked as vertex
    // colors. The shader sways vertices by their relative height.
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array([
      -0.035, 0, 0,
      0.035, 0, 0,
      0, BLADE_HEIGHT, 0,
    ]);
    const normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]);
    const colors = new Float32Array([
      0.29, 0.44, 0.20, // base: deep valley green
      0.29, 0.44, 0.20,
      0.55, 0.56, 0.30, // tip: olive-gold
    ]);
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    const mat = new THREE.MeshToonMaterial({
      vertexColors: true,
      gradientMap: toonRamp(),
      side: THREE.DoubleSide,
    });
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uTime = { value: 0 };
      shader.vertexShader = shader.vertexShader
        .replace(
          "#include <common>",
          "#include <common>\nuniform float uTime;",
        )
        .replace(
          "#include <begin_vertex>",
          [
            "#include <begin_vertex>",
            "{",
            "  float swayWeight = position.y / 0.24;",
            "  swayWeight *= swayWeight;",
            "  vec3 blade_origin = vec3(0.0);",
            "  #ifdef USE_INSTANCING",
            "    blade_origin = (instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;",
            "  #endif",
            "  float phase = blade_origin.x * 0.9 + blade_origin.z * 0.7;",
            "  float gust = sin(uTime * 1.5 + phase) * 0.6 + sin(uTime * 0.6 + phase * 0.35) * 0.4;",
            "  transformed.x += gust * 0.085 * swayWeight;",
            "  transformed.z += cos(uTime * 1.1 + phase) * 0.04 * swayWeight;",
            "}",
          ].join("\n"),
        );
      shaderRef.current = shader as unknown as {
        uniforms: { uTime: { value: number } };
      };
    };

    return { geometry: geo, material: mat, instances: scatterBlades() };
  }, []);

  const meshRef = useRef<THREE.InstancedMesh>(null);

  // Write instance matrices + tints once.
  useMemo(() => {
    // Deferred to ref callback below; placeholder to keep deps simple.
    return null;
  }, []);

  useFrame((state) => {
    if (shaderRef.current) {
      shaderRef.current.uniforms.uTime.value = state.clock.elapsedTime;
    }
  });

  return (
    <instancedMesh
      ref={(mesh) => {
        meshRef.current = mesh;
        if (!mesh || mesh.userData.__grassInit) return;
        mesh.userData.__grassInit = true;
        const m = new THREE.Matrix4();
        const q = new THREE.Quaternion();
        const up = new THREE.Vector3(0, 1, 0);
        const color = new THREE.Color();
        instances.forEach((blade, i) => {
          q.setFromAxisAngle(up, blade.yaw);
          m.compose(
            new THREE.Vector3(blade.x, blade.y, blade.z),
            q,
            new THREE.Vector3(blade.scale, blade.scale, blade.scale),
          );
          mesh.setMatrixAt(i, m);
          const t = dormant ? 0.55 + blade.tint * 0.1 : blade.tint;
          color.setRGB(t, dormant ? t : t, dormant ? t : t * 0.96);
          mesh.setColorAt(i, color);
        });
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      }}
      args={[geometry, material, instances.length]}
      frustumCulled={false}
    />
  );
}
