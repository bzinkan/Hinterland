/**
 * One zone as a full-bleed 2.5D layered scene (ADR 0012 addendum: the
 * composition pivot). Depth bands span the whole screen -- sky gradient
 * (palette + per-zone tint), FAR / MID / GROUND backdrop bands, optional
 * FORE framing accents -- with the ground running along the bottom: no
 * floating plateau, no island underside. Migrated zones (meadow first)
 * draw generated backdrop art; the rest center their existing island art
 * over a simple ground gradient as the documented interim.
 *
 * Render contract (unchanged from D7, non-negotiable):
 *  - SkPicture caching only: bands + sprites recorded once per palette
 *    state (sceneArt.ts), replayed per frame. No per-frame ImageSVG.
 *  - No per-frame React state: pan/wind are shared values read by
 *    useDerivedValue; state changes only on user actions, wake
 *    transitions, and the one-shot first-frame pulse.
 *  - All color through svgCache.substitute + paletteSlots; dormant scenes
 *    are baked desaturated-slot recordings; the wake moment is a
 *    transient saveLayer lerp that starts only once the tab is focused.
 *  - Tap vs pan uses movement AND velocity (gestures.classifyRelease);
 *    tap coordinates are root-relative pageX/pageY (D4 findings a+b).
 *  - The ground band pans 1:1 with the camera, so groundHitTest
 *    (pure, unit-tested) shares its transform with the drawn pixels.
 *
 * Mount this keyed by zoneId (`<BiomeScene key={zoneId} .../>`) so a zone
 * switch resets pan/wake state wholesale. A wake flip can also land while
 * this zone's scene is NOT mounted (kid on the chooser, or in another
 * zone's scene): the owner records it and mounts us with initiallyWaking,
 * which pins the same DORMANT_SAT start and plays the same held-until-
 * focused lerp -- the ceremony is never lost to the chooser flow.
 */

