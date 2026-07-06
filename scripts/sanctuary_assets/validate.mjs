/**
 * CI gate for Sanctuary 2.5D diorama art (mirrors scripts/validate_content.py:
 * per-item report, exit 0/1).
 *
 * Checks:
 *   1. Coverage -- every icon key in content/sanctuary/*.json resolves to an
 *      element sprite OR is allowlisted in placeholders.json (never both,
 *      no stale entries either way); every required souvenir id has a
 *      sprite (the 10 expedition ids are required whether or not
 *      content/sanctuary/souvenirs.json has landed yet); every
 *      DRESSING_RULES scenery key has a sprite whose zone/tierMin mirror
 *      the rule; all five element_type fallbacks exist.
 *   2. Files    -- every assets.json entry's svg exists; no orphan svg files.
 *   3. Allowlist-- every committed svg passes the ADR 0012 Skia lint
 *      (elements, attributes, {{token}} vocabulary, viewBox).
 *   4. Budgets  -- layer-svg <= 8 KB, sprite-svg <= 6 KB (assets.json
 *      categories), total svg/ payload <= 400 KB.
 *   5. Licenses -- every sources.json entry is OWNED (with provenance) or
 *      CC0; every assets.json entry references a ledger id.
 *   6. Drift    -- the committed mobile/src/sanctuary/art/*.gen.ts match a
 *      fresh in-memory build byte-for-byte.
 *
 * Usage: node validate.mjs
 */

import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { lintSvgSource } from "./author/lib/svg.mjs";
import { renderManifests } from "./build_manifest.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, "..", "..");
const CONTENT_DIR = path.join(REPO, "content", "sanctuary");
const ART_DIR = path.join(REPO, "mobile", "src", "sanctuary", "art");
const DRESSING_TS = path.join(REPO, "mobile", "src", "sanctuary", "diorama", "scene", "dressing.ts");

/** Souvenir sprites the app requires (docs/adr/0012, expedition souvenirs). */
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

const ELEMENT_TYPES = ["coarse", "charismatic", "relationship", "surprise", "signature"];

let failures = 0;
const fail = (msg) => {
  failures += 1;
  console.error(`✗ ${msg}`);
};
const ok = (msg) => console.log(`✓ ${msg}`);

async function readJson(p) {
  return JSON.parse(await readFile(p, "utf8"));
}

const assets = await readJson(path.join(HERE, "assets.json"));
const allowlist = new Set((await readJson(path.join(HERE, "placeholders.json"))).allowlist);
const layers = assets.entries.filter((entry) => entry.kind === "layer");
const sprites = assets.entries.filter((entry) => entry.kind === "sprite");
const backdrops = assets.entries.filter((entry) => entry.kind === "backdrop");
const byClass = (spriteClass) => sprites.filter((entry) => entry.spriteClass === spriteClass);

// --- gather content icon keys (souvenirs.json is the souvenir domain) -------

const contentIconKeys = new Set();
const souvenirContentKeys = new Set();
for (const file of await readdir(CONTENT_DIR)) {
  if (!file.endsWith(".json")) continue;
  const into = file === "souvenirs.json" ? souvenirContentKeys : contentIconKeys;
  const walk = (node) => {
    if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === "object") {
      if (typeof node.icon === "string") into.add(node.icon);
      Object.values(node).forEach(walk);
    }
  };
  walk(await readJson(path.join(CONTENT_DIR, file)));
}

// 1a. Element coverage (either sprite or placeholder, never both, no stale).
// The runtime manifests are keyed by entry.name; coverage below checks
// iconKey/sceneryKey. Pin the two together so a divergent entry cannot
// ship a sprite the app can never look up.
for (const entry of byClass("element").concat(byClass("souvenir"))) {
  if (entry.name !== entry.iconKey) {
    fail(`assets.json entry "${entry.name}" has iconKey "${entry.iconKey}" -- name and iconKey must match`);
  }
}
for (const entry of byClass("scenery")) {
  if (entry.name !== entry.sceneryKey) {
    fail(`assets.json entry "${entry.name}" has sceneryKey "${entry.sceneryKey}" -- name and sceneryKey must match`);
  }
}

