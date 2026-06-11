/**
 * Camera rig (L1): critically-damped glide between the vista and zone-dive
 * framings. Frame-rate independent easing (1 - exp(-k*dt)); all camera
 * math lives on the JS thread in useFrame per ADR 0011 (Reanimated stays
 * out of the GL path). Gestures (pinch/orbit) layer on in M4.
 */

import { useRef } from "react";
import * as THREE from "three";

import type { CameraView } from "@/src/sanctuary3d/camera/cameraViews";
import { useFrame } from "@/src/sanctuary3d/r3f";

const EASE_K = 2.6;

export function CameraRig({ view }: { view: CameraView }) {
  const current = useRef<{ pos: THREE.Vector3; look: THREE.Vector3 } | null>(null);

  useFrame((state, delta) => {
    if (!current.current) {
      current.current = {
        pos: new THREE.Vector3(...view.position),
        look: new THREE.Vector3(...view.lookAt),
      };
    }
    const target = current.current;
    const alpha = 1 - Math.exp(-EASE_K * Math.min(delta, 0.1));
    target.pos.lerp(new THREE.Vector3(...view.position), alpha);
    target.look.lerp(new THREE.Vector3(...view.lookAt), alpha);
    state.camera.position.copy(target.pos);
    state.camera.lookAt(target.look);
  });

  return null;
}
