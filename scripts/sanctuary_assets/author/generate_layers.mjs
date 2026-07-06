/**
 * Deterministic island-layer generator (ADR 0012 art strategy).
 *
 * For every `kind: "layer"` entry in assets.json, renders one of the four
 * parallax bands of a zone island from author/recipes/<zone>.json:
 *
 *   back  -- flat far-archipelago silhouette, {{horizon}} @ 25-40%
 *   base  -- the floating island mass: three stacked irregular slabs
 *            darkening downward, a tapering rock underside, root strands,
 *            and a {{glow}} rim-light duplicate of the top slab at (-3,-3)
 *   mid   -- the zone's terrain wash (grass / water / shelf / paving /
 *            cloud-ring / mist) plus seeded canopy blobs
 *   fore  -- high-saturation fringe: tufts, flowers, reeds, per-zone extras
 *
 * All geometry flows through the seeded PRNG (label `layer:<zone>:<band>`),
 * so reruns are byte-identical -- CI diffs the svg/ tree to prove it.
 * Canvas: 512x384, island bottom-center anchored. Colors are exclusively
 * {{palette-slot}} tokens; the builder throws on anything else.
 *
 * Usage: node author/generate_layers.mjs   (from scripts/sanctuary_assets)
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { rngFor } from "./lib/rand.mjs";
import { token } from "./lib/tokens.mjs";
import { blobPath, blobPoints, createDoc, el, fmt, smoothClosed, smoothOpen } from "./lib/svg.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const W = 512;
const H = 384;
const CX = W / 2;

// ---------------------------------------------------------------------------
// back: far-archipelago silhouette
// ---------------------------------------------------------------------------

function paintBack(doc, rng, recipe) {
  const { humps, height, opacity } = recipe.back;
  const baseY = 268;
  const pts = [[-8, baseY]];
  const n = humps * 2;
  for (let i = 0; i <= n; i++) {
    const x = (i / n) * (W + 16) - 8;
    const rise = i % 2 === 1 ? height * (0.6 + rng() * 0.4) : height * (0.12 + rng() * 0.2);
    pts.push([x, baseY - rise]);
  }
  pts.push([W + 8, baseY]);
  const d = `${smoothOpen(pts)}L${fmt(W + 8)} ${fmt(baseY + 30)}L-8 ${fmt(baseY + 30)}Z`;
  doc.add(el("path", { d, fill: token("horizon"), opacity: fmt(opacity) }));
}

// ---------------------------------------------------------------------------
// base: the floating island mass
// ---------------------------------------------------------------------------

function slabPoints(rng, cx, cy, rx, ry, wobble) {
  return blobPoints(rng, cx, cy, rx, ry, { points: 10, wobble, phase: 0.3 });
}

function paintBase(doc, rng, recipe) {
  const { width, topY, surfaceDrop, midDrop, taperDepth, roots, wobble } = recipe.island;
  const rx = width / 2;

  // Three stacked slabs, each with its own 2-stop vertical gradient in the
  // {{earth_mid}} family, darkening downward.
  const topD = smoothClosed(slabPoints(rng, CX, topY + surfaceDrop / 2, rx, surfaceDrop / 2, wobble));
  const midD = smoothClosed(
    slabPoints(rng, CX, topY + surfaceDrop + midDrop / 2 - 4, rx * 0.8, midDrop / 2, wobble * 1.3),
  );
  const lowY = topY + surfaceDrop + midDrop - 8;

  // Tapering rock underside: jagged sides converging on a low tip (clamped
  // to the canvas so deep recipes stay bottom-center anchored).
  const tipX = CX + (rng() * 2 - 1) * width * 0.06;
  const tipY = Math.min(topY + taperDepth, 376);
  const left = [];
  const right = [];
  const steps = 5;
  for (let i = 1; i <= steps; i++) {
    const t = i / (steps + 1);
    const spread = rx * 0.52 * (1 - t) * (0.88 + rng() * 0.24);
    const y = lowY + (tipY - lowY) * t;
    left.push([CX - spread, y + (rng() * 2 - 1) * 4]);
    right.push([CX + spread, y + (rng() * 2 - 1) * 4]);
  }
  const taperPts = [[CX - rx * 0.56, lowY], ...left, [tipX, tipY], ...right.reverse(), [CX + rx * 0.56, lowY]];
  const taperD = smoothClosed(taperPts);

  // Rim light first (peeks out up-left), then the slabs over it.
  doc.add(el("path", { d: topD, fill: token("glow"), opacity: "0.18", transform: "translate(-3 -3)" }));
  doc.add(
    el("path", {
      d: taperD,
      fill: doc.vGradient(lowY - 10, tipY, [[token("earth_deep")], [token("earth_deep"), 0.92]]),
    }),
  );
  doc.add(
    el("path", {
      d: midD,
      fill: doc.vGradient(topY + surfaceDrop - 6, lowY + 8, [[token("earth_mid")], [token("earth_deep")]]),
    }),
  );
  doc.add(
    el("path", {
      d: topD,
      fill: doc.vGradient(topY, topY + surfaceDrop + 6, [[token("earth_mid")], [token("earth_deep")]]),
    }),
  );

  // Root strands trailing off the underside.
  for (let i = 0; i < roots; i++) {
    const sx = CX + (rng() * 2 - 1) * rx * 0.4;
    const sy = lowY + 8 + rng() * (taperDepth - midDrop) * 0.4;
    const len = 26 + rng() * 30;
    const sway = (rng() * 2 - 1) * 18;
    const d =
      `M${fmt(sx)} ${fmt(sy)}` +
      `C${fmt(sx + sway * 0.4)} ${fmt(sy + len * 0.4)} ${fmt(sx + sway)} ${fmt(sy + len * 0.7)} ${fmt(sx + sway * 0.8)} ${fmt(sy + len)}` +
      `C${fmt(sx + sway * 0.9 + 2)} ${fmt(sy + len * 0.7 + 2)} ${fmt(sx + sway * 0.4 + 2.5)} ${fmt(sy + len * 0.4 + 2)} ${fmt(sx + 2.5)} ${fmt(sy + 1)}Z`;
    doc.add(el("path", { d, fill: token("earth_deep"), opacity: "0.85" }));
  }
}

// ---------------------------------------------------------------------------
// mid: terrain wash + canopy blobs
// ---------------------------------------------------------------------------

function canopyBlob(doc, rng, cx, cy, rx, ry, opacity) {
  // Dark under-blob offset down-right, gradient body, glow kiss on top.
  const underD = blobPath(rng, cx + 4, cy + 5, rx, ry, { points: 9, wobble: 0.16 });
  const bodyD = blobPath(rng, cx, cy, rx, ry, { points: 9, wobble: 0.18 });
  const glowD = blobPath(rng, cx - rx * 0.25, cy - ry * 0.4, rx * 0.45, ry * 0.4, { points: 7, wobble: 0.2 });
  doc.add(el("path", { d: underD, fill: token("green_deep"), opacity: fmt(opacity * 0.8) }));
  doc.add(
    el("path", {
      d: bodyD,
      fill: doc.vGradient(cy - ry, cy + ry, [[token("green_mid")], [token("green_deep")]]),
      opacity: fmt(opacity),
    }),
  );
  doc.add(el("path", { d: glowD, fill: token("glow"), opacity: "0.14" }));
}

function surfaceLens(doc, rng, recipe, fill, opacity, shrink = 1) {
  const { width, topY, surfaceDrop, wobble } = recipe.island;
  const rx = (width / 2) * 0.92 * shrink;
  const d = smoothClosed(
    blobPoints(rng, CX, topY + surfaceDrop / 2, rx, (surfaceDrop / 2) * 0.9, {
      points: 10,
      wobble: wobble * 0.8,
      phase: 0.3,
    }),
  );
  doc.add(el("path", { d, fill, ...(opacity !== undefined ? { opacity: fmt(opacity) } : {}) }));
}

function paintMid(doc, rng, recipe) {
  const { terrain, canopy } = recipe.mid;
  const { width, topY, surfaceDrop } = recipe.island;
  const surfY = topY + surfaceDrop / 2;
  const rx = width / 2;

  if (terrain === "grass") {
    surfaceLens(
      doc,
      rng,
      recipe,
      doc.vGradient(topY - 4, topY + surfaceDrop + 8, [[token("green_mid")], [token("green_deep")]]),
    );
  } else if (terrain === "water") {
    surfaceLens(doc, rng, recipe, token("sand"));
    const waterD = blobPath(rng, CX, surfY, rx * 0.62, surfaceDrop * 0.36, { points: 10, wobble: 0.08 });
    doc.add(
      el("path", {
        d: waterD,
        fill: doc.vGradient(surfY - surfaceDrop * 0.4, surfY + surfaceDrop * 0.5, [
          [token("water")],
          [token("earth_deep")],
        ]),
      }),
    );
    for (let i = 0; i < 3; i++) {
      doc.add(
        el("ellipse", {
          cx: fmt(CX + (rng() * 2 - 1) * rx * 0.4),
          cy: fmt(surfY + (rng() * 2 - 1) * surfaceDrop * 0.16),
          rx: fmt(10 + rng() * 14),
          ry: fmt(1.6 + rng() * 1.2),
          fill: token("cloud"),
          opacity: "0.4",
        }),
      );
    }
    for (let i = 0; i < (recipe.mid.lilyPads ?? 0); i++) {
      const px = CX + (rng() * 2 - 1) * rx * 0.42;
      const py = surfY + (rng() * 2 - 1) * surfaceDrop * 0.18;
      doc.add(
        el("ellipse", { cx: fmt(px), cy: fmt(py), rx: fmt(7 + rng() * 4), ry: fmt(3 + rng() * 1.4), fill: token("green_mid"), opacity: "0.92" }),
      );
      if (rng() > 0.5) doc.add(el("circle", { cx: fmt(px + 2), cy: fmt(py - 2), r: "1.6", fill: token("glow"), opacity: "0.9" }));
    }
  } else if (terrain === "shelf") {
    // Exposed strata bands on the shelf front: earth over a sand seam.
    surfaceLens(doc, rng, recipe, doc.vGradient(topY - 4, topY + surfaceDrop + 8, [[token("green_mid")], [token("green_deep")]]), undefined, 0.98);
    const bandY = topY + surfaceDrop * 0.72;
    doc.add(
      el("rect", { x: fmt(CX - rx * 0.7), y: fmt(bandY), width: fmt(rx * 1.4), height: "7", rx: "3.5", fill: token("earth_mid"), opacity: "0.95" }),
    );
    doc.add(
      el("rect", { x: fmt(CX - rx * 0.62), y: fmt(bandY + 7), width: fmt(rx * 1.24), height: "3", rx: "1.5", fill: token("sand"), opacity: "0.9" }),
    );
    doc.add(
      el("rect", { x: fmt(CX - rx * 0.56), y: fmt(bandY + 10), width: fmt(rx * 1.12), height: "5", rx: "2.5", fill: token("earth_deep"), opacity: "0.95" }),
    );
    for (let i = 0; i < 6; i++) {
      doc.add(
        el("circle", {
          cx: fmt(CX + (rng() * 2 - 1) * rx * 0.5),
          cy: fmt(bandY + 4 + rng() * 9),
          r: fmt(1.2 + rng() * 1.6),
          fill: token("earth_deep"),
          opacity: "0.6",
        }),
      );
    }
  } else if (terrain === "paving") {
    surfaceLens(doc, rng, recipe, token("earth_deep"), 0.9);
    const slabs = recipe.mid.slabs ?? 5;
    const slabW = (rx * 1.5) / slabs;
    for (let i = 0; i < slabs; i++) {
      const sx = CX - rx * 0.75 + i * slabW;
      doc.add(
        el("rect", {
          x: fmt(sx + 1.5),
          y: fmt(surfY - 7 + (rng() * 2 - 1) * 2),
          width: fmt(slabW - 3),
          height: fmt(12 + rng() * 3),
          rx: "2.5",
          fill: token("sand"),
          opacity: "0.96",
        }),
      );
      if (i < slabs - 1 && rng() > 0.4) {
        doc.add(
          el("ellipse", { cx: fmt(sx + slabW), cy: fmt(surfY + 5), rx: "2.6", ry: "1.4", fill: token("green_deep"), opacity: "0.85" }),
        );
      }
    }
    // Planter box with greenery.
    const px = CX + rx * 0.42;
    doc.add(el("rect", { x: fmt(px - 13), y: fmt(surfY - 16), width: "26", height: "10", rx: "2", fill: token("bark") }));
    doc.add(el("path", { d: blobPath(rng, px, surfY - 19, 13, 7, { points: 8, wobble: 0.2 }), fill: token("green_mid") }));
  } else if (terrain === "cloud-ring") {
    surfaceLens(doc, rng, recipe, doc.vGradient(topY - 4, topY + surfaceDrop + 6, [[token("green_mid")], [token("green_deep")]]));
    const puffs = recipe.mid.cloudPuffs ?? 6;
    for (let i = 0; i < puffs; i++) {
      const a = (i / puffs) * Math.PI * 2 + rng() * 0.5;
      const px = CX + Math.cos(a) * rx * (0.95 + rng() * 0.25);
      const py = surfY + 10 + Math.sin(a) * surfaceDrop * 0.7 + rng() * 8;
      doc.add(
        el("path", {
          d: blobPath(rng, px, py, 24 + rng() * 14, 9 + rng() * 5, { points: 8, wobble: 0.16 }),
          fill: token("cloud"),
          opacity: fmt(0.75 + rng() * 0.15),
        }),
      );
    }
  } else if (terrain === "mist") {
    surfaceLens(doc, rng, recipe, doc.vGradient(topY - 4, topY + surfaceDrop + 6, [[token("green_deep")], [token("earth_deep")]]));
    for (let i = 0; i < 3; i++) {
      doc.add(
        el("path", {
          d: blobPath(rng, CX + (rng() * 2 - 1) * rx * 0.7, surfY + 4 + i * 7, rx * (0.7 + rng() * 0.4), 6 + rng() * 4, {
            points: 9,
            wobble: 0.1,
          }),
          fill: token("cloud"),
          opacity: fmt(0.28 + rng() * 0.14),
        }),
      );
    }
    for (let i = 0; i < (recipe.mid.glowStalks ?? 0); i++) {
      const sx = CX + (rng() * 2 - 1) * rx * 0.5;
      const hgt = 16 + rng() * 10;
      doc.add(
        el("path", {
          d: `M${fmt(sx - 1.2)} ${fmt(surfY + 2)}C${fmt(sx - 1.6)} ${fmt(surfY - hgt * 0.6)} ${fmt(sx + 1)} ${fmt(surfY - hgt * 0.8)} ${fmt(sx + 0.6)} ${fmt(surfY - hgt)}L${fmt(sx + 2)} ${fmt(surfY - hgt + 1)}C${fmt(sx + 2.6)} ${fmt(surfY - hgt * 0.5)} ${fmt(sx + 1.6)} ${fmt(surfY)} ${fmt(sx + 1.4)} ${fmt(surfY + 2)}Z`,
          fill: token("green_deep"),
        }),
      );
      doc.add(el("circle", { cx: fmt(sx + 0.8), cy: fmt(surfY - hgt - 2), r: fmt(3 + rng() * 1.5), fill: token("glow"), opacity: "0.85" }));
    }
  }

  // Canopy blobs along the island's back edge.
  const count = canopy.count;
  for (let i = 0; i < count; i++) {
    const t = count === 1 ? 0.5 : i / (count - 1);
    const cx = CX - rx * 0.62 + t * rx * 1.24 + (rng() * 2 - 1) * 12;
    const cy = topY - canopy.ry * 0.55 + (rng() * 2 - 1) * 6;
    canopyBlob(doc, rng, cx, cy, canopy.rx * (0.8 + rng() * 0.4), canopy.ry * (0.8 + rng() * 0.4), canopy.opacity);
  }
}

// ---------------------------------------------------------------------------
// fore: high-saturation fringe
// ---------------------------------------------------------------------------

function grassTuft(doc, rng, x, y, size) {
  const blades = 3 + Math.floor(rng() * 3);
  for (let b = 0; b < blades; b++) {
    const lean = (b / (blades - 1) - 0.5) * 2;
    const hgt = size * (0.7 + rng() * 0.6);
    const tipX = x + lean * size * 0.5 + (rng() * 2 - 1) * 2;
    const d =
      `M${fmt(x + lean * 2.4 - 1.4)} ${fmt(y)}` +
      `C${fmt(x + lean * 3)} ${fmt(y - hgt * 0.5)} ${fmt(tipX - lean)} ${fmt(y - hgt * 0.8)} ${fmt(tipX)} ${fmt(y - hgt)}` +
      `C${fmt(tipX + 1)} ${fmt(y - hgt * 0.7)} ${fmt(x + lean * 3.4 + 1.4)} ${fmt(y - hgt * 0.3)} ${fmt(x + lean * 2.4 + 1.4)} ${fmt(y)}Z`;
    doc.add(el("path", { d, fill: token("green_deep") }));
  }
}

function flowerSpray(doc, rng, x, y, accent) {
  const heads = 2 + Math.floor(rng() * 2);
  for (let i = 0; i < heads; i++) {
    const hx = x + (rng() * 2 - 1) * 9;
    const hgt = 10 + rng() * 8;
    doc.add(
      el("path", {
        d: `M${fmt(hx - 0.9)} ${fmt(y)}C${fmt(hx - 1.2)} ${fmt(y - hgt * 0.6)} ${fmt(hx + 0.6)} ${fmt(y - hgt * 0.8)} ${fmt(hx + 0.4)} ${fmt(y - hgt)}L${fmt(hx + 1.6)} ${fmt(y - hgt + 0.6)}C${fmt(hx + 1.8)} ${fmt(y - hgt * 0.5)} ${fmt(hx + 1.1)} ${fmt(y)} ${fmt(hx + 1.1)} ${fmt(y)}Z`,
        fill: token("green_deep"),
      }),
    );
    doc.add(el("circle", { cx: fmt(hx + 0.5), cy: fmt(y - hgt - 1.6), r: fmt(2.4 + rng() * 1.4), fill: token(accent) }));
    doc.add(el("circle", { cx: fmt(hx + 0.5), cy: fmt(y - hgt - 1.6), r: "1", fill: token("glow"), opacity: "0.9" }));
  }
}

function paintFore(doc, rng, recipe) {
  const { tufts, flowers, extras } = recipe.fore;
  const { width, topY, surfaceDrop } = recipe.island;
  const rx = width / 2;
  const frontY = topY + surfaceDrop + 4;

  for (let i = 0; i < tufts; i++) {
    const t = tufts === 1 ? 0.5 : i / (tufts - 1);
    grassTuft(doc, rng, CX - rx * 0.8 + t * rx * 1.6 + (rng() * 2 - 1) * 10, frontY + (rng() * 2 - 1) * 5, 13 + rng() * 6);
  }
  flowers.forEach((accent, i) => {
    flowerSpray(doc, rng, CX - rx * 0.5 + (i + 0.5) * ((rx * 1.0) / flowers.length) + (rng() * 2 - 1) * 8, frontY + 2, accent);
  });

  for (const extra of extras) {
    if (extra === "reeds") {
      for (let i = 0; i < 5; i++) {
        const x = CX + (i - 2) * rx * 0.32 + (rng() * 2 - 1) * 10;
        const hgt = 30 + rng() * 22;
        doc.add(
          el("path", {
            d: `M${fmt(x - 1.1)} ${fmt(frontY)}C${fmt(x - 1.6)} ${fmt(frontY - hgt * 0.6)} ${fmt(x + 0.4)} ${fmt(frontY - hgt * 0.85)} ${fmt(x + 0.6)} ${fmt(frontY - hgt)}L${fmt(x + 2)} ${fmt(frontY - hgt + 0.8)}C${fmt(x + 2.2)} ${fmt(frontY - hgt * 0.5)} ${fmt(x + 1.3)} ${fmt(frontY)} ${fmt(x + 1.3)} ${fmt(frontY)}Z`,
            fill: token("green_deep"),
          }),
        );
        if (i % 2 === 0) {
          doc.add(
            el("rect", { x: fmt(x - 1.6), y: fmt(frontY - hgt - 9), width: "4.4", height: "10", rx: "2.2", fill: token("bark") }),
          );
        }
      }
    } else if (extra === "mushrooms") {
      for (let i = 0; i < 4; i++) {
        const x = CX + (rng() * 2 - 1) * rx * 0.7;
        const y = frontY + 2 + (rng() * 2 - 1) * 4;
        const r = 3 + rng() * 2;
        doc.add(el("rect", { x: fmt(x - 1.1), y: fmt(y - r * 1.1), width: "2.2", height: fmt(r * 1.2), rx: "1.1", fill: token("sand") }));
        doc.add(
          el("path", {
            d: `M${fmt(x - r)} ${fmt(y - r)}C${fmt(x - r)} ${fmt(y - r * 2.1)} ${fmt(x + r)} ${fmt(y - r * 2.1)} ${fmt(x + r)} ${fmt(y - r)}Z`,
            fill: token(i % 2 === 0 ? "accent_warm" : "earth_mid"),
          }),
        );
      }
    } else if (extra === "pebbles") {
      for (let i = 0; i < 6; i++) {
        doc.add(
          el("ellipse", {
            cx: fmt(CX + (rng() * 2 - 1) * rx * 0.8),
            cy: fmt(frontY + 3 + rng() * 4),
            rx: fmt(2.4 + rng() * 2.4),
            ry: fmt(1.6 + rng() * 1.4),
            fill: token("earth_mid"),
            opacity: "0.9",
          }),
        );
      }
    } else if (extra === "planters") {
      for (let i = 0; i < 2; i++) {
        const x = CX + (i === 0 ? -1 : 1) * rx * 0.55;
        doc.add(el("rect", { x: fmt(x - 11), y: fmt(frontY - 8), width: "22", height: "9", rx: "2", fill: token("bark") }));
        doc.add(el("path", { d: blobPath(rng, x, frontY - 11, 11, 6, { points: 8, wobble: 0.2 }), fill: token("green_mid") }));
        doc.add(el("circle", { cx: fmt(x - 4 + rng() * 8), cy: fmt(frontY - 13), r: "1.8", fill: token("accent_warm") }));
      }
    } else if (extra === "clouds") {
      for (const side of [-1, 1]) {
        doc.add(
          el("path", {
            d: blobPath(rng, CX + side * rx * 0.9, frontY + 14, 40 + rng() * 16, 12 + rng() * 5, { points: 8, wobble: 0.14 }),
            fill: token("cloud"),
            opacity: "0.82",
          }),
        );
      }
    } else if (extra === "spores") {
      for (let i = 0; i < 7; i++) {
        doc.add(
          el("circle", {
            cx: fmt(CX + (rng() * 2 - 1) * rx * 1.1),
            cy: fmt(frontY - 8 - rng() * 46),
            r: fmt(1 + rng() * 1.8),
            fill: token("glow"),
            opacity: fmt(0.35 + rng() * 0.3),
          }),
        );
      }
    } else {
      throw new Error(`unknown fore extra '${extra}'`);
    }
  }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const PAINTERS = { back: paintBack, base: paintBase, mid: paintMid, fore: paintFore };

export async function generateLayers() {
  const assets = JSON.parse(await readFile(path.join(ROOT, "assets.json"), "utf8"));
  const recipes = new Map();
  const written = [];

  for (const entry of assets.entries) {
    if (entry.kind !== "layer") continue;
    if (!recipes.has(entry.zone)) {
      recipes.set(
        entry.zone,
        JSON.parse(await readFile(path.join(HERE, "recipes", `${entry.zone}.json`), "utf8")),
      );
    }
    const recipe = recipes.get(entry.zone);
    const paint = PAINTERS[entry.layerBand];
    if (!paint) throw new Error(`entry '${entry.name}': unknown layerBand '${entry.layerBand}'`);

    const doc = createDoc({ width: W, height: H, idPrefix: `${entry.zone}-${entry.layerBand}-` });
    paint(doc, rngFor(`layer:${entry.zone}:${entry.layerBand}`), recipe);

    const outPath = path.join(ROOT, entry.out);
    await mkdir(path.dirname(outPath), { recursive: true });
    await writeFile(outPath, doc.render(), "utf8");
    written.push(entry.out);
  }
  return written;
}

import { pathToFileURL } from "node:url";
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const written = await generateLayers();
  console.log(`layers: ${written.length} svg files regenerated`);
}
