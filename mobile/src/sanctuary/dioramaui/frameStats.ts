/**
 * Rolling frame-time statistics for the spike HUD. A fixed ring of the
 * last N frame durations plus pure fold functions over it -- no React, no
 * reanimated, no Date.now().
 *
 * Shape note: this is free functions over a plain data object (not a
 * closure-based accumulator) because the ring lives inside a reanimated
 * shared value and is pushed from the UI-thread frame callback via
 * `.modify()` -- JS-created closures cannot cross that boundary, plain
 * data + 'worklet'-tagged functions can. Under jest they are ordinary
 * functions.
 */

/** Ring capacity: ~2 s of samples at 60 fps. */
export const FRAME_RING_CAPACITY = 120;

export type FrameRing = {
  /** Fixed-size sample store, milliseconds per frame. */
  samples: number[];
  /** Next write index (wraps). */
  next: number;
  /** Valid sample count (saturates at samples.length). */
  count: number;
};

export type FrameStats = {
  /** 1000 / mean frame ms over the window (0 when empty). */
  avgFps: number;
  /** 95th-percentile frame ms (nearest-rank) over the window. */
  p95Ms: number;
  /** Worst frame ms in the window. */
  worstMs: number;
  /** Samples currently in the window. */
  count: number;
};

export function createFrameRing(
  capacity: number = FRAME_RING_CAPACITY,
): FrameRing {
  "worklet";
  return { samples: new Array<number>(capacity).fill(0), next: 0, count: 0 };
}

/** Push one frame duration; non-finite / non-positive samples are dropped
 * (the first frame after a reset reports a null delta upstream). */
export function pushFrame(ring: FrameRing, frameMs: number): void {
  "worklet";
  if (!Number.isFinite(frameMs) || frameMs <= 0) return;
  ring.samples[ring.next] = frameMs;
  ring.next = (ring.next + 1) % ring.samples.length;
  if (ring.count < ring.samples.length) ring.count++;
}

export function resetFrameRing(ring: FrameRing): void {
  "worklet";
  ring.next = 0;
  ring.count = 0;
}

export function frameStats(ring: FrameRing): FrameStats {
  "worklet";
  if (ring.count === 0) {
    return { avgFps: 0, p95Ms: 0, worstMs: 0, count: 0 };
  }
  const window: number[] = [];
  let sum = 0;
  let worst = 0;
  for (let i = 0; i < ring.count; i++) {
    const ms = ring.samples[i];
    window.push(ms);
    sum += ms;
    if (ms > worst) worst = ms;
  }
  window.sort((a, b) => a - b);
  const p95Index = Math.ceil(0.95 * window.length) - 1;
  return {
    avgFps: 1000 / (sum / window.length),
    p95Ms: window[p95Index],
    worstMs: worst,
    count: ring.count,
  };
}
