import { Stack } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  PanResponder,
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
} from "react-native";
import {
  Easing,
  runOnJS,
  useDerivedValue,
  useFrameCallback,
  useSharedValue,
  withDecay,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import type { SkPicture, SkSVG, Transforms3d } from "@shopify/react-native-skia";

import { Text as ThemedText, View as ThemedView } from "@/components/Themed";
import { SANCTUARY_ISLAND_LAYERS } from "@/src/sanctuary/art/islandLayers.gen";
import {
  getScenerySprite,
  resolveElementSprite,
} from "@/src/sanctuary/diorama/assets/manifest";
import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import {
  vistaFraming,
  zoneFraming,
  type Framing,
} from "@/src/sanctuary/diorama/framing";
import { hitTest, type DioramaViewport } from "@/src/sanctuary/diorama/hitTest";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import { ZONE_ACCENT_COLOR } from "@/src/sanctuary/diorama/scene/zoneColors";
import {
  PARALLAX_FACTOR,
  parallaxFor,
  VISTA_CANVAS,
} from "@/src/sanctuary/diorama/vistaLayout";
import {
  buildVistaPlan,
  SPRITE_HALF_EXTENT,
  type IslandPlan,
  type VistaPlan,
} from "@/src/sanctuary/diorama/vistaPlan";
import {
  createFrameRing,
  frameStats,
  pushFrame,
  resetFrameRing,
  type FrameStats,
} from "@/src/sanctuary/dioramaui/frameStats";
import { paletteSlotHexes, satMatrix } from "@/src/sanctuary/dioramaui/paletteSlots";
import { createSvgCache } from "@/src/sanctuary/dioramaui/svgCache";

type SkiaModule = typeof import("@shopify/react-native-skia");

/**
 * D4 GO/NO-GO spike (ADR 0012): the meadow island rendered from real
 * generated art on one Skia canvas -- parallax pan, wind sway, tap-to-dive
 * via the shared hitTest contract, dormant desaturation, and an honest
 * UI-thread frame HUD. Dev route only (exp+dragonfly://dev/diorama-spike);
 * no flag wiring, no tab changes.
 *
 * Same guarded require() as skia-smoke: a dev client that predates the
 * Skia native rebuild renders a "rebuild needed" note instead of crashing
 * at bundle-registration time.
 */
export default function DioramaSpikeScreen() {
  let skia: SkiaModule | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    skia = require("@shopify/react-native-skia");
  } catch {
    skia = null;
  }

  if (skia === null) {
    return (
      <ThemedView style={styles.center}>
        <Stack.Screen options={{ title: "Diorama spike" }} />
        <ThemedText style={styles.heading}>Skia native module missing</ThemedText>
        <ThemedText style={styles.body}>
          This dev client predates the D3 native rebuild. Run a fresh
          development build (eas build --profile development) and reinstall
          it, then revisit this screen.
        </ThemedText>
      </ThemedView>
    );
  }

  return <SpikeBody skia={skia} />;
}

function SpikeBody({ skia }: { skia: SkiaModule }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const onLayout = useCallback((e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    // First layout wins: viewport shared values initialize from it, and a
    // spike does not chase rotation/resize.
    setSize((prev) => prev ?? (width > 0 && height > 0 ? { w: width, h: height } : prev));
  }, []);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Stack.Screen options={{ title: "Diorama spike" }} />
      {size ? <SpikeScene skia={skia} w={size.w} h={size.h} /> : null}
    </View>
  );
}

/** Preview tier driven through the dev snapshot (meadow gets the works:
 * elements through the relationship tier plus full flower dressing). */
const SPIKE_TIER = 20;

/** vistaLayout canvas units are dp of a 390dp-wide reference screen. */
const REFERENCE_WIDTH = 390;

/**
 * Island-local geometry of the 512x384 layer art: bottom-center anchored
 * at the island slot, scaled so the painted plateau brackets the placed
 * sprite field (element spiral + dressing skirt, ~±36 local units per
 * ISLAND_HIT_RADIUS).
 */
const LAYER_UNITS_PER_PX = 0.24;
const LAYER_W = 512 * LAYER_UNITS_PER_PX;
const LAYER_H = 384 * LAYER_UNITS_PER_PX;
/** Art bottom edge sits this far below the island anchor (local units). */
const LAYER_BOTTOM_Y = 44;

