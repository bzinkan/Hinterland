/**
 * Renders N seeded instances of one manifest GLB with InstancedMesh --
 * one draw call per (model, material part) regardless of count, which is
 * how 60+ trees and rocks stay inside the frame budget.
 *
 * Materials are converted to the shared toon ramp on load (the pipeline
 * ships palette-snapped baseColor; toonification keeps the whole scene in
 * one shading language). `dormant` tints instances toward the sleeping
 * grey via instanceColor.
 */

import React, { useMemo } from "react";
import * as THREE from "three";

import { useSanctuaryGLTF } from "@/src/sanctuary3d/assets/useSanctuaryGLTF";
import type { ElementTransform } from "@/src/sanctuary3d/placement/seededLayout";
import { toonRamp } from "@/src/sanctuary3d/scene/toonRamp";

type Part = {
  geometry: THREE.BufferGeometry;
  material: THREE.Material;
  local: THREE.Matrix4;
};

const toonCache = new Map<string, THREE.MeshToonMaterial>();

function toonify(source: THREE.Material): THREE.Material {
  const std = source as THREE.MeshStandardMaterial;
  if (!std.color) return source;
  const key = std.color.getHexString();
  let cached = toonCache.get(key);
  if (!cached) {
    cached = new THREE.MeshToonMaterial({
      color: std.color.clone(),
      gradientMap: toonRamp(),
    });
    toonCache.set(key, cached);
  }
  return cached;
}

export function InstancedGLB({
  moduleId,
  transforms,
  dormant = false,
}: {
  moduleId: number;
  transforms: ElementTransform[];
  dormant?: boolean;
}) {
  const { gltf } = useSanctuaryGLTF(moduleId);

  const parts = useMemo<Part[] | null>(() => {
    if (!gltf) return null;
    gltf.scene.updateMatrixWorld(true);
    const collected: Part[] = [];
    gltf.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      // Multi-material meshes are rare post-pipeline (palette() collapses
      // them); render group 0 only and accept the simplification.
      collected.push({
        geometry: mesh.geometry as THREE.BufferGeometry,
        material: toonify(materials[0]),
        local: mesh.matrixWorld.clone(),
      });
    });
    return collected;
  }, [gltf]);

  if (!parts || transforms.length === 0) return null;

  return (
    <group>
      {parts.map((part, index) => (
        <InstancedPart
          key={index}
          part={part}
          transforms={transforms}
          dormant={dormant}
        />
      ))}
    </group>
  );
}

function InstancedPart({
  part,
  transforms,
  dormant,
}: {
  part: Part;
  transforms: ElementTransform[];
  dormant: boolean;
}) {
  // Instance matrices/colors are written once per (part, transforms,
  // dormant) via the ref callback; a stable key forces a fresh mesh when
  // dormancy flips so the colors rewrite.
  return (
    <instancedMesh
      key={dormant ? "dormant" : "awake"}
      args={[part.geometry, part.material, transforms.length]}
      frustumCulled={false}
      ref={(mesh: THREE.InstancedMesh | null) => {
        if (!mesh || mesh.userData.__dressed) return;
        mesh.userData.__dressed = true;
        const placement = new THREE.Matrix4();
        const final = new THREE.Matrix4();
        const quaternion = new THREE.Quaternion();
        const up = new THREE.Vector3(0, 1, 0);
        const color = new THREE.Color(dormant ? "#9B9D96" : "#FFFFFF");
        transforms.forEach((t, i) => {
          quaternion.setFromAxisAngle(up, t.rotationY);
          placement.compose(
            new THREE.Vector3(t.position[0], t.position[1], t.position[2]),
            quaternion,
            new THREE.Vector3(t.scale, t.scale, t.scale),
          );
          final.multiplyMatrices(placement, part.local);
          mesh.setMatrixAt(i, final);
          mesh.setColorAt(i, color);
        });
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      }}
    />
  );
}
