/**
 * The living diorama: all 7 zone islands on one Skia canvas, driven by
 * REAL Sanctuary data (no sample snapshots here).
 *
 * Render contract (ADR 0012 + D4 device findings, non-negotiable):
 *  - SkPicture caching only: bands + sprites recorded once per palette
 *    state (islandArt.ts), replayed per frame. No per-frame ImageSVG.
 *  - The transform chain equals hitTest.screenFromIslandLocal exactly:
 *    viewport translate/scale -> per-island anchor -> island-local art.
 *  - No React state changes per frame: pan/wind/camera are shared values
 *    read by useDerivedValue; state changes only on user actions, wake
 *    transitions, and the one-shot first-frame pulse.
 *  - Tap vs pan uses movement AND velocity (gestures.classifyRelease);
 *    tap coordinates are root-relative pageX/pageY (D4 findings a+b).
 */

import { useIsFocused } from "@react-navigation/native";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  PanResponder,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import {
  Easing,
  useDerivedValue,
  useSharedValue,
  withDecay,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import type { SkSVG, Transforms3d } from "@shopify/react-native-skia";

import {
  SANCTUARY_ZONE_ORDER,
  type SanctuaryElementDto,
  type SanctuaryMysteryCueDto,
  type SanctuarySnapshotDto,
  type SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { useScreenReaderEnabled } from "@/src/config/featureFlags";
import {
  vistaFraming,
  zoneFraming,
  type Framing,
} from "@/src/sanctuary/diorama/framing";
import { hitTest, type DioramaViewport } from "@/src/sanctuary/diorama/hitTest";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import {
  REFERENCE_SCREEN_WIDTH,
  VISTA_CANVAS,
  VISTA_PAN_LIMIT,
} from "@/src/sanctuary/diorama/vistaLayout";
import {
  buildVistaPlan,
  type IslandPlan,
  type VistaPlan,
} from "@/src/sanctuary/diorama/vistaPlan";
import { CueCard } from "@/src/sanctuary/dioramaui/CueCard";
import { FirstFramePulse } from "@/src/sanctuary/dioramaui/FirstFramePulse";
import { classifyRelease } from "@/src/sanctuary/dioramaui/gestures";
import { IslandGroup, WIND_PERIOD_MS } from "@/src/sanctuary/dioramaui/IslandGroup";
import {
  dormantSlotHexes,
  paletteSlotHexes,
  silhouetteSlotHexes,
  zoneAccentSlotHexes,
} from "@/src/sanctuary/dioramaui/paletteSlots";
import { HazeBand, SkyLayer } from "@/src/sanctuary/dioramaui/SkyLayer";
import { createSvgCache } from "@/src/sanctuary/dioramaui/svgCache";
import { ElementInspectModal } from "@/src/sanctuary/panels/ElementInspectModal";

type SkiaModule = typeof import("@shopify/react-native-skia");

const DIVE_TIMING = { duration: 450, easing: Easing.inOut(Easing.cubic) };

/** Distinct (asset x palette) pairs alive at once: 28 layers x up to 3
 * palette states + ~60 sprites + silhouettes. Sized so a season remap
 * never thrashes the LRU. */
const SVG_CACHE_CAPACITY = 192;

export function DioramaScene({
  skia,
  w,
  h,
  snapshot,
  onFirstFrame,
}: {
  skia: SkiaModule;
  w: number;
  h: number;
  snapshot: SanctuarySnapshotDto;
  onFirstFrame: () => void;
}) {
  const { Canvas, Group, Skia } = skia;

  // --- Pure scene data: the real production path. ---
  const scene = useMemo(() => buildScenePlan(snapshot), [snapshot]);
  const vista: VistaPlan = useMemo(() => buildVistaPlan(scene), [scene]);

  const zoneTitles = useMemo(() => {
    const map = new Map<SanctuaryZoneId, string>();
    for (const zone of snapshot.zones) map.set(zone.zone_id, zone.title);
    return map;
  }, [snapshot.zones]);
  const elementById = useMemo(
    () => new Map(snapshot.elements.map((e) => [e.element_id, e] as const)),
    [snapshot.elements],
  );
  const cueByZone = useMemo(() => {
    const map = new Map<SanctuaryZoneId, SanctuaryMysteryCueDto>();
    for (const cue of snapshot.mystery_cues) map.set(cue.zone_id, cue);
    return map;
  }, [snapshot.mystery_cues]);

  // --- Palette states (stable references so only affected islands
  // re-record on a change; contract 5). ---
  const svgCache = useMemo(
    () => createSvgCache<SkSVG>((svg) => Skia.SVG.MakeFromString(svg), SVG_CACHE_CAPACITY),
    [Skia],
  );
  const baseSlots = useMemo(() => paletteSlotHexes(scene.palette), [scene.palette]);
  const dormantSlots = useMemo(() => dormantSlotHexes(baseSlots), [baseSlots]);
  const silhouetteSlots = useMemo(
    () => silhouetteSlotHexes(dormantSlots),
    [dormantSlots],
  );

  // --- Interaction state (never touched per frame). ---
  const [mode, setMode] = useState<"vista" | "dive">("vista");
  const modeRef = useRef<"vista" | "dive">("vista");
  const [dived, setDived] = useState<SanctuaryZoneId | null>(null);
  const divedRef = useRef<SanctuaryZoneId | null>(null);
  const [inspected, setInspected] = useState<SanctuaryElementDto | null>(null);
  const [cueZone, setCueZone] = useState<SanctuaryZoneId | null>(null);

  // At DIVE the dived island re-records with its zone-accent slots so it
  // picks up its zone identity (D4 left green_deep season/zone-invariant).
  const diveSlots = useMemo(
    () => (dived !== null ? zoneAccentSlotHexes(scene.palette, dived) : null),
    [scene.palette, dived],
  );
  const slotsFor = useCallback(
    (island: IslandPlan) => {
      if (island.dormant) return dormantSlots;
      if (island.zoneId === dived && diveSlots !== null) return diveSlots;
      return baseSlots;
    },
    [dormantSlots, dived, diveSlots, baseSlots],
  );

  // --- Wake transitions: snapshot flips a zone dormant->awake. Derived
  // DURING render (React's adjust-state-while-rendering pattern) so the
  // waking flag and the pinned-desaturated layer land in the SAME commit
  // as the awake-colored pictures -- a post-commit effect would flash the
  // island at full color for a few frames before the lerp started.
  //
  // Every production trigger for a snapshot refetch (observe-submit,
  // observation detail, kid-handoff) fires while this tab is BLURRED --
  // bottom tabs keep it mounted, so the new snapshot flows in offscreen.
  // The wake therefore arms here immediately (pin lands in this commit)
  // but the 1.2s lerp is HELD until the screen is focused: IslandGroup
  // keeps the island pinned at dormant saturation and only starts the
  // ceremony once `focused` is true, so the kid actually sees it when
  // they open the Sanctuary tab. ---
  const focused = useIsFocused();
  const [wakeState, setWakeState] = useState<{
    vista: VistaPlan | null;
    waking: ReadonlySet<SanctuaryZoneId>;
  }>({ vista: null, waking: new Set() });
  if (wakeState.vista !== vista) {
    const prev = wakeState.vista;
    const prevDormant = new Map(
      (prev?.islands ?? []).map((i) => [i.zoneId, i.dormant] as const),
    );
    const woke =
      prev === null
        ? [] // first snapshot: nothing to animate
        : vista.islands
            .filter((i) => !i.dormant && prevDormant.get(i.zoneId) === true)
            .map((i) => i.zoneId);
    setWakeState({ vista, waking: new Set([...wakeState.waking, ...woke]) });
  }
  const wakingZones = wakeState.waking;
  const setWakingZones = useCallback(
    (update: (s: ReadonlySet<SanctuaryZoneId>) => ReadonlySet<SanctuaryZoneId>) =>
      setWakeState((s) => ({ ...s, waking: update(s.waking) })),
    [],
  );
  const onWakeEnd = useCallback(
    (zoneId: SanctuaryZoneId) => {
      setWakingZones((s) => {
        if (!s.has(zoneId)) return s;
        const next = new Set(s);
        next.delete(zoneId);
        return next;
      });
    },
    [setWakingZones],
  );

  // --- Viewport camera (shared values; the ONLY animation state). ---
  const baseScale = w / REFERENCE_SCREEN_WIDTH;
  const viewportFor = useCallback(
    (f: Framing) => {
      const s = f.scale * baseScale;
      return { viewScale: s, viewX: w / 2 - f.x * s, viewY: h / 2 - f.y * s };
    },
    [baseScale, w, h],
  );
  const vistaVp = viewportFor(vistaFraming());

  const panX = useSharedValue(0);
  const viewX = useSharedValue(vistaVp.viewX);
  const viewY = useSharedValue(vistaVp.viewY);
  const viewScale = useSharedValue(vistaVp.viewScale);
  const windT = useSharedValue(0);

  useEffect(() => {
    windT.value = withRepeat(
      withTiming(1, { duration: WIND_PERIOD_MS, easing: Easing.linear }),
      -1,
      true,
    );
  }, [windT]);

  const viewportTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: viewX.value },
    { translateY: viewY.value },
    { scale: viewScale.value },
  ]);

  const flyTo = useCallback(
    (f: Framing) => {
      const target = viewportFor(f);
      viewX.value = withTiming(target.viewX, DIVE_TIMING);
      viewY.value = withTiming(target.viewY, DIVE_TIMING);
      viewScale.value = withTiming(target.viewScale, DIVE_TIMING);
    },
    [viewportFor, viewX, viewY, viewScale],
  );

  // --- Mode changes. ---
  const prevPanRef = useRef(0);
  const diveInto = useCallback(
    (zoneId: SanctuaryZoneId) => {
      modeRef.current = "dive";
      divedRef.current = zoneId;
      setMode("dive");
      setDived(zoneId);
      setCueZone(null);
      // zoneFraming centers the raw slot; retire the parallax pan so the
      // dived island lands dead-center (hitTest stays exact the whole way
      // because it reads live shared values at tap time).
      prevPanRef.current = panX.value;
      panX.value = withTiming(0, DIVE_TIMING);
      flyTo(zoneFraming(zoneId));
    },
    [flyTo, panX],
  );
  const onBack = useCallback(() => {
    modeRef.current = "vista";
    divedRef.current = null;
    setMode("vista");
    setDived(null);
    setCueZone(null);
    setInspected(null);
    panX.value = withTiming(
      Math.max(-VISTA_PAN_LIMIT, Math.min(VISTA_PAN_LIMIT, prevPanRef.current)),
      DIVE_TIMING,
    );
    flyTo(vistaFraming());
  }, [flyTo, panX]);

  const onTap = useCallback(
    (x: number, y: number) => {
      const viewport: DioramaViewport = {
        viewX: viewX.value,
        viewY: viewY.value,
        viewScale: viewScale.value,
        panX: panX.value,
      };
      const hit = hitTest(
        vista,
        viewport,
        { x, y },
        modeRef.current,
        divedRef.current,
      );
      if (modeRef.current === "vista") {
        if (hit?.type === "island") diveInto(hit.zoneId);
        return;
      }
      if (hit?.type === "sprite") {
        const element = elementById.get(hit.rectId);
        if (element) setInspected(element);
        return;
      }
      if (hit?.type === "silhouette") {
        setCueZone(hit.zoneId);
        return;
      }
      setCueZone(null);
    },
    [vista, elementById, diveInto, panX, viewX, viewY, viewScale],
  );

  // --- Pan + tap. PanResponder (gesture-handler is not a dependency);
  // pan events are discrete JS events writing one shared value, so all
  // per-frame motion stays on the UI thread. Tap coordinates are
  // root-relative (pageX/pageY minus the measured root offset) -- never
  // locationX/Y, which goes target-relative when a chip is under the
  // finger (D4 finding b).
  const rootRef = useRef<View>(null);
  const rootOffset = useRef({ x: 0, y: 0 });
  const measureRoot = useCallback(() => {
    rootRef.current?.measureInWindow((x, y) => {
      rootOffset.current = { x, y };
    });
  }, []);
  const panStart = useRef(0);
  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          measureRoot(); // async; lands before any humanly-possible release
          panStart.current = panX.value;
        },
        onPanResponderMove: (_evt, g) => {
          if (modeRef.current !== "vista") return;
          // Content follows the finger: anchorX carries -panX, so drag
          // right = pan down.
          const next = panStart.current - g.dx / viewScale.value;
          panX.value = Math.max(-VISTA_PAN_LIMIT, Math.min(VISTA_PAN_LIMIT, next));
        },
        onPanResponderRelease: (evt, g) => {
          if (classifyRelease(g) === "tap") {
            onTap(
              evt.nativeEvent.pageX - rootOffset.current.x,
              evt.nativeEvent.pageY - rootOffset.current.y,
            );
            return;
          }
          if (modeRef.current !== "vista") return;
          // gestureState velocity is px/ms; withDecay wants units/s.
          const velocity = Math.max(
            -2000,
            Math.min(2000, (-g.vx * 1000) / viewScale.value),
          );
          panX.value = withDecay({
            velocity,
            clamp: [-VISTA_PAN_LIMIT, VISTA_PAN_LIMIT],
          });
        },
      }),
    [measureRoot, onTap, panX, viewScale],
  );

  // --- First-frame pulse: one state flip, then the worklet unmounts. ---
  const [firstFrameSeen, setFirstFrameSeen] = useState(false);
  const handleFirstFrame = useCallback(() => {
    setFirstFrameSeen(true);
    onFirstFrame();
  }, [onFirstFrame]);

  const screenReaderEnabled = useScreenReaderEnabled();
  const palette = scene.palette;
  const windPhaseFor = (zoneId: SanctuaryZoneId) =>
    SANCTUARY_ZONE_ORDER.indexOf(zoneId) * 0.13;

  const renderIsland = (island: IslandPlan) => (
    <IslandGroup
      key={island.zoneId}
      skia={skia}
      island={island}
      slots={slotsFor(island)}
      silhouetteSlots={silhouetteSlots}
      svgCache={svgCache}
      panX={panX}
      windT={windT}
      windPhase={windPhaseFor(island.zoneId)}
      waking={wakingZones.has(island.zoneId)}
      focused={focused}
      onWakeEnd={onWakeEnd}
    />
  );

  // Islands arrive painter-sorted (back -> mid -> fore); the band splits
  // below preserve that order and only interleave the haze depth cues.
  const backIslands = vista.islands.filter((i) => i.slot.band === "back");
  const midIslands = vista.islands.filter((i) => i.slot.band === "mid");
  const foreIslands = vista.islands.filter((i) => i.slot.band === "fore");

  const divedTitle = dived !== null ? zoneTitles.get(dived) ?? dived : null;
  const cue = cueZone !== null ? cueByZone.get(cueZone) ?? null : null;

  return (
    <View
      ref={rootRef}
      style={styles.root}
      onLayout={measureRoot}
      {...responder.panHandlers}
    >
      <Canvas style={StyleSheet.absoluteFill}>
        <SkyLayer
          skia={skia}
          palette={palette}
          w={w}
          h={h}
          panX={panX}
          baseScale={baseScale}
        />
        <Group transform={viewportTransform}>
          {backIslands.map(renderIsland)}
          <HazeBand
            skia={skia}
            y={90}
            height={330}
            color={palette.fog}
            peakAlphaHex="55"
            canvasWidth={VISTA_CANVAS.width}
          />
          {midIslands.map(renderIsland)}
          <HazeBand
            skia={skia}
            y={330}
            height={300}
            color={palette.fog}
            peakAlphaHex="2E"
            canvasWidth={VISTA_CANVAS.width}
          />
          {foreIslands.map(renderIsland)}
        </Group>
      </Canvas>

      {!firstFrameSeen ? <FirstFramePulse onFirstFrame={handleFirstFrame} /> : null}

      {mode === "dive" ? (
        <View style={styles.topRow} pointerEvents="box-none">
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Back to all islands"
            style={styles.backButton}
            onPress={onBack}
          >
            <Text style={styles.backButtonText}>‹ Back</Text>
          </Pressable>
          {divedTitle !== null ? (
            <View style={styles.titleChip} pointerEvents="none">
              <Text style={styles.titleChipText}>{divedTitle}</Text>
            </View>
          ) : null}
        </View>
      ) : null}

      {cue !== null && mode === "dive" ? (
        <CueCard cue={cue} onClose={() => setCueZone(null)} />
      ) : null}

      {screenReaderEnabled ? (
        <A11yZoneOverlay
          islands={vista.islands}
          zoneTitles={zoneTitles}
          mode={mode}
          onDive={diveInto}
          onBack={onBack}
        />
      ) : null}

      <ElementInspectModal element={inspected} onClose={() => setInspected(null)} />
    </View>
  );
}

