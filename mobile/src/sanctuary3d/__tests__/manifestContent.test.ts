/**
 * App-side manifest <-> content cross-check (mirrors the CI gate in
 * scripts/sanctuary_assets/validate.mjs, but runs against the app's
 * generated manifest in jest so drift fails the mobile suite too).
 */

import { modeledIconKeys, resolveElementAsset } from "@/src/sanctuary3d/assets/manifest";

// Content files are the source of truth for icon keys (repo-root content/).
/* eslint-disable @typescript-eslint/no-var-requires */
const coarse = require("../../../../content/sanctuary/coarse_unlocks.json");
const charismatic = require("../../../../content/sanctuary/charismatic_unlocks.json");
const relationships = require("../../../../content/sanctuary/relationship_moments.json");
const surprises = require("../../../../content/sanctuary/tiny_surprises.json");
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

describe("asset manifest <-> sanctuary content", () => {
  it("content defines icon keys (sanity: the fixture import worked)", () => {
    expect(contentIconKeys.size).toBeGreaterThanOrEqual(20);
  });

  it("every MODELED manifest key corresponds to a real content icon key", () => {
    // A manifest entry whose key no content file references is stale and
    // would be invisible product surface -- fail loudly.
    for (const key of modeledIconKeys()) {
      expect(contentIconKeys.has(key)).toBe(true);
    }
  });

  it("every content icon key resolves to a model or a clean null fallback", () => {
    // resolveElementAsset must never throw for any authored key; null means
    // the scene renders the typed FallbackShape (placeholders.json keys).
    for (const key of contentIconKeys) {
      const resolved = resolveElementAsset(key);
      expect(resolved === null || typeof resolved.module === "number").toBe(true);
    }
  });
});
