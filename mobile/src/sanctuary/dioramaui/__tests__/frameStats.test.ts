import {
  createFrameRing,
  FRAME_RING_CAPACITY,
  frameStats,
  pushFrame,
  resetFrameRing,
} from "@/src/sanctuary/dioramaui/frameStats";

describe("frameStats", () => {
  it("reports zeros on an empty ring", () => {
    expect(frameStats(createFrameRing())).toEqual({
      avgFps: 0,
      p95Ms: 0,
      worstMs: 0,
      count: 0,
    });
  });

  it("computes avg / p95 / worst over a partial window", () => {
    const ring = createFrameRing();
    // A steady 16.67 ms cadence with one 50 ms hitch.
    for (let i = 0; i < 19; i++) pushFrame(ring, 16.67);
    pushFrame(ring, 50);
    const stats = frameStats(ring);
    expect(stats.count).toBe(20);
    expect(stats.worstMs).toBe(50);
    expect(stats.p95Ms).toBe(16.67); // nearest-rank: 19th of 20 sorted
    const expectedAvg = (19 * 16.67 + 50) / 20;
    expect(stats.avgFps).toBeCloseTo(1000 / expectedAvg, 6);
  });

  it("uses nearest-rank p95 on a known 1..100 sequence", () => {
    const ring = createFrameRing(100);
    for (let ms = 1; ms <= 100; ms++) pushFrame(ring, ms);
    expect(frameStats(ring).p95Ms).toBe(95);
  });

  it("keeps only the newest samples once the ring wraps", () => {
    const ring = createFrameRing(4);
    for (const ms of [100, 100, 100, 100]) pushFrame(ring, ms);
    // Overwrite the window with fast frames.
    for (const ms of [10, 10, 10, 10]) pushFrame(ring, ms);
    const stats = frameStats(ring);
    expect(stats.count).toBe(4);
    expect(stats.worstMs).toBe(10);
    expect(stats.avgFps).toBeCloseTo(100, 6);
  });

  it("saturates count at capacity while continuing to roll", () => {
    const ring = createFrameRing(8);
    for (let i = 0; i < 50; i++) pushFrame(ring, 20);
    expect(frameStats(ring).count).toBe(8);
    expect(frameStats(ring).avgFps).toBeCloseTo(50, 6);
  });

  it("drops non-finite and non-positive samples", () => {
    const ring = createFrameRing();
    pushFrame(ring, NaN);
    pushFrame(ring, Infinity);
    pushFrame(ring, 0);
    pushFrame(ring, -5);
    expect(frameStats(ring).count).toBe(0);
    pushFrame(ring, 16);
    expect(frameStats(ring).count).toBe(1);
  });

  it("reset empties the window", () => {
    const ring = createFrameRing();
    for (let i = 0; i < 10; i++) pushFrame(ring, 16);
    resetFrameRing(ring);
    expect(frameStats(ring)).toEqual({
      avgFps: 0,
      p95Ms: 0,
      worstMs: 0,
      count: 0,
    });
    // And keeps accepting fresh samples afterwards.
    pushFrame(ring, 25);
    expect(frameStats(ring).worstMs).toBe(25);
  });

  it("defaults to a ~2 s window at 60 fps", () => {
    expect(FRAME_RING_CAPACITY).toBe(120);
    expect(createFrameRing().samples).toHaveLength(120);
  });
});
