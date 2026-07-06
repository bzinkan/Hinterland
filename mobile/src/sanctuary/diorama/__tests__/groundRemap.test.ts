import { ISLAND_ART_HALF_WIDTH } from "@/src/sanctuary/diorama/artFit";
import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import {
  GROUND_MARGIN_FRACTION,
  groundHitTest,
  LOCAL_Y_DOMAIN,
  remapIslandToGround,
  SCENE_MAX_HIT,
  SCENE_MIN_HIT,
} from "@/src/sanctuary/diorama/groundRemap";
import { GROUND_REGION, sceneMetrics } from "@/src/sanctuary/diorama/sceneLayout";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import { buildVistaPlan, type IslandPlan } from "@/src/sanctuary/diorama/vistaPlan";

const METRICS = sceneMetrics(390, 800);

function islandFor(tier: number, zoneId: string): IslandPlan {
  const vista = buildVistaPlan(buildScenePlan(makeSampleSnapshot(tier)));
  const island = vista.islands.find((i) => i.zoneId === zoneId);
  if (!island) throw new Error(`no island for ${zoneId}`);
  return island;
}

describe("ground remap", () => {
  const meadow = islandFor(20, "meadow");
  const plan = remapIslandToGround(meadow, METRICS);

  it("is deterministic", () => {
    expect(remapIslandToGround(meadow, METRICS)).toEqual(plan);
  });

  it("preserves painter order and sprite identity", () => {
    expect(plan.sprites.map((s) => s.key)).toEqual(
      meadow.sprites.map((s) => s.key),
    );
    expect(plan.sprites.map((s) => s.kind)).toEqual(
      meadow.sprites.map((s) => s.kind),
    );
  });

  it("keeps every sprite inside the ground region", () => {
    const left = METRICS.sceneWidth * GROUND_MARGIN_FRACTION;
    const right = METRICS.sceneWidth * (1 - GROUND_MARGIN_FRACTION);
    for (const s of plan.sprites) {
      expect(s.x).toBeGreaterThanOrEqual(left - 1e-9);
      expect(s.x).toBeLessThanOrEqual(right + 1e-9);
      expect(s.y).toBeGreaterThanOrEqual(METRICS.h * GROUND_REGION.top - 1e-9);
      expect(s.y).toBeLessThanOrEqual(METRICS.h * GROUND_REGION.bottom + 1e-9);
    }
  });

  it("preserves relative spread (affine x map, monotone y map)", () => {
    // x: an affine map keeps ratios of distances exact.
    const xs = meadow.sprites.map((s) => s.x);
    const sxs = plan.sprites.map((s) => s.x);
    const span = (arr: number[]) => Math.max(...arr) - Math.min(...arr);
    if (span(xs) > 0) {
      const k = span(sxs) / span(xs);
      expect(k).toBeCloseTo(plan.unitScale, 6);
      for (let i = 1; i < xs.length; i++) {
        expect(sxs[i] - sxs[0]).toBeCloseTo((xs[i] - xs[0]) * k, 6);
      }
    }
    // y: order-preserving for in-domain values.
    const inDomain = meadow.sprites
      .map((s, i) => [s.y, plan.sprites[i].y] as const)
      .filter(([ly]) => ly >= LOCAL_Y_DOMAIN.min && ly <= LOCAL_Y_DOMAIN.max)
      .sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < inDomain.length; i++) {
      expect(inDomain[i][1]).toBeGreaterThanOrEqual(inDomain[i - 1][1]);
    }
  });

  it("scales all sprites by one uniform unitScale", () => {
    meadow.sprites.forEach((s, i) => {
      expect(plan.sprites[i].scale).toBeCloseTo(s.scale * plan.unitScale, 6);
    });
    // The dest width over the plateau width.
    expect(plan.unitScale).toBeCloseTo(
      (METRICS.sceneWidth * (1 - 2 * GROUND_MARGIN_FRACTION)) /
        (2 * ISLAND_ART_HALF_WIDTH),
      6,
    );
  });

  it("emits 44dp+ (and capped) hit rects for interactive kinds only", () => {
    const interactive = meadow.sprites.filter((s) => s.kind !== "scenery");
    expect(plan.hitRects.length).toBe(interactive.length);
    for (const r of plan.hitRects) {
      expect(r.w).toBeGreaterThanOrEqual(SCENE_MIN_HIT);
      expect(r.h).toBeGreaterThanOrEqual(SCENE_MIN_HIT);
      expect(r.w).toBeLessThanOrEqual(SCENE_MAX_HIT);
      expect(r.h).toBeLessThanOrEqual(SCENE_MAX_HIT);
    }
    expect(plan.hitRects.map((r) => r.id)).toEqual(
      interactive.map((s) => s.key),
    );
  });

  it("resolves taps: topmost sprite wins, ground pans 1:1", () => {
    const last = plan.sprites[plan.sprites.length - 1];
    const hit = groundHitTest(plan, { x: last.x, y: last.y }, 0);
    // The last-painted sprite at its own foot must be hittable unless a
    // non-interactive kind sits there (then any interactive rect match).
    if (last.kind !== "scenery") {
      expect(hit).toEqual({ type: "sprite", rectId: last.key });
    }
    // Panned camera: the same scene point is hit at screen x - panX.
    const target = plan.hitRects[0];
    const cx = target.x + target.w / 2;
    const cy = target.y + target.h / 2;
    const pan = 120;
    const panned = groundHitTest(plan, { x: cx - pan, y: cy }, pan);
    expect(panned?.type).toBe("sprite");
  });

  it("returns null on empty ground", () => {
    expect(groundHitTest(plan, { x: 5, y: 5 }, 0)).toBeNull();
  });

  it("remaps silhouettes for cued dormant zones with generous targets", () => {
    const elsewhere = islandFor(0, "elsewhere");
    expect(elsewhere.dormant).toBe(true);
    expect(elsewhere.silhouettes.length).toBeGreaterThan(0);
    const dormantPlan = remapIslandToGround(elsewhere, METRICS);
    expect(dormantPlan.silhouettes.length).toBe(elsewhere.silhouettes.length);
    expect(dormantPlan.silhouetteRects.length).toBe(
      dormantPlan.silhouettes.length,
    );
    const marker = dormantPlan.silhouettes[0];
    const rect = dormantPlan.silhouetteRects[0];
    expect(rect.w).toBeGreaterThanOrEqual(SCENE_MIN_HIT);
    expect(
      groundHitTest(dormantPlan, { x: marker.x, y: marker.y }, 0),
    ).toEqual({ type: "silhouette" });
  });
});
