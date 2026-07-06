/**
 * App-side manifest <-> content cross-check (mirrors the CI gate in
 * scripts/sanctuary_assets/validate.mjs, but runs against the app's
 * generated sprite manifest in jest so drift fails the mobile suite too).
 * Since D5 the atlas is real and coverage is total: every authored icon
 * key draws an actual sprite, and the seam contract (never throw, always
 * a sprite or a typed fallback) still holds for unknown keys.
 */

import {
  modeledIconKeys,
  resolveElementSprite,
} from "@/src/sanctuary/diorama/assets/manifest";
import {
  SANCTUARY_FALLBACK_SPRITES,
  SANCTUARY_SOUVENIR_SPRITES,
} from "@/src/sanctuary/art/sprites.gen";

// Content files are the source of truth for icon keys (repo-root content/).
/* eslint-disable @typescript-eslint/no-var-requires */
const coarse = require("../../../../../content/sanctuary/coarse_unlocks.json");
const charismatic = require("../../../../../content/sanctuary/charismatic_unlocks.json");
const relationships = require("../../../../../content/sanctuary/relationship_moments.json");
const surprises = require("../../../../../content/sanctuary/tiny_surprises.json");
/* eslint-enable @typescript-eslint/no-var-requires */

function collectIconKeys(node: unknown, out: Set<string>): Set<string> {
  if (Array.isArray(node)) {
    node.forEach((item) => collectIconKeys(item, out));
  } else if (node && typeof node === "object") {
    const record = node as Record<string, unknown>;
    if (typeof record.icon === "string") out.add(record.icon);
    Object.values(record).forEach((value) => collectIconKeys(value, out));
  }
  return out;
}

const contentIconKeys = collectIconKeys(
  [coarse, charismatic, relationships, surprises],
  new Set<string>(),
);

/** Souvenir sprites the diorama requires (ADR 0012 expedition souvenirs). */
const REQUIRED_SOUVENIR_IDS = [
  "backyard_starter",
  "park_starter",
  "street_starter",
  "school_starter",
  "anywhere_starter",
  "backyard_closeup",
  "park_pollinators",
  "street_survivors",
  "school_census",
  "anywhere_collector",
];

describe("sprite manifest <-> sanctuary content", () => {
  it("content defines icon keys (sanity: the fixture import worked)", () => {
    expect(contentIconKeys.size).toBeGreaterThanOrEqual(20);
  });

  it("manifest coverage is EXACTLY the content icon keys (none missing, none stale)", () => {
    // A content key without a sprite would silently draw a fallback shape;
    // a manifest key no content file references is invisible product
    // surface. Both directions fail loudly.
    expect([...modeledIconKeys()].sort()).toEqual([...contentIconKeys].sort());
  });

  it("every content icon key resolves to a real drawn sprite", () => {
    for (const key of contentIconKeys) {
      const resolved = resolveElementSprite(key, "coarse");
      expect(resolved.kind).toBe("sprite");
      if (resolved.kind === "sprite") {
        expect(resolved.sprite.svg).toContain("<svg");
        expect(resolved.sprite.svg).toContain('viewBox="0 0 128 128"');
        expect(resolved.sprite.viewBox).toEqual({ width: 128, height: 128 });
        expect(resolved.sprite.scale).toBeGreaterThan(0);
      }
    }
  });

  it("every required souvenir id has a sprite keyed sanctuary.souvenir.<id>", () => {
    for (const id of REQUIRED_SOUVENIR_IDS) {
      const sprite = SANCTUARY_SOUVENIR_SPRITES[`sanctuary.souvenir.${id}`];
      expect(sprite).toBeDefined();
      expect(sprite.svg).toContain("<svg");
    }
  });

  it("every element type has a drawn fallback motif", () => {
    for (const type of [
      "coarse",
      "charismatic",
      "relationship",
      "surprise",
      "signature",
    ] as const) {
      expect(SANCTUARY_FALLBACK_SPRITES[type].svg).toContain("<svg");
    }
  });

  it("fallback kinds track the element type's shape language", () => {
    expect(resolveElementSprite("no.such.key", "coarse").kind).toBe("fallback");
    expect(resolveElementSprite("no.such.key", "charismatic")).toEqual({
      kind: "fallback",
      fallback: "crystal",
    });
    expect(resolveElementSprite("no.such.key", "relationship")).toEqual({
      kind: "fallback",
      fallback: "ring",
    });
    expect(resolveElementSprite("no.such.key", "surprise")).toEqual({
      kind: "fallback",
      fallback: "trinket",
    });
    expect(resolveElementSprite("no.such.key", "signature")).toEqual({
      kind: "fallback",
      fallback: "landmark",
    });
  });
});
