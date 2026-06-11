/**
 * Sanctuary 3D living diorama -- screen shell (M1) + data-driven scene (M2).
 *
 * M2: the island renders from buildScenePlan(snapshot) -- zones color in at
 * their depth tiers, elements place deterministically from the manifest
 * (typed fallback shapes until asset milestones land), mystery cues render
 * as dormant silhouettes, and tapping an element opens the SAME
 * ElementInspectModal the 2D screen uses. A dev-only overlay (development/
 * preview envs) can step the island through tiers 0->50 with synthesized
 * sample data -- it never touches a real account's view.
 *
 * Still ahead: ambient animation (M3), pinch/orbit gestures (M4), the
 * reveal sequence (M5).
 *
 * Resilience contract (final since M1):
 * - Any render error inside the canvas -> GL crash recorded -> this session
 *   falls back to Sanctuary2DScreen in place. Three strikes pin 2D until an
 *   app update (src/sanctuary3d/flagDecision.ts).
 * - Mount watchdog: no first rendered frame within 5 s counts as a crash --
 *   native GL failures can present as a silent hang (expo #41543).
 * - All failure paths land on the permanent 2D screen, never a dead canvas.
 *
 * Inherits every Sanctuary invariant: read-only authored content, no
 * precise location, offline render (bundled assets only), no analytics.
 */