import { useIsFocused } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanResponder, Pressable, StyleSheet, Text, View } from "react-native";
import {
  Easing,
  useDerivedValue,
  useSharedValue,
  withDecay,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import type { SkSVG, Transforms3d } from "@shopify/react-native-skia";

import type {
  SanctuaryElementDto,
  SanctuaryMysteryCueDto,
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { useScreenReaderEnabled } from "@/src/config/featureFlags";
import {
  groundHitTest,
  remapIslandToGround,
} from "@/src/sanctuary/diorama/groundRemap";
import {
  BAND_BOTTOM_FRACTION,
  bandRect,
  INTERIM_GROUND_TOP_FRACTION,
  SCENE_PARALLAX,
  sceneMetrics,
} from "@/src/sanctuary/diorama/sceneLayout";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import { ZONE_PLACEHOLDER_COLOR } from "@/src/sanctuary/diorama/scene/zoneColors";
import { buildVistaPlan, type IslandPlan } from "@/src/sanctuary/diorama/vistaPlan";
import { CueCard } from "@/src/sanctuary/dioramaui/CueCard";
import { FirstFramePulse } from "@/src/sanctuary/dioramaui/FirstFramePulse";
import { classifyRelease } from "@/src/sanctuary/dioramaui/gestures";
import { WIND_PERIOD_MS } from "@/src/sanctuary/dioramaui/IslandGroup";
import type { SkiaApi } from "@/src/sanctuary/dioramaui/islandArt";
import {
  applyColorMatrixToHex,
  DORMANT_SAT,
  dormantSlotHexes,
  mixHex,
  paletteSlotHexes,
  satMatrix,
  silhouetteSlotHexes,
  zoneAccentSlotHexes,
} from "@/src/sanctuary/dioramaui/paletteSlots";
import { recordScenePictures } from "@/src/sanctuary/dioramaui/sceneArt";
import { HazeBand } from "@/src/sanctuary/dioramaui/SkyLayer";
import { createSvgCache } from "@/src/sanctuary/dioramaui/svgCache";
import { ElementInspectModal } from "@/src/sanctuary/panels/ElementInspectModal";

type SkiaModule = typeof import("@shopify/react-native-skia");

/** Distinct (asset x palette) pairs alive at once for ONE zone's scene:
 * 4 bands x up to 3 palette states + this zone's sprites + silhouette. */
const SVG_CACHE_CAPACITY = 96;

/** Wind skew radians at amplitude 1 for the big scene bands (gentler than
 * the island values -- these shapes span the whole screen). */
const SCENE_WIND_SKEW_MID = 0.006;
const SCENE_WIND_SKEW_FORE = 0.014;
const SCENE_WIND_SKEW_SILHOUETTE = 0.012;

/** The wake moment: 1.2s saturation lerp (IslandGroup's exact timing). */
const WAKE_TIMING = { duration: 1200, easing: Easing.inOut(Easing.cubic) };

export function BiomeScene({
  skia,
  w,
  h,
  snapshot,
  zoneId,
  initiallyWaking = false,
  onBack,
  onFirstFrame,
  onWakeEnd,
}: {
  skia: SkiaModule;
  w: number;
  h: number;
  snapshot: SanctuarySnapshotDto;
  zoneId: SanctuaryZoneId;
  /** True when this zone flipped dormant -> awake while its scene was
   * unmounted (DioramaGate's pending-wake ledger): start pinned at
   * DORMANT_SAT and play the wake lerp once focused, exactly as if the
   * flip had landed while mounted. Read at mount only. */
  initiallyWaking?: boolean;
  onBack: () => void;
  onFirstFrame: () => void;
  /** Wake ceremony completed (either trigger path): the owner clears its
   * pending-wake flag so the ceremony plays exactly once. */
  onWakeEnd?: () => void;
}) {
  const { Canvas, ColorMatrix, Group, LinearGradient, Paint, Picture, Rect, Skia, vec } =
    skia;

  // --- Pure scene data: the production path (scenePlan -> vistaPlan ->
  // ground remap). The seeded placement stays the single authority. ---
  const scene = useMemo(() => buildScenePlan(snapshot), [snapshot]);
  const island: IslandPlan = useMemo(() => {
    const vista = buildVistaPlan(scene);
    const found = vista.islands.find((i) => i.zoneId === zoneId);
    if (!found) throw new Error(`BiomeScene: no island plan for ${zoneId}`);
    return found;
  }, [scene, zoneId]);
  const metrics = useMemo(() => sceneMetrics(w, h), [w, h]);
  const ground = useMemo(() => remapIslandToGround(island, metrics), [island, metrics]);

  const zoneTitle = useMemo(
    () => snapshot.zones.find((z) => z.zone_id === zoneId)?.title ?? zoneId,
    [snapshot.zones, zoneId],
  );
  const elementById = useMemo(
    () => new Map(snapshot.elements.map((e) => [e.element_id, e] as const)),
    [snapshot.elements],
  );
  const cue: SanctuaryMysteryCueDto | null = useMemo(
    () => snapshot.mystery_cues.find((c) => c.zone_id === zoneId) ?? null,
    [snapshot.mystery_cues, zoneId],
  );

  // --- Palette states (stable references; recording happens exactly when
  // these change). Awake scenes carry the zone-accent slots (the D7 dive
  // behavior: green_deep follows the visited zone). ---
  const palette = scene.palette;
  const svgCache = useMemo(
    () => createSvgCache<SkSVG>((svg) => Skia.SVG.MakeFromString(svg), SVG_CACHE_CAPACITY),
    [Skia],
  );
  const baseSlots = useMemo(() => paletteSlotHexes(palette), [palette]);
  const accentSlots = useMemo(
    () => zoneAccentSlotHexes(palette, zoneId),
    [palette, zoneId],
  );
  const dormantSlots = useMemo(() => dormantSlotHexes(baseSlots), [baseSlots]);
  const silhouetteSlots = useMemo(
    () => silhouetteSlotHexes(dormantSlots),
    [dormantSlots],
  );

  // --- Interaction state (never touched per frame). ---
  const [inspected, setInspected] = useState<SanctuaryElementDto | null>(null);
  const [cueOpen, setCueOpen] = useState(false);

  // --- Wake transition, two trigger paths with ONE ceremony:
  //  (a) the snapshot flips this zone dormant -> awake while the scene is
  //      mounted (wake refetches land with the tab blurred; bottom tabs
  //      keep it mounted) -- detected as a prop transition, derived
  //      DURING render (adjust-state-while-rendering) so the waking flag
  //      and the awake-colored pictures land in the same commit;
  //  (b) the flip landed while this zone's scene was UNMOUNTED (kid on
  //      the chooser or in another zone) -- the owner recorded it and
  //      mounted us with initiallyWaking.
  // Either way the saturation pin below makes the first waking frame
  // pixel-equivalent to the baked dormant look, and the 1.2s lerp HOLDS
  // until the screen is focused -- exactly the D7 ceremony. ---
  const focused = useIsFocused();
  const [wakeState, setWakeState] = useState<{
    island: IslandPlan | null;
    waking: boolean;
  }>({ island: null, waking: initiallyWaking });
  if (wakeState.island !== island) {
    const woke =
      wakeState.island !== null && wakeState.island.dormant && !island.dormant;
    setWakeState({ island, waking: wakeState.waking || woke });
  }
  const waking = wakeState.waking;
  const finishWake = useCallback(() => {
    setWakeState((s) => (s.waking ? { ...s, waking: false } : s));
    onWakeEnd?.();
  }, [onWakeEnd]);

  const wakeSat = useSharedValue(1);
  const wakeArmed = useRef(false);
  if (waking && !wakeArmed.current) {
    wakeArmed.current = true;
    wakeSat.value = DORMANT_SAT;
  }
  if (!waking && wakeArmed.current) {
    wakeArmed.current = false;
  }
  useEffect(() => {
    if (!waking || !focused) return;
    wakeSat.value = withTiming(1, WAKE_TIMING);
    const timer = setTimeout(finishWake, WAKE_TIMING.duration + 100);
    return () => clearTimeout(timer);
  }, [waking, focused, finishWake, wakeSat]);
  const wakeMatrix = useDerivedValue(() => satMatrix(wakeSat.value));

  // --- Pictures: recorded once per (plan, palette state), replayed per
  // frame. Dormant scenes bake the desaturated slots. ---
  const pictures = useMemo(
    () =>
      recordScenePictures({
        Skia: Skia as SkiaApi,
        zoneId,
        dormant: island.dormant,
        ground,
        metrics,
        slots: island.dormant ? dormantSlots : accentSlots,
        silhouetteSlots,
        svgCache,
      }),
    [Skia, zoneId, island, ground, metrics, dormantSlots, accentSlots, silhouetteSlots, svgCache],
  );

  // --- Sky: palette gradient + per-zone tint, dormant-baked like the
  // rest of the art (colors are data here, not a per-frame filter). ---
  const skyColors = useMemo(() => {
    const top = mixHex(palette.skyTop, ZONE_PLACEHOLDER_COLOR[zoneId], 0.18);
    const horizon = mixHex(palette.horizon, ZONE_PLACEHOLDER_COLOR[zoneId], 0.12);
    if (!island.dormant) return [top, horizon];
    const m = satMatrix(DORMANT_SAT);
    return [applyColorMatrixToHex(top, m), applyColorMatrixToHex(horizon, m)];
  }, [palette, zoneId, island.dormant]);
  const groundGradient = useMemo(() => {
    const slots = island.dormant ? dormantSlots : accentSlots;
    return [slots.green_mid, slots.green_deep];
  }, [island.dormant, dormantSlots, accentSlots]);

  // --- Camera + wind (shared values; the ONLY animation state). Pan is
  // camera position in [0, maxPan]; every band translates -panX * its
  // parallax factor. Start centered. ---
  const panX = useSharedValue(metrics.maxPan / 2);
  const windT = useSharedValue(0);
  useEffect(() => {
    windT.value = withRepeat(
      withTiming(1, { duration: WIND_PERIOD_MS, easing: Easing.linear }),
      -1,
      true,
    );
  }, [windT]);

  const skyTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: -panX.value * SCENE_PARALLAX.sky },
  ]);
  const farTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: -panX.value * SCENE_PARALLAX.far },
  ]);
  const midBottom = h * BAND_BOTTOM_FRACTION.mid;
  const swayAmp = island.dormant ? 0 : 1;
  const midTransform = useDerivedValue<Transforms3d>(() => {
    const skew =
      Math.sin(windT.value * 2 * Math.PI) * SCENE_WIND_SKEW_MID * swayAmp;
    return [
      { translateX: -panX.value * SCENE_PARALLAX.mid },
      { translateY: midBottom },
      { skewX: skew },
      { translateY: -midBottom },
    ];
  });
  const groundTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: -panX.value * SCENE_PARALLAX.ground },
  ]);
  const foreTransform = useDerivedValue<Transforms3d>(() => {
    const skew =
      Math.sin((windT.value + 0.4) * 2 * Math.PI) *
      SCENE_WIND_SKEW_FORE *
      swayAmp;
    return [
      { translateX: -panX.value * SCENE_PARALLAX.fore },
      { translateY: h },
      { skewX: skew },
      { translateY: -h },
    ];
  });
  // The silhouette hint sways even on dormant scenes (the D7 tell).
  const silhouettePivot = h * 0.9;
  const silhouetteTransform = useDerivedValue<Transforms3d>(() => {
    const skew =
      Math.sin((windT.value + 0.2) * 2 * Math.PI) * SCENE_WIND_SKEW_SILHOUETTE;
    return [
      { translateX: -panX.value * SCENE_PARALLAX.ground },
      { translateY: silhouettePivot },
      { skewX: skew },
      { translateY: -silhouettePivot },
    ];
  });

  // --- Tap resolution: root-relative coordinates -> pure ground hit test
  // (the ground band's transform is panX at parallax 1.0). ---
  const onTap = useCallback(
    (x: number, y: number) => {
      const hit = groundHitTest(ground, { x, y }, panX.value);
      if (hit?.type === "sprite") {
        const element = elementById.get(hit.rectId);
        if (element) setInspected(element);
        return;
      }
      if (hit?.type === "silhouette") {
        setCueOpen(true);
        return;
      }
      setCueOpen(false);
    },
    [ground, elementById, panX],
  );

  // --- Pan + tap. PanResponder (gesture-handler is not a dependency);
  // pan events are discrete JS events writing one shared value. Tap
  // coordinates are root-relative pageX/pageY minus the measured root
  // offset -- never locationX/Y (D4 finding b). ---
  const rootRef = useRef<View>(null);
  const rootOffset = useRef({ x: 0, y: 0 });
  const measureRoot = useCallback(() => {
    rootRef.current?.measureInWindow((x, y) => {
      rootOffset.current = { x, y };
    });
  }, []);
  const panStart = useRef(0);
  const maxPan = metrics.maxPan;
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
          // Content follows the finger: drag right pans the camera left.
          const next = panStart.current - g.dx;
          panX.value = Math.max(0, Math.min(maxPan, next));
        },
        onPanResponderRelease: (evt, g) => {
          if (classifyRelease(g) === "tap") {
            onTap(
              evt.nativeEvent.pageX - rootOffset.current.x,
              evt.nativeEvent.pageY - rootOffset.current.y,
            );
            return;
          }
          // gestureState velocity is px/ms; withDecay wants units/s.
          const velocity = Math.max(-2000, Math.min(2000, -g.vx * 1000));
          panX.value = withDecay({ velocity, clamp: [0, maxPan] });
        },
      }),
    [measureRoot, onTap, panX, maxPan],
  );

  // --- First-frame pulse: one state flip, then the worklet unmounts. ---
  const [firstFrameSeen, setFirstFrameSeen] = useState(false);
  const handleFirstFrame = useCallback(() => {
    setFirstFrameSeen(true);
    onFirstFrame();
  }, [onFirstFrame]);

  const screenReaderEnabled = useScreenReaderEnabled();
  const isInterim = pictures.bands === null;
  const interimGroundTop = h * INTERIM_GROUND_TOP_FRACTION;
  const farRect = bandRect(metrics, "far");

  return (
    <View
      ref={rootRef}
      style={styles.root}
      onLayout={measureRoot}
      {...responder.panHandlers}
    >
      <Canvas style={StyleSheet.absoluteFill}>
        {/* Wake ceremony: transient saveLayer over the whole scene for the
            1.2s lerp only -- steady-state scenes draw baked pictures with
            no layer at all (IslandGroup's exact pattern, scene-wide so the
            sky warms up with the land). */}
        <Group
          layer={
            waking ? (
              <Paint>
                <ColorMatrix matrix={wakeMatrix} />
              </Paint>
            ) : undefined
          }
        >
          <Group transform={skyTransform}>
            <Rect x={-w * 0.2} y={0} width={w * 1.4 + metrics.maxPan} height={h}>
              <LinearGradient
                start={vec(0, 0)}
                end={vec(0, h)}
                colors={skyColors}
              />
            </Rect>
          </Group>

          {pictures.bands !== null ? (
            <>
              <Group transform={farTransform}>
                <Picture picture={pictures.bands.far} />
              </Group>
              <Group transform={midTransform}>
                <Picture picture={pictures.bands.mid} />
              </Group>
              <Group transform={groundTransform}>
                <Picture picture={pictures.bands.ground} />
              </Group>
            </>
          ) : (
            <>
              {/* Interim: haze for depth, ground gradient, centered island
                  art (the zone's existing pictures as a distant landform). */}
              <Group transform={farTransform}>
                <HazeBand
                  skia={skia}
                  y={farRect.y + farRect.height * 0.4}
                  height={farRect.height * 0.8}
                  color={palette.fog}
                  peakAlphaHex="3C"
                  canvasWidth={metrics.sceneWidth}
                />
              </Group>
              <Group transform={groundTransform}>
                <Rect
                  x={-40}
                  y={interimGroundTop}
                  width={metrics.sceneWidth + 80}
                  height={h - interimGroundTop + 40}
                >
                  <LinearGradient
                    start={vec(0, interimGroundTop)}
                    end={vec(0, h)}
                    colors={groundGradient}
                  />
                </Rect>
                {pictures.interimIsland !== null ? (
                  <Picture picture={pictures.interimIsland} />
                ) : null}
              </Group>
            </>
          )}

          <Group transform={groundTransform}>
            <Picture picture={pictures.sprites} />
          </Group>

          {pictures.silhouette !== null ? (
            <Group transform={silhouetteTransform}>
              <Picture picture={pictures.silhouette} />
            </Group>
          ) : null}

          {pictures.bands !== null ? (
            <Group transform={foreTransform}>
              <Picture picture={pictures.bands.fore} />
            </Group>
          ) : null}
        </Group>
      </Canvas>

      {!firstFrameSeen ? <FirstFramePulse onFirstFrame={handleFirstFrame} /> : null}

      <View style={styles.topRow} pointerEvents="box-none">
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Back to all biomes"
          style={styles.backButton}
          onPress={onBack}
        >
          <Text style={styles.backButtonText}>‹ Back</Text>
        </Pressable>
        <View style={styles.titleChip} pointerEvents="none">
          <Text style={styles.titleChipText}>{zoneTitle}</Text>
        </View>
      </View>

      {cueOpen && cue !== null ? (
        <CueCard cue={cue} onClose={() => setCueOpen(false)} />
      ) : null}

      {screenReaderEnabled ? (
        <A11ySceneOverlay
          island={island}
          zoneTitle={zoneTitle}
          elementById={elementById}
          hasCue={cue !== null && island.dormant}
          onInspect={setInspected}
          onCue={() => setCueOpen(true)}
          onBack={onBack}
        />
      ) : null}

      <ElementInspectModal element={inspected} onClose={() => setInspected(null)} />
    </View>
  );
}

