/**
 * M0 spike for Sanctuary 3D (ADR 0011). Dev-only route -- verifies on a
 * PHYSICAL Android device that our exact asset path works end to end:
 *
 *   gltf-transform output (quantized, textureless GLB)
 *     -> Metro bundled asset -> expo-asset -> GLTFLoader.parse (Hermes)
 *     -> @react-three/fiber Canvas on expo-gl (new architecture)
 *     -> tap raycast -> RN state update
 *
 * Pass criteria (record results in docs/adr/0011-sanctuary-3d-rendering.md):
 *   1. The low-poly tree renders lit, no blank canvas, no crash.
 *   2. FPS readout holds ~60 on a mid-tier device.
 *   3. Tapping the tree increments the tap counter (raycast works).
 *
 * This screen is throwaway: it is replaced by the real Sanctuary3DScreen
 * and removed once M2 lands. Not linked from any kid-facing surface.
 */

import React, { Suspense, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { router } from "expo-router";
import type { Group } from "three";

import { Canvas, useFrame } from "@/src/sanctuary3d/r3f";

import { env } from "@/src/config/env";
import { useSanctuaryGLTF } from "@/src/sanctuary3d/assets/useSanctuaryGLTF";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const SPIKE_TREE = require("../assets/sanctuary/models/dev/spike-tree.glb");

export default function DevSanctuarySpikeScreen() {
  // Dev-only surface. Harmless if reached in a release build, but make it
  // inert anyway: production/play-internal users see a dead-end note.
  if (!__DEV__ && env.appEnv !== "development" && env.appEnv !== "preview") {
    return (
      <View style={styles.center}>
        <Text style={styles.subtle}>Not available.</Text>
      </View>
    );
  }

  return <Spike />;
}

function Spike() {
  const [taps, setTaps] = useState(0);
  const [fps, setFps] = useState<number | null>(null);
  const [glReady, setGlReady] = useState(false);

  return (
    <View style={styles.root}>
      <Canvas
        style={styles.canvas}
        camera={{ position: [0, 1.6, 4], fov: 45 }}
        onCreated={(state) => {
          // Residual new-arch workaround lineage (r3f #3399): filter the
          // UNPACK_FLIP_Y_WEBGL pixelStorei expo-gl does not implement.
          const gl = state.gl.getContext() as WebGLRenderingContext & {
            pixelStorei: (pname: number, param: unknown) => void;
          };
          const basePixelStorei = gl.pixelStorei.bind(gl);
          gl.pixelStorei = (pname: number, param: unknown) => {
            if (pname === gl.UNPACK_FLIP_Y_WEBGL) return;
            basePixelStorei(pname, param as number);
          };
          setGlReady(true);
        }}
      >
        <color attach="background" args={["#DCEAF2"]} />
        <hemisphereLight args={["#cfe8ff", "#6b8f5a", 0.9]} />
        <directionalLight position={[3, 5, 2]} intensity={1.4} />
        <Suspense fallback={null}>
          <SpikeTree onTap={() => setTaps((t) => t + 1)} />
        </Suspense>
        <FpsProbe onSample={setFps} />
        {/* ground disc so the tree is not floating in a void */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]}>
          <circleGeometry args={[2.2, 24]} />
          <meshLambertMaterial color="#9DBE7E" />
        </mesh>
      </Canvas>
      <View style={styles.hud} pointerEvents="box-none">
        <Text style={styles.hudText}>
          GL: {glReady ? "ready" : "starting…"} · FPS:{" "}
          {fps === null ? "—" : Math.round(fps)} · taps: {taps}
        </Text>
        <Pressable
          accessibilityRole="button"
          style={styles.closeButton}
          onPress={() => router.back()}
        >
          <Text style={styles.closeButtonText}>Close spike</Text>
        </Pressable>
      </View>
    </View>
  );
}

function SpikeTree({ onTap }: { onTap: () => void }) {
  const group = useRef<Group>(null);
  const { status, gltf, error } = useSanctuaryGLTF(SPIKE_TREE as number);

  useFrame((_, delta) => {
    if (group.current) {
      group.current.rotation.y += delta * 0.4; // slow idle spin
    }
  });

  if (status === "error") {
    // Surface load failures loudly in the spike -- this is exactly what
    // M0 exists to catch. (console.error is visible in the dev client.)
    console.error("spike GLB load failed", error);
    return null;
  }
  if (status !== "ready") {
    return null;
  }

  return (
    <group
      ref={group}
      onClick={(event) => {
        event.stopPropagation();
        onTap();
      }}
    >
      <primitive object={gltf.scene} />
    </group>
  );
}

/** Rolling FPS sample pushed to RN state once a second. */
function FpsProbe({ onSample }: { onSample: (fps: number) => void }) {
  const frames = useRef(0);
  const elapsed = useRef(0);
  useFrame((_, delta) => {
    frames.current += 1;
    elapsed.current += delta;
    if (elapsed.current >= 1) {
      onSample(frames.current / elapsed.current);
      frames.current = 0;
      elapsed.current = 0;
    }
  });
  return null;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#DCEAF2" },
  canvas: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  subtle: { color: "#666", fontSize: 14 },
  hud: {
    position: "absolute",
    top: 48,
    left: 16,
    right: 16,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  hudText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#2A2A2A",
    backgroundColor: "#FFFFFFCC",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    overflow: "hidden",
  },
  closeButton: {
    backgroundColor: "#3F6B40",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
  },
  closeButtonText: { color: "#FFF", fontSize: 13, fontWeight: "500" },
});