/** Sprite art px -> island-local units (128px canvas ~= a 30-unit body). */
const SPRITE_UNITS_PER_PX = 0.24;

/** Island-local rect the 512x384 layer art is drawn into. */
const LAYER_RECT = {
  x: -LAYER_W / 2,
  y: LAYER_BOTTOM_Y - LAYER_H,
  width: LAYER_W,
  height: LAYER_H,
};

/** Wind: one full sway cycle; skew radians at amplitude 1. */
const WIND_PERIOD_MS = 4800;
const WIND_SKEW = 0.012;

/** Release movement under this many dp counts as a tap, not a pan. */
const TAP_SLOP = 8;

const DIVE_TIMING = { duration: 450, easing: Easing.inOut(Easing.cubic) };
const WAKE_TIMING = { duration: 1200, easing: Easing.inOut(Easing.cubic) };
const DOZE_TIMING = { duration: 450, easing: Easing.inOut(Easing.cubic) };
const DORMANT_SAT = 0.12;

type SpriteNode =
  | { kind: "svg"; key: string; svg: SkSVG | null; x: number; y: number; w: number; h: number }
  | { kind: "fallback"; key: string; cx: number; cy: number; r: number };

/** Resolve a placed sprite to its generated art record, if it has one. */
function spriteRecordFor(sprite: IslandPlan["sprites"][number]) {
  if (sprite.kind === "element" && sprite.iconKey !== null) {
    // The plan does not carry element_type (only manifest fallbacks need
    // it); the spike only draws the sprite branch, so any type works here.
    const res = resolveElementSprite(sprite.iconKey, "coarse");
    return res.kind === "sprite"
      ? { cacheKey: `sprite:${res.spriteKey}`, record: res.sprite }
      : null;
  }
  if (sprite.kind === "scenery") {
    const name = sprite.key.split("#")[0];
    const record = getScenerySprite(name);
    return record ? { cacheKey: `sprite:${name}`, record } : null;
  }
  // Souvenirs: none in the spike (buildVistaPlan souvenirs default []).
  return null;
}

