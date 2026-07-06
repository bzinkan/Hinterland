import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import {
  ZONE_ACCENT_COLOR,
} from "@/src/sanctuary/diorama/scene/zoneColors";
import {
  deriveBiomeCards,
  TIER_LADDER,
} from "@/src/sanctuary/dioramaui/biomeCards";

describe("biome chooser cards", () => {
  const snapshot = makeSampleSnapshot(20);
  const cards = deriveBiomeCards(snapshot);

  it("derives one card per zone in canonical order", () => {
    expect(cards.map((c) => c.zoneId)).toEqual([...SANCTUARY_ZONE_ORDER]);
  });

  it("is deterministic", () => {
    expect(deriveBiomeCards(snapshot)).toEqual(cards);
  });

  it("mirrors awake/dormant and tier state from the snapshot", () => {
    const byZone = new Map(cards.map((c) => [c.zoneId, c] as const));
    for (const zone of snapshot.zones) {
      const card = byZone.get(zone.zone_id);
      expect(card).toBeDefined();
      expect(card?.dormant).toBe(!zone.unlocked);
      expect(card?.depthTier).toBe(zone.depth_tier);
      expect(card?.observationCount).toBe(zone.observation_count);
      expect(card?.tierIndex).toBe(TIER_LADDER.indexOf(zone.depth_tier as 20));
    }
  });

  it("counts mystery cues only on dormant zones", () => {
    const elsewhere = cards.find((c) => c.zoneId === "elsewhere");
    expect(elsewhere?.dormant).toBe(true);
    expect(elsewhere?.cueCount).toBe(1);
    const meadow = cards.find((c) => c.zoneId === "meadow");
    expect(meadow?.dormant).toBe(false);
    expect(meadow?.cueCount).toBe(0);
  });

  it("gives awake zones their zone accent and dormant zones a muted one", () => {
    const meadow = cards.find((c) => c.zoneId === "meadow");
    expect(meadow?.colors.accent).toBe(ZONE_ACCENT_COLOR.meadow);
    const elsewhere = cards.find((c) => c.zoneId === "elsewhere");
    expect(elsewhere?.colors.accent).not.toBe(ZONE_ACCENT_COLOR.elsewhere);
    // Each awake card smells like its own biome.
    const awake = cards.filter((c) => !c.dormant);
    expect(new Set(awake.map((c) => c.colors.background)).size).toBe(
      awake.length,
    );
  });

  it("writes full TalkBack labels for both states", () => {
    const meadow = cards.find((c) => c.zoneId === "meadow");
    expect(meadow?.a11yLabel).toBe("Meadow, awake, depth 20");
    const elsewhere = cards.find((c) => c.zoneId === "elsewhere");
    expect(elsewhere?.a11yLabel).toBe(
      "Elsewhere, still sleeping, 1 mystery waiting",
    );
    const tier0 = deriveBiomeCards(makeSampleSnapshot(0));
    const sleepyNoCue = tier0.find((c) => c.zoneId === "sky");
    expect(sleepyNoCue?.dormant).toBe(true);
    expect(sleepyNoCue?.a11yLabel).toBe("Sky, still sleeping");
  });
});