/**
 * Non-visual proxies for screen-reader users who land on the canvas
 * (decideSanctuaryDiorama routes them to the classic screen, but TalkBack
 * can be enabled mid-session): Back, one row per inhabitant, and the
 * mystery hint on cued dormant scenes. Mounted ONLY while a screen reader
 * is active so it never intercepts touch gestures.
 */
function A11ySceneOverlay({
  island,
  zoneTitle,
  elementById,
  hasCue,
  onInspect,
  onCue,
  onBack,
}: {
  island: IslandPlan;
  zoneTitle: string;
  elementById: Map<string, SanctuaryElementDto>;
  hasCue: boolean;
  onInspect: (element: SanctuaryElementDto) => void;
  onCue: () => void;
  onBack: () => void;
}) {
  const elements = island.sprites
    .filter((s) => s.kind === "element")
    .map((s) => elementById.get(s.key))
    .filter((e): e is SanctuaryElementDto => e !== undefined);
  return (
    <View style={styles.a11yOverlay}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Back to all biomes"
        style={styles.a11yRow}
        onPress={onBack}
      />
      {elements.map((element) => (
        <Pressable
          key={element.element_id}
          accessibilityRole="button"
          accessibilityLabel={`${element.title}, in the ${zoneTitle}`}
          accessibilityHint="Opens details"
          style={styles.a11yRow}
          onPress={() => onInspect(element)}
        />
      ))}
      {hasCue ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`A mystery waits in the ${zoneTitle}`}
          accessibilityHint="Shows the mystery hint"
          style={styles.a11yRow}
          onPress={onCue}
        />
      ) : null}
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
