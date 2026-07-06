/**
 * One island's Skia subtree. The transform chain here IS the hitTest
 * contract (screenFromIslandLocal): the caller wraps everything in the
 * viewport translate/scale group; this component applies the per-island
 * anchor group at (slot.x - panX * parallaxFor(zone), slot.y) with scale
 * slot.islandScale, and band parallax / wind sway are art-only offsets on
 * NON-interactive layers inside the anchor group -- exactly the D4 spike
 * pattern.
 *
 * Per-frame motion is shared values -> useDerivedValue only. React state
 * changes happen exclusively at the wake moment (mount/unmount of the
 * transient saturation layer, once per dormant->awake transition).
 */

import { memo, useEffect, useMemo, useRef } from "react";
import {
  Easing,
  useDerivedValue,
  useSharedValue,
  withTiming,
  type SharedValue,
} from "react-native-reanimated";
import type { SkSVG, Transforms3d } from "@shopify/react-native-skia";

import { LAYER_BOTTOM_Y } from "@/src/sanctuary/diorama/artFit";
import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import {
  PARALLAX_FACTOR,
  parallaxFor,
} from "@/src/sanctuary/diorama/vistaLayout";
import type { IslandPlan } from "@/src/sanctuary/diorama/vistaPlan";
import {
  recordIslandPictures,
  type SkiaApi,
} from "@/src/sanctuary/dioramaui/islandArt";
import {
  DORMANT_SAT,
  satMatrix,
} from "@/src/sanctuary/dioramaui/paletteSlots";
import type { SlotHexes, SvgCache } from "@/src/sanctuary/dioramaui/svgCache";

type SkiaModule = typeof import("@shopify/react-native-skia");

/** Wind: one full sway cycle; skew radians at amplitude 1 (D4 values). */
export const WIND_PERIOD_MS = 4800;
const WIND_SKEW = 0.012;

/** The wake moment: 1.2s saturation lerp, then the layer unmounts. */
const WAKE_TIMING = { duration: 1200, easing: Easing.inOut(Easing.cubic) };

export type IslandGroupProps = {
  skia: SkiaModule;
  island: IslandPlan;
  /** Palette for this island's current state (base / dive-accent / dormant-baked). */
  slots: SlotHexes;
  silhouetteSlots: SlotHexes;
  svgCache: SvgCache<SkSVG>;
  panX: SharedValue<number>;
  windT: SharedValue<number>;
  /** Per-island sway phase offset so the archipelago does not sway in lockstep. */
  windPhase: number;
  /** True while this island runs its dormant->awake saturation lerp. */
  waking: boolean;
  /**
   * Whether the Sanctuary screen is focused. Wake refetches land while the
   * tab is blurred-but-mounted, so an armed wake HOLDS at the pinned
   * dormant saturation until focus and only then starts the lerp --
   * otherwise the ceremony would play offscreen and never be seen.
   */
  focused: boolean;
  onWakeEnd: (zoneId: SanctuaryZoneId) => void;
};