const elementKeys = new Set(byClass("element").map((entry) => entry.iconKey));
for (const key of contentIconKeys) {
  if (!elementKeys.has(key) && !allowlist.has(key)) {
    fail(`content icon key '${key}' has no sprite and is not in placeholders.json`);
  }
  if (elementKeys.has(key) && allowlist.has(key)) {
    fail(`'${key}' is BOTH a sprite and a placeholder -- remove it from the allowlist`);
  }
}
for (const key of elementKeys) {
  if (!contentIconKeys.has(key)) {
    fail(`element sprite '${key}' has no matching content icon key (stale entry)`);
  }
}

// 1b. Souvenir coverage: the required ids are required regardless of whether
// content/sanctuary/souvenirs.json has landed; once it lands, its icon keys
// must be covered too and nothing may go stale.
const souvenirKeys = new Set(byClass("souvenir").map((entry) => entry.iconKey));
const requiredSouvenirKeys = new Set([
  ...REQUIRED_SOUVENIR_IDS.map((id) => `sanctuary.souvenir.${id}`),
  ...souvenirContentKeys,
]);
for (const key of requiredSouvenirKeys) {
  if (!souvenirKeys.has(key) && !allowlist.has(key)) {
    fail(`souvenir '${key}' has no sprite and is not in placeholders.json`);
  }
  if (souvenirKeys.has(key) && allowlist.has(key)) {
    fail(`'${key}' is BOTH a sprite and a placeholder -- remove it from the allowlist`);
  }
}
for (const key of souvenirKeys) {
  if (!requiredSouvenirKeys.has(key)) {
    fail(`souvenir sprite '${key}' is neither a required id nor in souvenirs.json (stale entry)`);
  }
}

// 1c. Placeholder staleness (all domains).
for (const key of allowlist) {
  if (!contentIconKeys.has(key) && !requiredSouvenirKeys.has(key)) {
    fail(`placeholders.json lists '${key}' which nothing references (stale)`);
  }
}

