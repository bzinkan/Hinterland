import type {
  SanctuaryElementDto,
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import {
  buildVistaPlan,
  MIN_HIT_SIZE,
  SOUVENIR_SCALE,
  type IslandPlan,
} from "@/src/sanctuary/diorama/vistaPlan";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeZone(
  zoneId: SanctuaryZoneId,
  depthTier: number,
): SanctuarySnapshotDto["zones"][number] {
  return {
    zone_id: zoneId,
    title: zoneId,
    mood: "",
    description: "",
    observation_count: depthTier,
    depth_tier: depthTier,
    unlocked: depthTier >= 1,
    next_threshold: depthTier >= 50 ? null : 50,
    accent: null,
  };
}

function makeElement(id: string, zone: SanctuaryZoneId): SanctuaryElementDto {
  return {
    element_id: id,
    zone_id: zone,
    element_type: "coarse",
    title: id,
    detail: "",
    icon: `sanctuary.${zone}.test`,
    taxon_id: null,
    source_observation_id: null,
    unlocked_at: "2026-06-01T00:00:00Z",
    payload: {},
  };
}

function makeSnapshot(
  overrides: Partial<SanctuarySnapshotDto> = {},
): SanctuarySnapshotDto {
  return {
    zones: SANCTUARY_ZONE_ORDER.map((z) => makeZone(z, 0)),
    elements: [],
    recent_events: [],
    guide_message: { speaker: "guide", text: "" },
    mystery_cues: [],
    journal: [],
    identity_reflection: null,
    relationship_moments: [],
    tiny_surprises: [],
    season: {
      season: "spring",
      background_tone: "fresh",
      zone_accents: {
        meadow: "", woodland: "", pond: "", sky: "", soil: "", urban: "", elsewhere: "",
      },
      variant_copy: null,
    },
    soundscapes: [],
    sound_assets_available: false,
    ...overrides,
  };
}

function islandOf(plan: { islands: IslandPlan[] }, zoneId: SanctuaryZoneId) {
  const island = plan.islands.find((i) => i.zoneId === zoneId);
  if (!island) throw new Error(`no island for ${zoneId}`);
  return island;
}

const BAND_ORDER = { back: 0, mid: 1, fore: 2 } as const;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("buildVistaPlan", () => {
  it("is deterministic: same input twice -> deep-equal plans", () => {
    const scene = buildScenePlan(makeSampleSnapshot(5));
    expect(buildVistaPlan(scene, ["souv_a"])).toEqual(
      buildVistaPlan(scene, ["souv_a"]),
    );
    // And so is rebuilding the scene plan from the same snapshot.
    expect(
      buildVistaPlan(buildScenePlan(makeSampleSnapshot(5))),
    ).toEqual(buildVistaPlan(buildScenePlan(makeSampleSnapshot(5))));
  });

  it("carries all 7 islands sorted back -> front", () => {
    const vista = buildVistaPlan(buildScenePlan(makeSampleSnapshot(3)));
    expect(vista.islands.map((i) => i.zoneId).sort()).toEqual(
      [...SANCTUARY_ZONE_ORDER].sort(),
    );
    const bands = vista.islands.map((i) => BAND_ORDER[i.slot.band]);
    for (let i = 1; i < bands.length; i++) {
      expect(bands[i]).toBeGreaterThanOrEqual(bands[i - 1]);
    }
  });

  it("painter-sorts each island's sprites by depth", () => {
    const vista = buildVistaPlan(buildScenePlan(makeSampleSnapshot(10)));
    for (const island of vista.islands) {
      for (let i = 1; i < island.sprites.length; i++) {
        expect(island.sprites[i].depth).toBeGreaterThanOrEqual(
          island.sprites[i - 1].depth,
        );
      }
    }
  });

  it("gates dressing by the crossed tier (tierMin <= depthTier)", () => {
    const tier1 = buildVistaPlan(
      buildScenePlan(makeSnapshot({ zones: [makeZone("meadow", 1)] })),
    );
    const tier5 = buildVistaPlan(
      buildScenePlan(makeSnapshot({ zones: [makeZone("meadow", 5)] })),
    );
    const keysAt = (island: IslandPlan) =>
      new Set(
        island.sprites
          .filter((s) => s.kind === "scenery")
          .map((s) => s.key.split("#")[0]),
      );
    const at1 = keysAt(islandOf(tier1, "meadow"));
    const at5 = keysAt(islandOf(tier5, "meadow"));
    expect(at1).toEqual(new Set(["bush"])); // only tierMin 1 meadow rule
    expect(at5.has("bush")).toBe(true);
    expect(at5.has("flower-purple")).toBe(true); // tierMin 3
    expect(at5.has("flower-red")).toBe(true); // tierMin 5
  });

  it("dormant islands carry no dressing and no element sprites", () => {
    const vista = buildVistaPlan(
      buildScenePlan(makeSnapshot({ zones: [makeZone("woodland", 0)] })),
    );
    const woodland = islandOf(vista, "woodland");
    expect(woodland.dormant).toBe(true);
    expect(woodland.sprites).toHaveLength(0);
  });

  it("marks cued dormant zones with a silhouette", () => {
    const vista = buildVistaPlan(
      buildScenePlan(
        makeSnapshot({
          zones: [makeZone("elsewhere", 0), makeZone("meadow", 1)],
          mystery_cues: [{ zone_id: "elsewhere", title: "", detail: "" }],
        }),
      ),
    );
    expect(islandOf(vista, "elsewhere").silhouettes).toHaveLength(1);
    expect(islandOf(vista, "meadow").silhouettes).toHaveLength(0);
  });

  it("emits hit rects at the 44-unit floor, centered on their sprite", () => {
    const vista = buildVistaPlan(
      buildScenePlan(makeSampleSnapshot(10)),
      ["souv_a", "souv_b"],
    );
    for (const island of vista.islands) {
      const spriteByKey = new Map(island.sprites.map((s) => [s.key, s]));
      for (const rect of island.hitRects) {
        expect(rect.w).toBeGreaterThanOrEqual(MIN_HIT_SIZE);
        expect(rect.h).toBeGreaterThanOrEqual(MIN_HIT_SIZE);
        const sprite = spriteByKey.get(rect.id);
        expect(sprite).toBeDefined();
        expect(rect.x + rect.w / 2).toBeCloseTo(sprite!.x, 6);
        expect(rect.y + rect.h / 2).toBeCloseTo(sprite!.y, 6);
      }
    }
  });

  it("keeps scenery decorative: only elements and souvenirs get hit rects", () => {
    const vista = buildVistaPlan(
      buildScenePlan(makeSampleSnapshot(10)),
      ["souv_a"],
    );
    for (const island of vista.islands) {
      for (const rect of island.hitRects) {
        expect(rect.kind).not.toBe("scenery");
      }
    }
  });

  it("souvenir ring is independent: adding one moves no element sprite", () => {
    const scene = buildScenePlan(makeSampleSnapshot(5));
    const without = buildVistaPlan(scene);
    const withSouvenirs = buildVistaPlan(scene, ["souv_a", "souv_b"]);
    for (const zoneId of SANCTUARY_ZONE_ORDER) {
      const elements = (plan: typeof without) =>
        islandOf(plan, zoneId).sprites.filter((s) => s.kind === "element");
      expect(elements(withSouvenirs)).toEqual(elements(without));
    }
    const souvenirs = islandOf(withSouvenirs, "meadow").sprites.filter(
      (s) => s.kind === "souvenir",
    );
    expect(souvenirs.map((s) => s.key).sort()).toEqual(["souv_a", "souv_b"]);
    for (const s of souvenirs) {
      expect(s.iconKey).toBeNull();
      // 0.75 base scale, modulated only by the false perspective.
      expect(s.scale).toBeGreaterThan(SOUVENIR_SCALE * 0.8);
      expect(s.scale).toBeLessThan(SOUVENIR_SCALE * 1.2);
    }
  });

  it("passes the invitation state through", () => {
    expect(
      buildVistaPlan(buildScenePlan(makeSampleSnapshot(0))).isInvitationState,
    ).toBe(true);
    expect(
      buildVistaPlan(buildScenePlan(makeSampleSnapshot(3))).isInvitationState,
    ).toBe(false);
  });
});