/**
 * Non-visual zone proxies for screen-reader users who land on the canvas
 * (decideSanctuaryDiorama routes them to the classic screen, but TalkBack
 * can be enabled mid-session): one focusable element per island with the
 * zone name + awake/dormant state; activating it dives. Mounted ONLY while
 * a screen reader is active so it never intercepts touch gestures.
 */
function A11yZoneOverlay({
  islands,
  zoneTitles,
  mode,
  onDive,
  onBack,
}: {
  islands: IslandPlan[];
  zoneTitles: Map<SanctuaryZoneId, string>;
  mode: "vista" | "dive";
  onDive: (zoneId: SanctuaryZoneId) => void;
  onBack: () => void;
}) {
  const ordered = useMemo(
    () =>
      SANCTUARY_ZONE_ORDER.map((zoneId) =>
        islands.find((i) => i.zoneId === zoneId),
      ).filter((i): i is IslandPlan => i !== undefined),
    [islands],
  );
  return (
    <View style={styles.a11yOverlay}>
      {mode === "dive" ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Back to all islands"
          style={styles.a11yRow}
          onPress={onBack}
        />
      ) : (
        ordered.map((island) => (
          <Pressable
            key={island.zoneId}
            accessibilityRole="button"
            accessibilityLabel={`${zoneTitles.get(island.zoneId) ?? island.zoneId} island, ${
              island.dormant ? "still sleeping" : "awake"
            }`}
            accessibilityHint="Visits this island"
            style={styles.a11yRow}
            onPress={() => onDive(island.zoneId)}
          />
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0B0F0D" },
  topRow: {
    position: "absolute",
    top: 10,
    left: 10,
    right: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  backButton: {
    minHeight: 44,
    minWidth: 44,
    justifyContent: "center",
    backgroundColor: "rgba(11, 15, 13, 0.72)",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  backButtonText: { color: "#EDEFEA", fontSize: 15, fontWeight: "600" },
  titleChip: {
    backgroundColor: "rgba(11, 15, 13, 0.55)",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  titleChipText: { color: "#EDEFEA", fontSize: 14, fontWeight: "600" },
  a11yOverlay: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
  },
  a11yRow: { height: 44 },
});