// 1d. Scenery coverage against DRESSING_RULES (parsed from the TS source so
// the rule table stays the single authority for keys/zones/tiers).
const dressingSource = await readFile(DRESSING_TS, "utf8");
const ruleRe = /\{ key: "([^"]+)", zone: "([^"]+)", count: \d+, tierMin: (\d+)/g;
const rules = new Map();
for (const match of dressingSource.matchAll(ruleRe)) {
  rules.set(match[1], { zone: match[2], tierMin: Number(match[3]) });
}
if (rules.size === 0) {
  fail("could not parse any DRESSING_RULES from dressing.ts -- update the regex in validate.mjs");
}
const sceneryByKey = new Map(byClass("scenery").map((entry) => [entry.sceneryKey, entry]));
for (const [key, rule] of rules) {
  const entry = sceneryByKey.get(key);
  if (!entry) {
    fail(`DRESSING_RULES key '${key}' has no scenery sprite`);
    continue;
  }
  if (entry.zone !== rule.zone) {
    fail(`scenery '${key}' zone '${entry.zone}' != dressing rule zone '${rule.zone}'`);
  }
  if (entry.tierMin !== rule.tierMin) {
    fail(`scenery '${key}' tierMin ${entry.tierMin} != dressing rule tierMin ${rule.tierMin}`);
  }
}
for (const key of sceneryByKey.keys()) {
  if (!rules.has(key)) fail(`scenery sprite '${key}' matches no DRESSING_RULES key (stale entry)`);
}

// 1e. Fallback motifs, one per element type.
const fallbackTypes = new Set(byClass("fallback").map((entry) => entry.elementType));
for (const type of ELEMENT_TYPES) {
  if (!fallbackTypes.has(type)) fail(`missing '${type}' fallback sprite`);
}

// 1f. Backdrop band sets: a zone ships all four scene bands or none (the
// renderer treats a zone as "migrated" only when its full set exists;
// build_manifest.mjs enforces the same rule at emit time).
const SCENE_BANDS = ["far", "mid", "ground", "fore"];
const backdropZoneBands = new Map();
for (const entry of backdrops) {
  if (!SCENE_BANDS.includes(entry.sceneBand)) {
    fail(`backdrop '${entry.name}' has unknown sceneBand '${entry.sceneBand}'`);
    continue;
  }
  const bands = backdropZoneBands.get(entry.zone) ?? new Set();
  if (bands.has(entry.sceneBand)) {
    fail(`backdrop '${entry.name}' duplicates ${entry.zone}/${entry.sceneBand}`);
  }
  bands.add(entry.sceneBand);
  backdropZoneBands.set(entry.zone, bands);
}
for (const [zone, bands] of backdropZoneBands) {
  for (const band of SCENE_BANDS) {
    if (!bands.has(band)) {
      fail(`backdrop set for '${zone}' is missing the '${band}' band (all four or none)`);
    }
  }
}

if (failures === 0) {
  ok(
    `coverage: ${contentIconKeys.size} content icon keys, ${souvenirKeys.size} souvenirs, ` +
      `${rules.size} scenery keys, ${fallbackTypes.size} fallbacks (${allowlist.size} placeholder)`,
  );
}

// 2 + 3 + 4. Files exist, lint clean, within budget.
let totalBytes = 0;
const referenced = new Set();
for (const entry of [...layers, ...sprites, ...backdrops]) {
  referenced.add(entry.out.replace(/\\/g, "/"));
  const filePath = path.join(HERE, entry.out);
  let bytes;
  try {
    bytes = (await stat(filePath)).size;
  } catch {
    fail(`'${entry.name}' svg missing: ${entry.out} -- run the generators`);
    continue;
  }
  totalBytes += bytes;
  const budget = assets.categories[entry.category];
  if (!budget) {
    fail(`'${entry.name}' category '${entry.category}' has no budget in assets.json`);
  } else if (bytes > budget.maxKB * 1024) {
    fail(`'${entry.name}' over budget: ${(bytes / 1024).toFixed(2)} KB (max ${budget.maxKB} KB)`);
  }
  const expectViewBox =
    entry.kind === "layer"
      ? "0 0 512 384"
      : entry.kind === "backdrop"
        ? "0 0 1024 640"
        : "0 0 128 128";
  const errors = lintSvgSource(await readFile(filePath, "utf8"), { expectViewBox });
  for (const error of errors) fail(`${entry.out}: ${error}`);
}

async function* walkSvg(dir) {
  for (const item of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, item.name);
    if (item.isDirectory()) yield* walkSvg(p);
    else yield p;
  }
}
for await (const filePath of walkSvg(path.join(HERE, "svg"))) {
  const rel = path.relative(HERE, filePath).replace(/\\/g, "/");
  if (!referenced.has(rel)) fail(`orphan file '${rel}' has no assets.json entry`);
}

if (totalBytes > assets.totals.svgMaxKB * 1024) {
  fail(`total svg payload ${(totalBytes / 1024).toFixed(1)} KB exceeds ${assets.totals.svgMaxKB} KB`);
} else {
  ok(`svg payload ${(totalBytes / 1024).toFixed(1)} KB across ${referenced.size} files (max ${assets.totals.svgMaxKB} KB)`);
}

// 5. Licenses (OWNED needs provenance; the CC0 mechanism stays for audio).
const sources = (await readJson(path.join(HERE, "sources.json"))).sources;
const sourceIds = new Set();
for (const source of sources) {
  sourceIds.add(source.id);
  if (source.license !== "OWNED" && source.license !== "CC0") {
    fail(`source '${source.id}' license is '${source.license}' -- OWNED or CC0 only`);
  }
  if (source.license === "OWNED" && !source.provenance) {
    fail(`source '${source.id}' is OWNED but has no provenance (recipe path)`);
  }
}
for (const entry of [...layers, ...sprites, ...backdrops]) {
  if (!sourceIds.has(entry.source)) {
    fail(`'${entry.name}' references unknown source '${entry.source}' -- add it to sources.json first`);
  }
}
ok(`licenses: ${sources.length} sources, all OWNED/CC0`);

// 6. Manifest drift.
const { islandLayersTs, spritesTs, backdropsTs } = await renderManifests();
for (const [file, expected] of [
  ["islandLayers.gen.ts", islandLayersTs],
  ["sprites.gen.ts", spritesTs],
  ["backdrops.gen.ts", backdropsTs],
]) {
  let actual = null;
  try {
    actual = await readFile(path.join(ART_DIR, file), "utf8");
  } catch {
    fail(`mobile/src/sanctuary/art/${file} missing -- run build_manifest.mjs`);
    continue;
  }
  if (actual !== expected) {
    fail(`mobile/src/sanctuary/art/${file} is out of date -- run build_manifest.mjs and commit`);
  }
}
if (failures === 0) ok("generated manifests match assets.json + svg/ byte-for-byte");

// --- summary -----------------------------------------------------------------

if (failures > 0) {
  console.error(`\n${failures} validation failure(s)`);
  process.exit(1);
}
console.log("\nAll sanctuary art checks passed.");
