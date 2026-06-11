/**
 * Sanctuary asset normalizer: raw pack/authored models -> app-ready GLBs.
 *
 * Per entry in assets.json:
 *   ingest GLB/GLTF (from .cache/ or sources_authored/)
 *   -> dedup/flatten/join/weld           (draw-call control)
 *   -> simplify to category tri budget   (build-time meshoptimizer; no runtime cost)
 *   -> strip ALL textures                (flat low-poly look; palette-only colors)
 *   -> apply paletteSlots recolors       (material name -> palette/base.json slot)
 *   -> snap remaining material colors to the nearest palette slot
 *   -> scale/origin normalization        (1 unit = 1 m, origin at base, Y-up)
 *   -> animation whitelist + resample
 *   -> quantize (KHR_mesh_quantization)  (NO Draco/meshopt: Hermes has no WASM)
 *   -> prune -> write GLB -> report.json entry
 *
 * Usage:
 *   node normalize.mjs                # all entries
 *   node normalize.mjs --only NAME    # one entry
 *
 * Generated dev models (source: generated-dev) are skipped -- they are
 * produced by make_test_model.mjs and already normalized.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import {
  dedup,
  flatten,
  join,
  prune,
  quantize,
  resample,
  simplify,
  weld,
} from "@gltf-transform/functions";
import { MeshoptSimplifier } from "meshoptimizer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT_ROOT = path.join(HERE, "..", "..", "mobile", "assets", "sanctuary", "models");
const REPORT_PATH = path.join(HERE, "report.json");

const args = process.argv.slice(2);
const onlyIdx = args.indexOf("--only");
const only = onlyIdx >= 0 ? args[onlyIdx + 1] : null;

const assetsConfig = JSON.parse(await readFile(path.join(HERE, "assets.json"), "utf8"));
const palette = JSON.parse(
  await readFile(path.join(HERE, "palette", "base.json"), "utf8"),
).slots;

// ---------------------------------------------------------------------------
// Color helpers (sRGB hex <-> linear factors, as glTF baseColorFactor is linear)
// ---------------------------------------------------------------------------

function hexToLinearRgb(hex) {
  const v = hex.replace("#", "");
  const srgb = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255);
  return srgb.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
}

function linearToSrgbChannel(c) {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;
}

// Match in sRGB space with a hue-aware weight: pure-linear euclidean
// distance maps bright greens onto pale blues (the leafsGreen->sky bug).
// Authored paletteSlots in assets.json always win over this heuristic.
function nearestSlot(linearRgb) {
  const c = linearRgb.map(linearToSrgbChannel);
  let best = null;
  let bestDist = Infinity;
  for (const [name, hex] of Object.entries(palette)) {
    const v = hex.replace("#", "");
    const p = [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16) / 255);
    // Weighted: penalize channel-ORDER mismatches (hue flips) heavily.
    const d =
      (p[0] - c[0]) ** 2 +
      (p[1] - c[1]) ** 2 +
      (p[2] - c[2]) ** 2 +
      (Math.sign(c[1] - c[2]) !== Math.sign(p[1] - p[2]) ? 0.25 : 0);
    if (d < bestDist) {
      bestDist = d;
      best = name;
    }
  }
  return best;
}

/** Strip every texture reference; flat factors only. */
function stripTextures(document) {
  for (const material of document.getRoot().listMaterials()) {
    material
      .setBaseColorTexture(null)
      .setMetallicRoughnessTexture(null)
      .setNormalTexture(null)
      .setOcclusionTexture(null)
      .setEmissiveTexture(null)
      .setMetallicFactor(0)
      .setRoughnessFactor(1);
  }
  for (const texture of document.getRoot().listTextures()) {
    texture.dispose();
  }
}

/** Apply authored recolors, then snap every material to its nearest slot. */
function applyPalette(document, paletteSlots) {
  const slotsUsed = {};
  for (const material of document.getRoot().listMaterials()) {
    const authored = paletteSlots?.[material.getName()];
    const slot = authored ?? nearestSlot(material.getBaseColorFactor().slice(0, 3));
    const rgb = hexToLinearRgb(palette[slot]);
    material.setBaseColorFactor([rgb[0], rgb[1], rgb[2], 1]);
    slotsUsed[material.getName() || "(unnamed)"] = slot;
  }
  return slotsUsed;
}