function SpikeScene({ skia, w, h }: { skia: SkiaModule; w: number; h: number }) {
  const {
    Canvas,
    Circle,
    ColorMatrix,
    Group,
    ImageSVG,
    LinearGradient,
    Paint,
    Picture,
    Rect,
    Skia,
    vec,
  } = skia;

  // --- Pure scene data (built once; same modules production will use). ---
  const scene = useMemo(() => {
    const plan = buildScenePlan(makeSampleSnapshot(SPIKE_TIER));
    // One-island spike: hit testing should only know about what is drawn.
    const vista: VistaPlan = buildVistaPlan({
      ...plan,
      zones: plan.zones.filter((z) => z.zoneId === "meadow"),
    });
    const island = vista.islands[0];
    return { plan, vista, island, slots: paletteSlotHexes(plan.palette) };
  }, []);
  const { island } = scene;
  const slot = island.slot;
  const islandParallax = parallaxFor(island.zoneId);

  const svgCache = useMemo(
    () => createSvgCache<SkSVG>((svg) => Skia.SVG.MakeFromString(svg)),
    [Skia],
  );

  const layerSvgs = useMemo(() => {
    const layers = SANCTUARY_ISLAND_LAYERS.meadow;
    const make = (band: keyof typeof layers) =>
      svgCache.makeSvg(`layer:meadow:${band}`, layers[band].svg, scene.slots);
    return { back: make("back"), base: make("base"), mid: make("mid"), fore: make("fore") };
  }, [svgCache, scene.slots]);

  // Painter order is the plan's: map preserves it. Live ImageSVG per
  // sprite is fine at vista scale for the spike; rasterizing each sprite
  // to a thumbnail image once is the D7 follow-up if sprite draw time
  // shows up in the HUD.
  const spriteNodes = useMemo<SpriteNode[]>(
    () =>
      island.sprites.map((s) => {
        const resolved = spriteRecordFor(s);
        if (!resolved) {
          const r = SPRITE_HALF_EXTENT * s.scale;
          return { kind: "fallback", key: s.key, cx: s.x, cy: s.y - r, r };
        }
        const { cacheKey, record } = resolved;
        const drawW =
          record.viewBox.width * record.scale * s.scale * SPRITE_UNITS_PER_PX;
        const drawH =
          record.viewBox.height * record.scale * s.scale * SPRITE_UNITS_PER_PX;
        const ax = record.anchor?.x ?? 0.5; // null anchor = bottom-center
        const ay = record.anchor?.y ?? 1;
        return {
          kind: "svg",
          key: s.key,
          svg: svgCache.makeSvg(cacheKey, record.svg, scene.slots),
          x: s.x - drawW * ax,
          y: s.y - drawH * ay,
          w: drawW,
          h: drawH,
        };
      }),
    [island, svgCache, scene.slots],
  );

  // --- Phase-1 caching (ADR 0012): record each band once as an SkPicture
  // and replay per frame, instead of re-walking the SVG DOMs every frame.
  // Content inside sway/parallax groups is static, so the animated
  // transforms wrap cached pictures untouched. The "Cache" chip flips back
  // to the live-ImageSVG path for an on-device A/B.
  const bandPictures = useMemo(() => {
    const bounds = Skia.XYWHRect(-250, -250, 500, 500);
    const recordLayer = (svg: SkSVG | null): SkPicture => {
      const rec = Skia.PictureRecorder();
      const canvas = rec.beginRecording(bounds);
      if (svg !== null) {
        canvas.save();
        canvas.translate(LAYER_RECT.x, LAYER_RECT.y);
        canvas.drawSvg(svg, LAYER_RECT.width, LAYER_RECT.height);
        canvas.restore();
      }
      return rec.finishRecordingAsPicture();
    };
    const rec = Skia.PictureRecorder();
    const canvas = rec.beginRecording(bounds);
    const fallbackPaint = Skia.Paint();
    fallbackPaint.setColor(Skia.Color(ZONE_ACCENT_COLOR.meadow));
    for (const n of spriteNodes) {
      if (n.kind === "svg") {
        if (n.svg !== null) {
          canvas.save();
          canvas.translate(n.x, n.y);
          canvas.drawSvg(n.svg, n.w, n.h);
          canvas.restore();
        }
      } else {
        canvas.drawCircle(n.cx, n.cy, n.r, fallbackPaint);
      }
    }
    return {
      back: recordLayer(layerSvgs.back),
      base: recordLayer(layerSvgs.base),
      mid: recordLayer(layerSvgs.mid),
      fore: recordLayer(layerSvgs.fore),
      sprites: rec.finishRecordingAsPicture(),
    };
  }, [Skia, layerSvgs, spriteNodes]);

  // --- Viewport camera (shared values; the ONLY animation state). ---
  const baseScale = w / REFERENCE_WIDTH;
  const panLimit = 0.4 * (w / baseScale);
  const viewportFor = useCallback(
    (f: Framing) => {
      const s = f.scale * baseScale;
      return { viewScale: s, viewX: w / 2 - f.x * s, viewY: h / 2 - f.y * s };
    },
    [baseScale, w, h],
  );
  // Start with the meadow centered inside the pan range rather than the
  // (empty) vista canvas center.
  const initialPanX = Math.max(
    -panLimit,
    Math.min(panLimit, slot.x - VISTA_CANVAS.width / 2),
  );
  const vistaVp = viewportFor(vistaFraming());

  const panX = useSharedValue(initialPanX);
  const viewX = useSharedValue(vistaVp.viewX);
  const viewY = useSharedValue(vistaVp.viewY);
  const viewScale = useSharedValue(vistaVp.viewScale);
  const windT = useSharedValue(0);
  const dormantSat = useSharedValue(1);

  useEffect(() => {
    windT.value = withRepeat(
      withTiming(1, { duration: WIND_PERIOD_MS, easing: Easing.linear }),
      -1,
      true,
    );
  }, [windT]);

  // --- Derived transforms. The anchor group applies EXACTLY the hitTest
  // contract (screenFromIslandLocal): viewport translate+scale outside,
  // (slot - panX*parallax, slot.y) then islandScale inside. Band parallax
  // and sway are art-only offsets on non-interactive layers.
  const viewportTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: viewX.value },
    { translateY: viewY.value },
    { scale: viewScale.value },
  ]);
  const anchorTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: slot.x - panX.value * islandParallax },
    { translateY: slot.y },
    { scale: slot.islandScale },
  ]);
  const backTransform = useDerivedValue<Transforms3d>(() => [
    {
      translateX:
        (panX.value * (islandParallax - PARALLAX_FACTOR.back)) / slot.islandScale,
    },
  ]);
  const midSwayTransform = useDerivedValue<Transforms3d>(() => {
    const skew = Math.sin(windT.value * 2 * Math.PI) * WIND_SKEW;
    return [
      {
        translateX:
          (panX.value * (islandParallax - PARALLAX_FACTOR.mid)) / slot.islandScale,
      },
      { translateY: LAYER_BOTTOM_Y },
      { skewX: skew },
      { translateY: -LAYER_BOTTOM_Y },
    ];
  });
  const foreSwayTransform = useDerivedValue<Transforms3d>(() => {
    const skew = Math.sin((windT.value + 0.4) * 2 * Math.PI) * WIND_SKEW * 1.6;
    return [
      { translateY: LAYER_BOTTOM_Y },
      { skewX: skew },
      { translateY: -LAYER_BOTTOM_Y },
    ];
  });
  const skyTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: -panX.value * PARALLAX_FACTOR.sky * baseScale },
  ]);
  const dormantMatrix = useDerivedValue(() => satMatrix(dormantSat.value));

  // --- Interaction state (never touched per frame). ---
  const [mode, setMode] = useState<"vista" | "dive">("vista");
  const modeRef = useRef<"vista" | "dive">("vista");
  const [spriteHit, setSpriteHit] = useState<string | null>(null);
  const [cachedMode, setCachedMode] = useState(true);
  const [dormantOn, setDormantOn] = useState(false);
  const [satLayerMounted, setSatLayerMounted] = useState(false);
  const wakeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (wakeTimer.current !== null) clearTimeout(wakeTimer.current);
    },
    [],
  );

  const flyTo = useCallback(
    (f: Framing) => {
      const target = viewportFor(f);
      viewX.value = withTiming(target.viewX, DIVE_TIMING);
      viewY.value = withTiming(target.viewY, DIVE_TIMING);
      viewScale.value = withTiming(target.viewScale, DIVE_TIMING);
    },
    [viewportFor, viewX, viewY, viewScale],
  );

  const onTap = useCallback(
    (x: number, y: number) => {
      const viewport: DioramaViewport = {
        viewX: viewX.value,
        viewY: viewY.value,
        viewScale: viewScale.value,
        panX: panX.value,
      };
      const hit = hitTest(
        scene.vista,
        viewport,
        { x, y },
        modeRef.current,
        modeRef.current === "dive" ? island.zoneId : null,
      );
      if (modeRef.current === "vista") {
        if (hit?.type === "island" && hit.zoneId === island.zoneId) {
          modeRef.current = "dive";
          setMode("dive");
          setSpriteHit(null);
          // zoneFraming centers the raw slot; retire the parallax pan so
          // the dived island lands dead-center (hitTest stays exact the
          // whole way because it reads live shared values at tap time).
          panX.value = withTiming(0, DIVE_TIMING);
          flyTo(zoneFraming(island.zoneId));
        }
      } else if (hit?.type === "sprite") {
        setSpriteHit(hit.rectId);
      }
    },
    [scene.vista, island.zoneId, flyTo, panX, viewX, viewY, viewScale],
  );

  const onBack = useCallback(() => {
    modeRef.current = "vista";
    setMode("vista");
    setSpriteHit(null);
    panX.value = withTiming(initialPanX, DIVE_TIMING);
    flyTo(vistaFraming());
  }, [flyTo, panX, initialPanX]);

  const onToggleDormant = useCallback(() => {
    if (wakeTimer.current !== null) {
      clearTimeout(wakeTimer.current);
      wakeTimer.current = null;
    }
    if (!dormantOn) {
      setDormantOn(true);
      setSatLayerMounted(true);
      dormantSat.value = withTiming(DORMANT_SAT, DOZE_TIMING);
    } else {
      setDormantOn(false);
      // The wake moment: 1.2 s back to full color, then drop the
      // saveLayer so steady-state rendering pays no layer cost.
      dormantSat.value = withTiming(1, WAKE_TIMING);
      wakeTimer.current = setTimeout(() => setSatLayerMounted(false), 1300);
    }
  }, [dormantOn, dormantSat]);

  // --- Pan + tap. PanResponder (not gesture-handler: it is not a
  // dependency of this app) -- pan events are discrete JS events writing
  // one shared value; all per-frame motion stays on the UI thread.
  const panStart = useRef(0);
  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          panStart.current = panX.value;
        },
        onPanResponderMove: (_evt, g) => {
          if (modeRef.current !== "vista") return;
          // Content follows the finger: anchorX carries -panX, so drag
          // right = pan down.
          const next = panStart.current - g.dx / viewScale.value;
          panX.value = Math.max(-panLimit, Math.min(panLimit, next));
        },
        onPanResponderRelease: (evt, g) => {
          if (Math.hypot(g.dx, g.dy) < TAP_SLOP) {
            onTap(evt.nativeEvent.locationX, evt.nativeEvent.locationY);
            return;
          }
          if (modeRef.current !== "vista") return;
          // gestureState velocity is px/ms; withDecay wants units/s.
          const velocity = Math.max(
            -2000,
            Math.min(2000, (-g.vx * 1000) / viewScale.value),
          );
          panX.value = withDecay({ velocity, clamp: [-panLimit, panLimit] });
        },
      }),
    // Everything captured is a ref, shared value, or spike-lifetime const.
    [onTap, panLimit, panX, viewScale],
  );

  const palette = scene.plan.palette;

  return (
    <View style={styles.root} {...responder.panHandlers}>
      <Canvas style={StyleSheet.absoluteFill}>
        {/* Sky: near-static band (parallax 0.1), drawn oversized so the
            slow drift never exposes an edge. */}
        <Group transform={skyTransform}>
          <Rect x={-w * 0.6} y={0} width={w * 2.2} height={h}>
            <LinearGradient
              start={vec(0, 0)}
              end={vec(0, h)}
              colors={[palette.skyTop, palette.horizon]}
            />
          </Rect>
        </Group>

        <Group transform={viewportTransform}>
          <Group transform={anchorTransform}>
            <Group
              layer={
                satLayerMounted ? (
                  <Paint>
                    <ColorMatrix matrix={dormantMatrix} />
                  </Paint>
                ) : undefined
              }
            >
              {cachedMode ? (
                <>
                  <Group transform={backTransform}>
                    <Picture picture={bandPictures.back} />
                  </Group>
                  <Picture picture={bandPictures.base} />
                  <Group transform={midSwayTransform}>
                    <Picture picture={bandPictures.mid} />
                  </Group>
                  <Picture picture={bandPictures.sprites} />
                  <Group transform={foreSwayTransform}>
                    <Picture picture={bandPictures.fore} />
                  </Group>
                </>
              ) : (
                <>
                  <Group transform={backTransform}>
                    <ImageSVG svg={layerSvgs.back} {...LAYER_RECT} />
                  </Group>
                  <ImageSVG svg={layerSvgs.base} {...LAYER_RECT} />
                  <Group transform={midSwayTransform}>
                    <ImageSVG svg={layerSvgs.mid} {...LAYER_RECT} />
                  </Group>
                  {spriteNodes.map((n) =>
                    n.kind === "svg" ? (
                      <ImageSVG
                        key={n.key}
                        svg={n.svg}
                        x={n.x}
                        y={n.y}
                        width={n.w}
                        height={n.h}
                      />
                    ) : (
                      <Circle
                        key={n.key}
                        cx={n.cx}
                        cy={n.cy}
                        r={n.r}
                        color={ZONE_ACCENT_COLOR.meadow}
                      />
                    ),
                  )}
                  <Group transform={foreSwayTransform}>
                    <ImageSVG svg={layerSvgs.fore} {...LAYER_RECT} />
                  </Group>
                </>
              )}
            </Group>
          </Group>
        </Group>
      </Canvas>

      {/* RN overlays: HUD owns its own 2 Hz state so the canvas subtree
          never re-renders on a stats tick. */}
      <FrameHud />
      <View style={styles.topRow} pointerEvents="box-none">
        <View style={styles.chip}>
          <Text style={styles.chipTitle}>Spike</Text>
          <Text style={styles.chipText}>
            Meadow · tier {island.depthTier} · {mode}
          </Text>
        </View>
        <Pressable
          style={styles.button}
          onPress={() => setCachedMode((c) => !c)}
        >
          <Text style={styles.buttonText}>
            {cachedMode ? "Cache on" : "Cache off"}
          </Text>
        </Pressable>
        <Pressable style={styles.button} onPress={onToggleDormant}>
          <Text style={styles.buttonText}>
            {dormantOn ? "Wake island" : "Dormant"}
          </Text>
        </Pressable>
        {mode === "dive" ? (
          <Pressable style={styles.button} onPress={onBack}>
            <Text style={styles.buttonText}>Back</Text>
          </Pressable>
        ) : null}
      </View>
      {spriteHit !== null ? (
        <View style={styles.hitChip} pointerEvents="none">
          <Text style={styles.chipText}>tapped: {spriteHit}</Text>
        </View>
      ) : null}
    </View>
  );
}