import React, {
  Component,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import type { SanctuaryElementDto, SanctuaryZoneId } from "@/src/api/sanctuary";
import { env } from "@/src/config/env";
import Sanctuary2DScreen from "@/src/sanctuary/Sanctuary2DScreen";
import { ElementInspectModal } from "@/src/sanctuary/panels/ElementInspectModal";
import { useSanctuary } from "@/src/sanctuary/useSanctuary";
import { CameraRig } from "@/src/sanctuary3d/camera/CameraRig";
import { VISTA_VIEW, ZONE_VIEWS } from "@/src/sanctuary3d/camera/cameraViews";
import {
  DEV_TIER_LADDER,
  makeSampleSnapshot,
} from "@/src/sanctuary3d/dev/sampleSnapshot";
import { useSanctuary3DPrefs } from "@/src/sanctuary3d/prefs";
import { Canvas, useFrame } from "@/src/sanctuary3d/r3f";
import { buildScenePlan, type ScenePlan } from "@/src/sanctuary3d/scenePlan";
import { IslandScene } from "@/src/sanctuary3d/scene/IslandScene";

const FIRST_FRAME_WATCHDOG_MS = 5_000;
const DEV_CONTROLS_ENABLED =
  env.appEnv === "development" || env.appEnv === "preview";

export default function Sanctuary3DScreen() {
  const { data, isLoading, isError, error, refetch } = useSanctuary();
  const recordGlCrash = useSanctuary3DPrefs((s) => s.recordGlCrash);
  const [sessionFallback, setSessionFallback] = useState(false);
  const [inspected, setInspected] = useState<SanctuaryElementDto | null>(null);
  const [focusedZone, setFocusedZone] = useState<SanctuaryZoneId | null>(null);

  // Dev preview: null = off (real data), number = sample snapshot at tier.
  const [devTier, setDevTier] = useState<number | null>(null);

  const snapshot = useMemo(() => {
    if (DEV_CONTROLS_ENABLED && devTier !== null) {
      return makeSampleSnapshot(devTier);
    }
    return data ?? null;
  }, [data, devTier]);

  const plan: ScenePlan | null = useMemo(
    () => (snapshot ? buildScenePlan(snapshot) : null),
    [snapshot],
  );

  if (sessionFallback) {
    return <Sanctuary2DScreen />;
  }

  if (!plan && isLoading) {
    return (
      <SafeAreaView style={styles.centered} edges={["top"]}>
        <ActivityIndicator />
        <Text style={styles.centeredText}>Waking up…</Text>
      </SafeAreaView>
    );
  }

  if (!plan) {
    return (
      <SafeAreaView style={styles.centered} edges={["top"]}>
        <Text style={styles.centeredTitle}>Couldn't reach your Sanctuary</Text>
        <Text style={styles.centeredText}>
          {isError ? (error?.message ?? "Try again in a moment.") : "Try again in a moment."}
        </Text>
        <Pressable
          accessibilityRole="button"
          style={styles.retryButton}
          onPress={() => void refetch()}
        >
          <Text style={styles.retryButtonText}>Retry</Text>
        </Pressable>
        {DEV_CONTROLS_ENABLED ? (
          <Pressable
            accessibilityRole="button"
            style={[styles.retryButton, styles.devButton]}
            onPress={() => setDevTier(3)}
          >
            <Text style={styles.retryButtonText}>Dev: preview sample island</Text>
          </Pressable>
        ) : null}
      </SafeAreaView>
    );
  }

  return (
    <GlCrashBoundary
      onCrash={() => {
        recordGlCrash();
        setSessionFallback(true);
      }}
      fallback={<Sanctuary2DScreen />}
    >
      <SafeAreaView style={styles.root} edges={["top"]}>
        <View style={styles.header}>
          <Text style={styles.headerTitle}>Sanctuary</Text>
          <Text style={styles.headerSubtitle}>
            A quiet place that grows when you go outside.
          </Text>
        </View>
        <View style={styles.canvasWrap}>
          <IslandCanvas
            plan={plan}
            focusedZone={focusedZone}
            onInspect={setInspected}
            onFocusZone={setFocusedZone}
            onMountFailure={() => {
              recordGlCrash();
              setSessionFallback(true);
            }}
          />
          {focusedZone ? (
            <Pressable
              accessibilityRole="button"
              style={styles.backChip}
              onPress={() => setFocusedZone(null)}
            >
              <Text style={styles.backChipText}>‹ Back to island</Text>
            </Pressable>
          ) : null}
          {DEV_CONTROLS_ENABLED ? (
            <DevTierStepper devTier={devTier} onSelect={setDevTier} />
          ) : null}
        </View>
        <ElementInspectModal
          element={inspected}
          onClose={() => setInspected(null)}
        />
      </SafeAreaView>
    </GlCrashBoundary>
  );
}

// ---------------------------------------------------------------------------
// Canvas + watchdog
// ---------------------------------------------------------------------------

function IslandCanvas({
  plan,
  focusedZone,
  onInspect,
  onFocusZone,
  onMountFailure,
}: {
  plan: ScenePlan;
  focusedZone: SanctuaryZoneId | null;
  onInspect: (element: SanctuaryElementDto) => void;
  onFocusZone: (zone: SanctuaryZoneId | null) => void;
  onMountFailure: () => void;
}) {
  const firstFrameSeen = useRef(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (!firstFrameSeen.current) {
        onMountFailure();
      }
    }, FIRST_FRAME_WATCHDOG_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Canvas
      style={styles.canvas}
      camera={{
        position: [...VISTA_VIEW.position],
        // Wide lens: portrait phones crush horizontal FOV (vfov 50 ≈ hfov 24
        // on 9:19.5) -- 68 vertical restores the open-world wide shot.
        fov: 68,
        near: 0.2,
        far: 140,
      }}
      onCreated={(state) => {
        // New-architecture workaround lineage (r3f #3399): expo-gl does not
        // implement UNPACK_FLIP_Y_WEBGL; filter it out of pixelStorei calls.
        // Harmless no-op on web.
        const gl = state.gl.getContext() as WebGLRenderingContext & {
          pixelStorei: (pname: number, param: unknown) => void;
        };
        const basePixelStorei = gl.pixelStorei.bind(gl);
        gl.pixelStorei = (pname: number, param: unknown) => {
          if (pname === gl.UNPACK_FLIP_Y_WEBGL) return;
          basePixelStorei(pname, param as number);
        };
      }}
    >
      <FirstFramePing
        onFirstFrame={() => {
          firstFrameSeen.current = true;
        }}
      />
      <CameraRig view={focusedZone ? ZONE_VIEWS[focusedZone] : VISTA_VIEW} />
      <color attach="background" args={[plan.palette.horizon]} />
      <fog attach="fog" args={[plan.palette.fog, 16, 46]} />
      <hemisphereLight
        args={[plan.palette.hemiSky, plan.palette.hemiGround, 0.85]}
      />
      <directionalLight
        position={[6, 9, 3]}
        color={plan.palette.sunColor}
        intensity={plan.palette.sunIntensity}
      />
      <IslandScene
        plan={plan}
        onInspect={onInspect}
        onFocusZone={onFocusZone}
      />
    </Canvas>
  );
}