/** Keep only whitelisted animation clips (exact name match after trim). */
function whitelistAnimations(document, keep) {
  const keepSet = new Set(keep ?? []);
  for (const animation of document.getRoot().listAnimations()) {
    if (!keepSet.has(animation.getName())) {
      animation.dispose();
    }
  }
}

/** Scale so max height == target (per category), origin at base center, on the scene root nodes. */
function normalizeTransform(document, targetHeight) {
  const scene = document.getRoot().getDefaultScene() ?? document.getRoot().listScenes()[0];
  if (!scene) return;
  // Compute world bbox from accessor min/max of all primitives (approximate:
  // ignores node transforms on rigged models, which packs keep at identity).
  let min = [Infinity, Infinity, Infinity];
  let max = [-Infinity, -Infinity, -Infinity];
  for (const mesh of document.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      const pos = prim.getAttribute("POSITION");
      if (!pos) continue;
      const pMin = pos.getMin([]);
      const pMax = pos.getMax([]);
      for (let i = 0; i < 3; i++) {
        min[i] = Math.min(min[i], pMin[i]);
        max[i] = Math.max(max[i], pMax[i]);
      }
    }
  }
  if (!Number.isFinite(min[1]) || !Number.isFinite(max[1])) return;
  const height = max[1] - min[1] || 1;
  const scale = targetHeight ? targetHeight / height : 1;
  const cx = (min[0] + max[0]) / 2;
  const cz = (min[2] + max[2]) / 2;
  // Wrap existing roots in a normalizer node: scale + translate base to origin.
  const wrapper = document.createNode("__normalized__").setScale([scale, scale, scale]);
  wrapper.setTranslation([-cx * scale, -min[1] * scale, -cz * scale]);
  for (const child of scene.listChildren()) {
    scene.removeChild(child);
    wrapper.addChild(child);
  }
  scene.addChild(wrapper);
}

function countTris(document) {
  let tris = 0;
  for (const mesh of document.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      const indices = prim.getIndices();
      const pos = prim.getAttribute("POSITION");
      tris += Math.floor((indices ? indices.getCount() : pos ? pos.getCount() : 0) / 3);
    }
  }
  return tris;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
await MeshoptSimplifier.ready;

const report = [];
let failed = 0;

for (const entry of assetsConfig.entries) {
  if (only && entry.name !== only) continue;
  if (entry.source === "generated-dev") {
    report.push({ name: entry.name, out: entry.out, skipped: "generated" });
    continue;
  }
  const category = assetsConfig.categories[entry.category];
  if (!category) {
    console.error(`✗ ${entry.name}: unknown category '${entry.category}'`);
    failed += 1;
    continue;
  }
  const rawPath = path.join(HERE, entry.raw);
  try {
    const document = await io.read(rawPath);

    await document.transform(dedup(), flatten(), join(), weld());
    const trisBefore = countTris(document);
    if (trisBefore > category.maxTris) {
      await document.transform(
        simplify({
          simplifier: MeshoptSimplifier,
          ratio: category.maxTris / trisBefore,
          error: 0.001,
        }),
      );
    }
    stripTextures(document);
    const slotsUsed = applyPalette(document, entry.paletteSlots);
    whitelistAnimations(document, entry.animations);
    normalizeTransform(document, entry.targetHeight ?? null);
    await document.transform(resample(), quantize(), prune());

    const outPath = path.join(OUT_ROOT, entry.out);
    await mkdir(path.dirname(outPath), { recursive: true });
    await io.write(outPath, document);

    const glb = await readFile(outPath);
    const kb = Math.round(glb.byteLength / 1024);
    const tris = countTris(document);
    const ok = kb <= category.maxKB && tris <= category.maxTris;
    report.push({
      name: entry.name,
      out: entry.out,
      kb,
      tris,
      budget: category,
      withinBudget: ok,
      paletteSlots: slotsUsed,
      animations: document.getRoot().listAnimations().map((a) => a.getName()),
    });
    if (!ok) failed += 1;
    console.log(`${ok ? "✓" : "✗ OVER BUDGET"} ${entry.name}: ${kb} KB, ${tris} tris`);
  } catch (err) {
    failed += 1;
    report.push({ name: entry.name, out: entry.out, error: String(err) });
    console.error(`✗ ${entry.name}: ${err}`);
  }
}

await writeFile(REPORT_PATH, JSON.stringify(report, null, 2) + "\n");
console.log(`report -> ${REPORT_PATH}`);
if (failed > 0) {
  console.error(`${failed} entr${failed === 1 ? "y" : "ies"} failed`);
  process.exit(1);
}