function IslandGroupInner({
  skia,
  island,
  slots,
  silhouetteSlots,
  svgCache,
  panX,
  windT,
  windPhase,
  waking,
  focused,
  onWakeEnd,
}: IslandGroupProps) {
  const { ColorMatrix, Group, Paint, Picture, Skia } = skia;
  const slot = island.slot;
  const islandParallax = parallaxFor(island.zoneId);

  // Re-records exactly when the plan or this island's palette changes
  // (slots references are stable per state in the parent), never per frame.
  const pictures = useMemo(
    () =>
      recordIslandPictures({
        Skia: Skia as SkiaApi,
        island,
        slots,
        silhouetteSlots,
        svgCache,
      }),
    [Skia, island, slots, silhouetteSlots, svgCache],
  );

  // Dormant islands do NOT sway; the silhouette hint on them does.
  const swayAmp = island.dormant ? 0 : WIND_SKEW;

  // Intra-island band depth only makes sense when the band is FARTHER
  // than the island itself (band factor < island parallax). On far
  // islands (sky 0.1, woodland/elsewhere 0.35) the raw formula inverts
  // and can push a band clear off its plateau at the pan clamp —
  // device-verified on the sky island, whose cloud band detached. Clamp
  // each band factor to the island's own parallax so far islands move
  // as one rigid piece while fore islands keep their full depth.
  const backFactor = Math.min(PARALLAX_FACTOR.back, islandParallax);
  const midFactor = Math.min(PARALLAX_FACTOR.mid, islandParallax);

  const anchorTransform = useDerivedValue<Transforms3d>(() => [
    { translateX: slot.x - panX.value * islandParallax },
    { translateY: slot.y },
    { scale: slot.islandScale },
  ]);
  const backTransform = useDerivedValue<Transforms3d>(() => [
    {
      translateX:
        (panX.value * (islandParallax - backFactor)) / slot.islandScale,
    },
  ]);
  const midSwayTransform = useDerivedValue<Transforms3d>(() => {
    const skew = Math.sin((windT.value + windPhase) * 2 * Math.PI) * swayAmp;
    return [
      {
        translateX:
          (panX.value * (islandParallax - midFactor)) / slot.islandScale,
      },
      { translateY: LAYER_BOTTOM_Y },
      { skewX: skew },
      { translateY: -LAYER_BOTTOM_Y },
    ];
  });
  const foreSwayTransform = useDerivedValue<Transforms3d>(() => {
    const skew =
      Math.sin((windT.value + windPhase + 0.4) * 2 * Math.PI) * swayAmp * 1.6;
    return [
      { translateY: LAYER_BOTTOM_Y },
      { skewX: skew },
      { translateY: -LAYER_BOTTOM_Y },
    ];
  });
  const silhouetteSwayTransform = useDerivedValue<Transforms3d>(() => {
    const skew =
      Math.sin((windT.value + windPhase + 0.2) * 2 * Math.PI) * WIND_SKEW * 1.2;
    return [
      { translateY: LAYER_BOTTOM_Y },
      { skewX: skew },
      { translateY: -LAYER_BOTTOM_Y },
    ];
  });

  // Wake moment: transient saveLayer for the 1.2s lerp only (contract 3 of
  // the D7 plan) -- steady-state dormant islands draw baked-desaturated
  // pictures with no layer at all. The saturation is pinned to DORMANT_SAT
  // DURING the waking render (shared-value write is just a ref write) so
  // the very first awake-colored frame is pixel-equivalent to the baked
  // dormant look; the effect then starts the lerp -- but ONLY once the
  // screen is focused. Wake snapshots arrive while the tab is blurred
  // (observe-submit et al. invalidate the sanctuary query from other
  // screens), so an unfocused armed wake just holds the pin; the ceremony
  // starts on the first focused commit and is actually visible.
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
    const timer = setTimeout(
      () => onWakeEnd(island.zoneId),
      WAKE_TIMING.duration + 100,
    );
    return () => clearTimeout(timer);
  }, [waking, focused, island.zoneId, onWakeEnd, wakeSat]);
  const wakeMatrix = useDerivedValue(() => satMatrix(wakeSat.value));

  return (
    <Group transform={anchorTransform}>
      <Group
        layer={
          waking ? (
            <Paint>
              <ColorMatrix matrix={wakeMatrix} />
            </Paint>
          ) : undefined
        }
      >
        <Group transform={backTransform}>
          <Picture picture={pictures.back} />
        </Group>
        <Picture picture={pictures.base} />
        <Group transform={midSwayTransform}>
          <Picture picture={pictures.mid} />
        </Group>
        <Picture picture={pictures.sprites} />
        <Group transform={foreSwayTransform}>
          <Picture picture={pictures.fore} />
        </Group>
        {pictures.silhouette !== null ? (
          <Group transform={silhouetteSwayTransform}>
            <Picture picture={pictures.silhouette} />
          </Group>
        ) : null}
      </Group>
    </Group>
  );
}

/** Memoized so an unrelated state change never re-records siblings. */
export const IslandGroup = memo(IslandGroupInner);
