import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import {
  hitTest,
  islandLocalFromScreen,
  screenFromIslandLocal,
  type DioramaViewport,
} from "@/src/sanctuary/diorama/hitTest";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import { ISLAND_SLOTS } from "@/src/sanctuary/diorama/vistaLayout";
import { buildVistaPlan, type HitRect } from "@/src/sanctuary/diorama/vistaPlan";

const IDENTITY: DioramaViewport = { viewX: 0, viewY: 0, viewScale: 1, panX: 0 };

const vista = buildVistaPlan(buildScenePlan(makeSampleSnapshot(3)));

function islandOf(zoneId: Parameters<typeof screenFromIslandLocal>[0]) {
  const island = vista.islands.find((i) => i.zoneId === zoneId);
  if (!island) throw new Error(`no island for ${zoneId}`);
  return island;
}

const contains = (rect: HitRect, p: { x: number; y: number }) =>
  p.x >= rect.x && p.x <= rect.x + rect.w && p.y >= rect.y && p.y <= rect.y + rect.h;

describe("screenFromIslandLocal / islandLocalFromScreen", () => {
  it("round-trips through a zoomed, panned viewport", () => {
    const viewport: DioramaViewport = { viewX: 37, viewY: -12, viewScale: 1.8, panX: 140 };
    const local = { x: 13.5, y: -8.25 };
    const screen = screenFromIslandLocal("woodland", ISLAND_SLOTS.woodland, viewport, local);
    const back = islandLocalFromScreen("woodland", ISLAND_SLOTS.woodland, viewport, screen);
    expect(back.x).toBeCloseTo(local.x, 8);
    expect(back.y).toBeCloseTo(local.y, 8);
  });
});

describe("hitTest -- vista mode", () => {
  it("golden tap: the meadow anchor under a scaled, panned camera", () => {
    const viewport: DioramaViewport = { viewX: 10, viewY: 20, viewScale: 0.5, panX: 100 };
    // meadow: slot (330, 560), fore band (parallax 1.0)
    // anchor = ((330 - 100 * 1.0) * 0.5 + 10, 560 * 0.5 + 20) = (125, 300)
    const point = { x: 125, y: 300 };
    expect(
      screenFromIslandLocal("meadow", ISLAND_SLOTS.meadow, viewport, { x: 0, y: 0 }),
    ).toEqual(point);
    expect(hitTest(vista, viewport, point, "vista", null)).toEqual({
      type: "island",
      zoneId: "meadow",
    });
  });

  it("applies each island's parallax factor to the pan", () => {
    const viewport: DioramaViewport = { viewX: 0, viewY: 0, viewScale: 1, panX: 200 };
    // woodland: slot (180, 200), back band (parallax 0.35)
    // anchor = (180 - 200 * 0.35, 200) = (110, 200)
    expect(hitTest(vista, viewport, { x: 110, y: 200 }, "vista", null)).toEqual({
      type: "island",
      zoneId: "woodland",
    });
    // Where woodland WOULD sit if it wrongly panned 1:1 (180 - 200 = -20):
    // nothing lives there, so the tap must miss.
    expect(hitTest(vista, viewport, { x: -20, y: 200 }, "vista", null)).toBeNull();
  });

  it("dormant islands are tappable too (mystery-cue taps)", () => {
    const elsewhere = islandOf("elsewhere");
    expect(elsewhere.dormant).toBe(true);
    expect(
      hitTest(vista, IDENTITY, { x: ISLAND_SLOTS.elsewhere.x, y: ISLAND_SLOTS.elsewhere.y }, "vista", null),
    ).toEqual({ type: "island", zoneId: "elsewhere" });
  });

  it("misses empty sky", () => {
    expect(hitTest(vista, IDENTITY, { x: 5000, y: 5000 }, "vista", null)).toBeNull();
  });
});

describe("hitTest -- dive mode", () => {
  const dive: DioramaViewport = { viewX: -300, viewY: -800, viewScale: 2.4, panX: 0 };

  it("golden tap: returns the topmost sprite (reverse painter order)", () => {
    const meadow = islandOf("meadow");
    expect(meadow.hitRects.length).toBeGreaterThan(0);
    // Aim at the center of the FIRST (painted-earliest) rect; whatever
    // painter-later rect overlaps that point must win the tap.
    const first = meadow.hitRects[0];
    const local = { x: first.x + first.w / 2, y: first.y + first.h / 2 };
    const expected = [...meadow.hitRects].reverse().find((r) => contains(r, local));
    const point = screenFromIslandLocal("meadow", meadow.slot, dive, local);
    expect(hitTest(vista, dive, point, "dive", "meadow")).toEqual({
      type: "sprite",
      islandZoneId: "meadow",
      rectId: expected?.id,
    });
  });

  it("restricts hits to the dived island", () => {
    const meadow = islandOf("meadow");
    const first = meadow.hitRects[0];
    const local = { x: first.x + first.w / 2, y: first.y + first.h / 2 };
    const point = screenFromIslandLocal("meadow", meadow.slot, IDENTITY, local);
    // Same tap, but diving the pond: the meadow sprite must not resolve.
    expect(hitTest(vista, IDENTITY, point, "dive", "pond")).toBeNull();
  });

  it("golden tap: silhouette markers on a cued dormant island", () => {
    const elsewhere = islandOf("elsewhere");
    expect(elsewhere.silhouettes).toHaveLength(1);
    const marker = elsewhere.silhouettes[0];
    const point = screenFromIslandLocal(
      "elsewhere",
      elsewhere.slot,
      dive,
      { x: marker.x, y: marker.y },
    );
    expect(hitTest(vista, dive, point, "dive", "elsewhere")).toEqual({
      type: "silhouette",
      zoneId: "elsewhere",
    });
  });

  it("returns null without a dived island or off every target", () => {
    expect(hitTest(vista, dive, { x: 0, y: 0 }, "dive", null)).toBeNull();
    const meadow = islandOf("meadow");
    const farCorner = screenFromIslandLocal("meadow", meadow.slot, dive, { x: 900, y: 900 });
    expect(hitTest(vista, dive, farCorner, "dive", "meadow")).toBeNull();
  });
});
