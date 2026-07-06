/**
 * Sanctuary diorama screen (D7, recomposed by the ADR 0012 addendum).
 * Owns everything AROUND the canvas:
 *  - the guarded Skia require (a dev client predating the native rebuild
 *    quietly gets the classic screen, never a crash),
 *  - the real data path: useSanctuary -> SanctuarySnapshotDto, with
 *    loading and error states (no sample snapshots),
 *  - the chooser <-> scene flow: the kid lands on the native biome
 *    chooser (no canvas) and taps into one zone's full-bleed 2.5D scene;
 *    Back returns to the chooser (plain local state -- no new routes),
 *  - the pending-wake ledger: the gate outlives every chooser <-> scene
 *    switch, so it keeps per-zone dormant history across snapshots and
 *    records wakes that land while the woken zone's scene is unmounted;
 *    the next scene mount for such a zone plays the held wake ceremony
 *    (BiomeScene's initiallyWaking) and reports back via onWakeEnd,
 *  - the render watchdog: 3-strike crash latch + first-frame timeout with
 *    the same semantics as the 3D latch, armed PER SCENE MOUNT (the
 *    chooser is plain RN and can never strike). The scene canvas sits
 *    inside an error boundary; a crash OR a canvas that never draws
 *    within the timeout records ONE persisted strike
 *    (prefs.recordRenderCrash) and swaps this session to
 *    Sanctuary2DScreen. At MAX_RENDER_CRASHES the tab route pins to the
 *    classic screen across launches.
 *
 * The tab route (app/(tabs)/sanctuary.tsx) only mounts this component
 * when decideSanctuaryDiorama says diorama.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
} from "react-native";

import type { SanctuarySnapshotDto, SanctuaryZoneId } from "@/src/api/sanctuary";
import { useSanctuaryDioramaPrefs } from "@/src/sanctuary/diorama/prefs";
import { BiomeChooserScreen } from "@/src/sanctuary/dioramaui/BiomeChooserScreen";
import { BiomeScene } from "@/src/sanctuary/dioramaui/BiomeSceneScreen";
import { RenderBoundary } from "@/src/sanctuary/dioramaui/RenderBoundary";
import {
  FIRST_FRAME_TIMEOUT_MS,
  isStrikeTransition,
  reduceWatchdog,
  type WatchdogEvent,
  type WatchdogPhase,
} from "@/src/sanctuary/dioramaui/watchdog";
import { Sanctuary2DScreen } from "@/src/sanctuary/Sanctuary2DScreen";
import { useSanctuary } from "@/src/sanctuary/useSanctuary";

type SkiaModule = typeof import("@shopify/react-native-skia");

export function DioramaScreen() {
  let skia: SkiaModule | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    skia = require("@shopify/react-native-skia");
  } catch {
    skia = null;
  }

  if (skia === null) {
    // Known state, not a crash: no strike, just the permanent fallback.
    return <Sanctuary2DScreen />;
  }

  return <DioramaGate skia={skia} />;
}

function DioramaGate({ skia }: { skia: SkiaModule }) {
  const { data, isLoading, isError, error, refetch } = useSanctuary();
  const recordRenderCrash = useSanctuaryDioramaPrefs((s) => s.recordRenderCrash);

  // Chooser <-> scene flow: null shows the biome chooser.
  const [zoneId, setZoneId] = useState<SanctuaryZoneId | null>(null);
  const onBack = useCallback(() => setZoneId(null), []);

  // Pending-wake ledger (the D7 "kid actually sees it" guarantee,
  // restated for the chooser flow): BiomeScene can only detect a wake as
  // a prop transition on its own mounted island, so a dormant -> awake
  // flip that lands while that zone's scene is unmounted (kid on the
  // chooser, or inside another zone) would otherwise lose the ceremony
  // forever. The gate outlives those switches: it diffs per-zone dormant
  // flags across snapshots, records un-celebrated wakes, hands the flag
  // to the mounting scene (initiallyWaking), and clears it only when the
  // ceremony has actually played (onWakeEnd) -- so it survives backing
  // out mid-hold and replays on the next entry. Refs, not state: the
  // ledger is read at scene mount, never mid-scene.
  const prevDormantRef = useRef<Map<SanctuaryZoneId, boolean> | null>(null);
  const pendingWakesRef = useRef<Set<SanctuaryZoneId>>(new Set());
  useEffect(() => {
    if (data === undefined) return;
    const dormantByZone = new Map(
      data.zones.map((z) => [z.zone_id, !z.unlocked] as const),
    );
    // The first snapshot this gate sees only seeds the history: an
    // already-awake zone is not a fresh wake.
    const prev = prevDormantRef.current;
    if (prev !== null) {
      for (const [zone, dormant] of dormantByZone) {
        if (prev.get(zone) === true && !dormant) {
          pendingWakesRef.current.add(zone);
        } else if (dormant) {
          // Dormant (or rolled back to dormant): nothing to celebrate.
          pendingWakesRef.current.delete(zone);
        }
      }
    }
    prevDormantRef.current = dormantByZone;
  }, [data]);
  const onWakeEnd = useCallback(() => {
    if (zoneId !== null) pendingWakesRef.current.delete(zoneId);
  }, [zoneId]);

  // Watchdog: pure reducer behind refs; one strike max per scene mount.
  const phaseRef = useRef<WatchdogPhase>("waiting");
  const [struck, setStruck] = useState(false);
  const applyEvent = useCallback(
    (event: WatchdogEvent) => {
      const prev = phaseRef.current;
      const next = reduceWatchdog(prev, event);
      phaseRef.current = next;
      if (isStrikeTransition(prev, next)) {
        recordRenderCrash();
        setStruck(true);
      }
    },
    [recordRenderCrash],
  );

  // Arm only while a scene canvas is actually the rendered branch: the
  // chooser has no canvas (a timeout there would mis-attribute a browse
  // as a renderer strike), and a failed refetch keeps cached data
  // (isError && data defined) but renders the Retry screen. Each scene
  // mount re-arms from "waiting" -- the reducer stays the one authority
  // for strike transitions.
  const canvasMounted =
    !struck && data !== undefined && !isError && !isLoading && zoneId !== null;
  useEffect(() => {
    if (!canvasMounted) return;
    phaseRef.current = "waiting";
    const timer = setTimeout(
      () => applyEvent("timeout"),
      FIRST_FRAME_TIMEOUT_MS,
    );
    return () => clearTimeout(timer);
  }, [canvasMounted, zoneId, applyEvent]);

  const onFirstFrame = useCallback(() => applyEvent("first-frame"), [applyEvent]);
  const onCrash = useCallback(() => applyEvent("crash"), [applyEvent]);

  if (struck) {
    // This session falls back immediately; the persisted count decides
    // whether future launches try the diorama again.
    return <Sanctuary2DScreen />;
  }

  if (isLoading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator />
        <Text style={styles.centeredText}>Waking up…</Text>
      </View>
    );
  }

  if (isError || data === undefined) {
    return (
      <View style={styles.centered}>
        <Text style={styles.centeredTitle}>Couldn't reach your Sanctuary</Text>
        <Text style={styles.centeredText}>
          {error?.message ?? "Try again in a moment."}
        </Text>
        <Pressable
          accessibilityRole="button"
          style={styles.retryButton}
          onPress={() => void refetch()}
        >
          <Text style={styles.retryButtonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  if (zoneId === null) {
    return <BiomeChooserScreen snapshot={data} onOpenZone={setZoneId} />;
  }

  return (
    <RenderBoundary onCrash={onCrash}>
      <SceneBody
        skia={skia}
        snapshot={data}
        zoneId={zoneId}
        initiallyWaking={pendingWakesRef.current.has(zoneId)}
        onBack={onBack}
        onFirstFrame={onFirstFrame}
        onWakeEnd={onWakeEnd}
      />
    </RenderBoundary>
  );
}

/** First layout wins: the scene camera initializes from it, and the
 * diorama does not chase rotation/resize (same policy as the D4 spike). */
function SceneBody({
  skia,
  snapshot,
  zoneId,
  initiallyWaking,
  onBack,
  onFirstFrame,
  onWakeEnd,
}: {
  skia: SkiaModule;
  snapshot: SanctuarySnapshotDto;
  zoneId: SanctuaryZoneId;
  initiallyWaking: boolean;
  onBack: () => void;
  onFirstFrame: () => void;
  onWakeEnd: () => void;
}) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const onLayout = useCallback((e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize((prev) => prev ?? (width > 0 && height > 0 ? { w: width, h: height } : prev));
  }, []);

  return (
    <View style={styles.root} onLayout={onLayout}>
      {size ? (
        <BiomeScene
          key={zoneId}
          skia={skia}
          w={size.w}
          h={size.h}
          snapshot={snapshot}
          zoneId={zoneId}
          initiallyWaking={initiallyWaking}
          onBack={onBack}
          onFirstFrame={onFirstFrame}
          onWakeEnd={onWakeEnd}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0B0F0D" },
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
    minHeight: 44,
    minWidth: 44,
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
    backgroundColor: "#3F6B40",
    alignItems: "center",
    justifyContent: "center",
  },
  retryButtonText: { color: "#fff", fontSize: 14, fontWeight: "500" },
});
