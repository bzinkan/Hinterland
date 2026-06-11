/**
 * Gradient sky dome: zenith color blending to a pale horizon (the single
 * cheapest "open world" signal there is). A big back-side sphere with a
 * tiny shader -- no textures, GLSL 100-compatible for expo-gl.
 */

import React, { useMemo } from "react";
import * as THREE from "three";

const VERT = /* glsl */ `
  varying vec3 vWorldPosition;
  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldPosition = worldPosition.xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAG = /* glsl */ `
  uniform vec3 uTopColor;
  uniform vec3 uHorizonColor;
  varying vec3 vWorldPosition;
  void main() {
    float h = normalize(vWorldPosition).y;
    float t = smoothstep(-0.05, 0.45, h);
    gl_FragColor = vec4(mix(uHorizonColor, uTopColor, t), 1.0);
  }
`;

export function SkyDome({
  topColor,
  horizonColor,
}: {
  topColor: string;
  horizonColor: string;
}) {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: FRAG,
        uniforms: {
          uTopColor: { value: new THREE.Color(topColor) },
          uHorizonColor: { value: new THREE.Color(horizonColor) },
        },
        side: THREE.BackSide,
        depthWrite: false,
        fog: false,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  // Update uniforms in place when the palette changes (season/tone).
  material.uniforms.uTopColor.value.set(topColor);
  material.uniforms.uHorizonColor.value.set(horizonColor);

  return (
    <mesh material={material} renderOrder={-1}>
      <sphereGeometry args={[60, 24, 12]} />
    </mesh>
  );
}
