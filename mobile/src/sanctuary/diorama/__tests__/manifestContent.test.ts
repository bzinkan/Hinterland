/**
 * App-side manifest <-> content cross-check (mirrors the CI gate in
 * scripts/sanctuary_assets/validate.mjs, but runs against the app's
 * generated sprite manifest in jest so drift fails the mobile suite too).
 * The atlas is a stub until D5; the seam contract (never throw, always a
 * sprite or a typed fallback) is what these tests pin down.
 */

import {
  modeledIconKeys,
  resolveElementSprite,
} from "@/src/sanctuary/diorama/assets/manifest";

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

describe("sprite manifest <-> sanctuary content", () => {
  it("content defines icon keys (sanity: the fixture import worked)", () => {
    expect(contentIconKeys.size).toBeGreaterThanOrEqual(20);
  });

  it("every DRAWN manifest key corresponds to a real content icon key", () => {
    // A manifest entry whose key no content file references is stale and
    // would be invisible product surface -- fail loudly.
    for (const key of modeledIconKeys()) {
      expect(contentIconKeys.has(key)).toBe(true);
    }
  });

  it("every content icon key resolves to a sprite or a typed fallback", () => {
    // resolveElementSprite must never throw for any authored key; a
    // fallback kind means the diorama draws the simple typed shape.
    for (const key of contentIconKeys) {
      const resolved = resolveElementSprite(key, "coarse");
      if (resolved.kind === "sprite") {
        expect(typeof resolved.sprite.module).toBe("number");
      } else {
        expect(resolved.fallback).toBe("dome");
      }
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
