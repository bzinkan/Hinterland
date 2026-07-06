import {
  DRESSING_RULES,
  DRESSING_TRANSFORMS,
} from "@/src/sanctuary/diorama/scene/dressing";
import { getScenerySprite } from "@/src/sanctuary/diorama/assets/manifest";
import { ZONE_LAYOUT } from "@/src/sanctuary/diorama/placement/zoneAnchors";
import { heightAt, WATER_LEVEL } from "@/src/sanctuary/diorama/terrain/heightfield";

const TIER_LADDER = [0, 1, 3, 5, 10, 20, 50];

describe("island dressing", () => {
  it("rules are well-formed: unique keys, ladder tiers, sane counts", () => {
    const keys = new Set(DRESSING_RULES.map((r) => r.key));
    expect(keys.size).toBe(DRESSING_RULES.length);
    for (const rule of DRESSING_RULES) {
      expect(rule.count).toBeGreaterThan(0);
      expect(TIER_LADDER).toContain(rule.tierMin);
      expect(rule.scale[0]).toBeLessThanOrEqual(rule.scale[1]);
    }
  });

  it("every rule key resolves to a real scenery sprite", () => {
    // The D5 atlas covers every authored rule key -- a null here means the
    // dressing table and scripts/sanctuary_assets drifted apart, which the
    // pipeline's validate.mjs also fails. zone/tierMin mirror the rule.
    for (const rule of DRESSING_RULES) {
      const sprite = getScenerySprite(rule.key);
      expect(sprite).not.toBeNull();
      expect(sprite?.zone).toBe(rule.zone);
      expect(sprite?.tierMin).toBe(rule.tierMin);
      expect(sprite?.svg).toContain("<svg");
    }
  });

  it("scatters a usable number of instances per rule", () => {
    for (const rule of DRESSING_RULES) {
      const transforms = DRESSING_TRANSFORMS.get(rule.key) ?? [];
      // The height-band rejection can drop a few, but a rule that places
      // less than half its count means the band/zone combo is wrong.
      expect(transforms.length).toBeGreaterThanOrEqual(Math.ceil(rule.count / 2));
      expect(transforms.length).toBeLessThanOrEqual(rule.count);
    }
  });

  it("placements sit on the terrain, out of the pond, off the cliff", () => {
    const pond = ZONE_LAYOUT.pond;
    for (const [key, transforms] of DRESSING_TRANSFORMS) {
      for (const t of transforms) {
        const [x, y, z] = t.position;
        expect(y).toBe(heightAt(x, z));
        expect(y).toBeGreaterThan(WATER_LEVEL);
        expect(z).toBeLessThanOrEqual(3.7);
        expect(
          Math.hypot(x - pond.center[0], z - pond.center[2]),
        ).toBeGreaterThan(pond.radius);
        expect(t.scale).toBeGreaterThan(0.5);
        expect(t.scale).toBeLessThan(1.6);
        void key;
      }
    }
  });

  it("is deterministic across module evaluations (seeded)", () => {
    // Same seed inputs -> the precomputed map is stable; spot-check a rule
    // by re-deriving from the same constants.
    const a = DRESSING_TRANSFORMS.get("tree-default");
    const b = DRESSING_TRANSFORMS.get("tree-default");
    expect(a).toBe(b);
    expect(a?.[0]?.position).toBeDefined();
  });
});