const EMPTY_STATS: FrameStats = { avgFps: 0, p95Ms: 0, worstMs: 0, count: 0 };

/**
 * Honest frame HUD: deltas come from reanimated's UI-thread frame
 * callback timestamps (never a JS timer), accumulate in a shared-value
 * ring on the UI thread, and reach React at ~2 Hz via runOnJS.
 */
function FrameHud() {
  const ring = useSharedValue(createFrameRing());
  const lastPublish = useSharedValue(0);
  const firstFrameSeen = useSharedValue(false);
  const mountedAt = useRef(Date.now());
  const [tti, setTti] = useState<number | null>(null);
  const [stats, setStats] = useState<FrameStats>(EMPTY_STATS);

  const publish = useCallback((s: FrameStats) => setStats(s), []);
  const markFirstFrame = useCallback(() => {
    setTti(Date.now() - mountedAt.current);
  }, []);

  useFrameCallback((info) => {
    if (!firstFrameSeen.value) {
      firstFrameSeen.value = true;
      runOnJS(markFirstFrame)();
    }
    const dt = info.timeSincePreviousFrame;
    if (dt !== null && dt > 0) {
      ring.modify((r) => {
        "worklet";
        pushFrame(r, dt);
        return r;
      });
    }
    if (info.timestamp - lastPublish.value >= 500) {
      lastPublish.value = info.timestamp;
      runOnJS(publish)(frameStats(ring.value));
    }
  });

  const onReset = useCallback(() => {
    ring.modify((r) => {
      "worklet";
      resetFrameRing(r);
      return r;
    });
    setStats(EMPTY_STATS);
  }, [ring]);

  return (
    <View style={styles.hud} pointerEvents="box-none">
      <Text style={styles.hudText}>
        TTI {tti === null ? "…" : `${tti} ms`} · {stats.avgFps.toFixed(1)} fps ·
        p95 {stats.p95Ms.toFixed(1)} ms · worst {stats.worstMs.toFixed(1)} ms
      </Text>
      <Pressable style={styles.hudButton} onPress={onReset}>
        <Text style={styles.buttonText}>Reset</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0B0F0D" },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.7, marginBottom: 16 },
  hud: {
    position: "absolute",
    top: 8,
    left: 8,
    right: 8,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  hudText: {
    flex: 1,
    color: "#EDEFEA",
    fontSize: 12,
    fontVariant: ["tabular-nums"],
    backgroundColor: "rgba(11, 15, 13, 0.72)",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    overflow: "hidden",
  },
  hudButton: {
    backgroundColor: "rgba(11, 15, 13, 0.72)",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  topRow: {
    position: "absolute",
    top: 44,
    left: 8,
    right: 8,
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
  },
  chip: {
    flex: 1,
    backgroundColor: "rgba(11, 15, 13, 0.72)",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  chipTitle: { color: "#EDEFEA", fontSize: 14, fontWeight: "600" },
  chipText: { color: "#C9D1C8", fontSize: 12 },
  button: {
    backgroundColor: "rgba(31, 54, 41, 0.9)",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  buttonText: { color: "#EDEFEA", fontSize: 12, fontWeight: "600" },
  hitChip: {
    position: "absolute",
    bottom: 24,
    alignSelf: "center",
    backgroundColor: "rgba(11, 15, 13, 0.8)",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
});