function FirstFramePing({ onFirstFrame }: { onFirstFrame: () => void }) {
  const pinged = useRef(false);
  useFrame(() => {
    if (!pinged.current) {
      pinged.current = true;
      onFirstFrame();
    }
  });
  return null;
}

// ---------------------------------------------------------------------------
// Dev tier stepper (development/preview builds only)
// ---------------------------------------------------------------------------

function DevTierStepper({
  devTier,
  onSelect,
}: {
  devTier: number | null;
  onSelect: (tier: number | null) => void;
}) {
  return (
    <View style={styles.devBar} pointerEvents="box-none">
      <Pressable
        accessibilityRole="button"
        style={[styles.devChip, devTier === null ? styles.devChipActive : null]}
        onPress={() => onSelect(null)}
      >
        <Text style={styles.devChipText}>live</Text>
      </Pressable>
      {DEV_TIER_LADDER.map((tier) => (
        <Pressable
          key={tier}
          accessibilityRole="button"
          style={[styles.devChip, devTier === tier ? styles.devChipActive : null]}
          onPress={() => onSelect(tier)}
        >
          <Text style={styles.devChipText}>{tier}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Crash boundary
// ---------------------------------------------------------------------------

class GlCrashBoundary extends Component<
  { children: ReactNode; fallback: ReactNode; onCrash: () => void },
  { crashed: boolean }
> {
  state = { crashed: false };

  static getDerivedStateFromError() {
    return { crashed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("sanctuary3d: canvas crashed, falling back to 2D", error);
    this.props.onCrash();
  }

  render() {
    return this.state.crashed ? this.props.fallback : this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F7F6F2" },
  canvasWrap: { flex: 1 },
  canvas: { flex: 1 },
  header: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 8 },
  headerTitle: { fontSize: 24, fontWeight: "700", color: "#2A2A2A" },
  headerSubtitle: { fontSize: 13, color: "#666", marginTop: 2 },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#F7F6F2",
  },
  centeredTitle: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  centeredText: { fontSize: 14, color: "#666", marginTop: 8 },
  retryButton: {
    marginTop: 16,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
    backgroundColor: "#3F6B40",
  },
  retryButtonText: { color: "#fff", fontSize: 14, fontWeight: "500" },
  devButton: { backgroundColor: "#4F4F4F" },
  devBar: {
    position: "absolute",
    bottom: 12,
    left: 12,
    right: 12,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
  },
  devChip: {
    paddingVertical: 5,
    paddingHorizontal: 10,
    borderRadius: 999,
    backgroundColor: "#2A2A2ACC",
  },
  devChipActive: { backgroundColor: "#3F6B40" },
  devChipText: { color: "#FFF", fontSize: 12, fontWeight: "600" },
  backChip: {
    position: "absolute",
    top: 10,
    left: 12,
    paddingVertical: 7,
    paddingHorizontal: 12,
    borderRadius: 999,
    backgroundColor: "#2A2A2ACC",
  },
  backChipText: { color: "#FFF", fontSize: 13, fontWeight: "600" },
});
