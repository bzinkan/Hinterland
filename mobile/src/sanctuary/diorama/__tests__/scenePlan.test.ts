import type {
  SanctuaryElementDto,
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import { buildScenePlan } from "@/src/sanctuary/diorama/scenePlan";
import {
  DEV_TIER_LADDER,
  makeSampleSnapshot,
} from "@/src/sanctuary/diorama/dev/sampleSnapshot";

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

function makeElement(
  id: string,
  zone: SanctuaryZoneId,
  icon = `sanctuary.${zone}.test`,
): SanctuaryElementDto {
  return {
    element_id: id,
    zone_id: zone,
    element_type: "coarse",
    title: id,
    detail: "",
    icon,
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
    guide_message: { speaker: "dragonfly", text: "" },
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("buildScenePlan", () => {
  it("is pure and deterministic: same snapshot -> deep-equal plans", () => {
    const snapshot = makeSnapshot({
      zones: SANCTUARY_ZONE_ORDER.map((z, i) => makeZone(z, i % 2 ? 3 : 0)),
      elements: [
        makeElement("e1", "woodland"),
        makeElement("e2", "woodland"),
        makeElement("e3", "sky"),
      ],
    });
    expect(buildScenePlan(snapshot)).toEqual(buildScenePlan(snapshot));
  });

  it("element placement ignores API ordering (sorted by element_id)", () => {
    const a = makeSnapshot({
      zones: [makeZone("meadow", 3)],
      elements: [makeElement("e1", "meadow"), makeElement("e2", "meadow")],
    });
    const b = makeSnapshot({
      zones: [makeZone("meadow", 3)],
      elements: [makeElement("e2", "meadow"), makeElement("e1", "meadow")],
    });
    expect(buildScenePlan(a)).toEqual(buildScenePlan(b));
  });

  it("maps unlocked/dormant and tier values through to the zone plans", () => {
    const plan = buildScenePlan(
      makeSnapshot({
        zones: [makeZone("meadow", 10), makeZone("pond", 0)],
      }),
    );
    const meadow = plan.zones.find((z) => z.zoneId === "meadow");
    const pond = plan.zones.find((z) => z.zoneId === "pond");
    expect(meadow?.unlocked).toBe(true);
    expect(meadow?.depthTier).toBe(10);
    expect(pond?.unlocked).toBe(false);
  });

  it("flags mystery silhouettes only on dormant cued zones", () => {
    const plan = buildScenePlan(
      makeSnapshot({
        zones: [makeZone("meadow", 3), makeZone("pond", 0), makeZone("urban", 0)],
        mystery_cues: [
          { zone_id: "meadow", title: "", detail: "" },
          { zone_id: "pond", title: "", detail: "" },
        ],
      }),
    );
    expect(plan.zones.find((z) => z.zoneId === "meadow")?.silhouette).toBe(false); // awake
    expect(plan.zones.find((z) => z.zoneId === "pond")?.silhouette).toBe(true); // dormant + cued
    expect(plan.zones.find((z) => z.zoneId === "urban")?.silhouette).toBe(false); // dormant, no cue
  });

  it("never throws on unknown icon keys; falls back typed (fallback kind)", () => {
    const plan = buildScenePlan(
      makeSnapshot({
        zones: [makeZone("meadow", 3)],
        elements: [makeElement("e1", "meadow", "sanctuary.meadow.NOT_A_REAL_KEY")],
      }),
    );
    const placed = plan.zones.find((z) => z.zoneId === "meadow")?.elements[0];
    expect(placed).toBeDefined();
    expect(placed?.sprite).toEqual({ kind: "fallback", fallback: "dome" });
    expect(placed?.transform.position).toHaveLength(3);
  });

  it("invitation state only when nothing is awake and nothing is unlocked", () => {
    expect(buildScenePlan(makeSnapshot()).isInvitationState).toBe(true);
    expect(
      buildScenePlan(makeSnapshot({ zones: [makeZone("meadow", 1)] }))
        .isInvitationState,
    ).toBe(false);
  });

  it("zones come out in authored order regardless of input order", () => {
    const shuffled = [...SANCTUARY_ZONE_ORDER].reverse();
    const plan = buildScenePlan(
      makeSnapshot({ zones: shuffled.map((z) => makeZone(z, 1)) }),
    );
    expect(plan.zones.map((z) => z.zoneId)).toEqual([...SANCTUARY_ZONE_ORDER]);
  });

  it.each(DEV_TIER_LADDER)(
    "handles the dev sample snapshot at tier %d without throwing",
    (tier) => {
      const plan = buildScenePlan(makeSampleSnapshot(tier));
      expect(plan.zones).toHaveLength(7);
      if (tier === 0) {
        expect(plan.isInvitationState).toBe(true);
      } else {
        expect(plan.isInvitationState).toBe(false);
        expect(
          plan.zones.find((z) => z.zoneId === "meadow")?.depthTier,
        ).toBe(tier);
      }
    },
  );
});
