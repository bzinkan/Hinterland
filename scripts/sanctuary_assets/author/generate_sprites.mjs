/**
 * Deterministic sprite generator (ADR 0012 art strategy).
 *
 * For every `kind: "sprite"` entry in assets.json, renders a 128x128
 * bottom-center-anchored sprite from its author/recipes/sprites.json
 * parameters: painterly blob-gradient silhouettes with a darker
 * self-outline, in the same language as the island layers. Feet sit on
 * y=124; fliers hover with a small cast shadow so the anchor still lands
 * on terrain.
 *
 * Sprite classes (assets.json `spriteClass`):
 *   element  -- one per content icon key, shaped to its subject
 *   fallback -- element_type motifs: dome/crystal/ring/trinket/landmark
 *   souvenir -- expedition trophies keyed sanctuary.souvenir.<id>
 *   scenery  -- every DRESSING_RULES key (trees/rocks/flowers/bushes)
 *
 * Seeded per sprite (label `sprite:<name>`): reruns are byte-identical.
 * Colors are exclusively {{palette-slot}} tokens.
 *
 * Usage: node author/generate_sprites.mjs   (from scripts/sanctuary_assets)
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { rngFor } from "./lib/rand.mjs";
import { token } from "./lib/tokens.mjs";
import { blobPath, createDoc, el, fmt } from "./lib/svg.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const S = 128;
const CX = 64;
const GY = 124;

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

function shadow(doc, rx = 20, opacity = 0.16, cy = 121.5) {
  doc.add(el("ellipse", { cx: CX, cy: fmt(cy), rx: fmt(rx), ry: fmt(rx * 0.22), fill: token("earth_deep"), opacity: fmt(opacity) }));
}

/** Blob body with a darker self-outline: outline blob +2px, gradient body. */
function organism(doc, rng, cx, cy, rx, ry, topSlot, botSlot, outlineSlot, opts = {}) {
  const outline = blobPath(rng, cx, cy, rx + 2, ry + 2, { points: opts.points ?? 8, wobble: (opts.wobble ?? 0.1) * 0.8, phase: opts.phase ?? 0 });
  const body = blobPath(rng, cx, cy, rx, ry, { points: opts.points ?? 8, wobble: opts.wobble ?? 0.1, phase: opts.phase ?? 0 });
  doc.add(el("path", { d: outline, fill: token(outlineSlot) }));
  doc.add(el("path", { d: body, fill: doc.vGradient(cy - ry, cy + ry, [[token(topSlot)], [token(botSlot)]]) }));
}

/** Standing donut ring (nonzero winding: outer CW arc + inner CCW arc). */
function ringPath(cx, cy, rOuter, rInner) {
  return (
    `M${fmt(cx + rOuter)} ${fmt(cy)}A${fmt(rOuter)} ${fmt(rOuter)} 0 1 1 ${fmt(cx - rOuter)} ${fmt(cy)}A${fmt(rOuter)} ${fmt(rOuter)} 0 1 1 ${fmt(cx + rOuter)} ${fmt(cy)}Z` +
    `M${fmt(cx + rInner)} ${fmt(cy)}A${fmt(rInner)} ${fmt(rInner)} 0 1 0 ${fmt(cx - rInner)} ${fmt(cy)}A${fmt(rInner)} ${fmt(rInner)} 0 1 0 ${fmt(cx + rInner)} ${fmt(cy)}Z`
  );
}

/** Half-dome cap sitting on baseline y. */
function domePath(cx, y, r) {
  return `M${fmt(cx - r)} ${fmt(y)}A${fmt(r)} ${fmt(r)} 0 0 1 ${fmt(cx + r)} ${fmt(y)}Z`;
}

/** Five-petal flower head. */
function flowerHead(doc, cx, cy, r, accent) {
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2 - Math.PI / 2;
    doc.add(el("circle", { cx: fmt(cx + Math.cos(a) * r), cy: fmt(cy + Math.sin(a) * r), r: fmt(r * 0.78), fill: token(accent) }));
  }
  doc.add(el("circle", { cx: fmt(cx), cy: fmt(cy), r: fmt(r * 0.62), fill: token("glow") }));
}

/** Tapered leaf blade from (x, y) to a tip. */
function bladePath(x, y, tipX, tipY, w) {
  return `M${fmt(x - w)} ${fmt(y)}C${fmt(x - w)} ${fmt(y - (y - tipY) * 0.5)} ${fmt(tipX - w * 0.4)} ${fmt(tipY + (y - tipY) * 0.2)} ${fmt(tipX)} ${fmt(tipY)}C${fmt(tipX + w * 0.4)} ${fmt(tipY + (y - tipY) * 0.25)} ${fmt(x + w)} ${fmt(y - (y - tipY) * 0.45)} ${fmt(x + w)} ${fmt(y)}Z`;
}

function stem(doc, x, y, tipX, tipY, w = 1.4) {
  doc.add(el("path", { d: bladePath(x, y, tipX, tipY, w), fill: token("green_deep") }));
}

// ---------------------------------------------------------------------------
// Element motifs
// ---------------------------------------------------------------------------

function drawFloweringPlant(doc, rng) {
  shadow(doc, 16);
  stem(doc, CX, GY, CX - 2, 62, 1.8);
  doc.add(el("path", { d: bladePath(CX - 1, 104, CX - 22, 86, 2.6), fill: token("green_mid") }));
  doc.add(el("path", { d: bladePath(CX + 1, 94, CX + 20, 74, 2.4), fill: token("green_mid") }));
  flowerHead(doc, CX - 2, 52, 9, "accent_cool");
  doc.add(el("circle", { cx: fmt(CX + 14), cy: "70", r: "3", fill: token("accent_warm"), opacity: "0.9" }));
}

function drawButterfly(doc, rng, params) {
  shadow(doc, 9, 0.12);
  const cy = 66;
  const wing = params.wing;
  const edge = params.monarch ? "earth_deep" : "green_deep";
  for (const side of [-1, 1]) {
    const upper = blobPath(rng, CX + side * 15, cy - 8, 15, 12, { points: 7, wobble: 0.12, phase: side });
    const lower = blobPath(rng, CX + side * 11, cy + 9, 10, 9, { points: 7, wobble: 0.14, phase: side * 2 });
    doc.add(el("path", { d: blobPath(rng, CX + side * 15, cy - 8, 16.5, 13.5, { points: 7, wobble: 0.1, phase: side }), fill: token(edge) }));
    doc.add(el("path", { d: upper, fill: doc.vGradient(cy - 22, cy + 6, [[token(wing)], [token(params.monarch ? "glow" : "water")]]) }));
    doc.add(el("path", { d: blobPath(rng, CX + side * 11, cy + 9, 11.5, 10.5, { points: 7, wobble: 0.12, phase: side * 2 }), fill: token(edge) }));
    doc.add(el("path", { d: lower, fill: token(wing), "fill-opacity": "0.92" }));
    if (params.monarch) {
      doc.add(el("circle", { cx: fmt(CX + side * 22), cy: fmt(cy - 14), r: "1.6", fill: token("cloud") }));
      doc.add(el("circle", { cx: fmt(CX + side * 9), cy: fmt(cy + 14), r: "1.3", fill: token("cloud") }));
    } else {
      doc.add(el("circle", { cx: fmt(CX + side * 15), cy: fmt(cy - 8), r: "2.4", fill: token("glow"), opacity: "0.85" }));
    }
  }
  doc.add(el("ellipse", { cx: CX, cy: fmt(cy), rx: "2.6", ry: "11", fill: token("bark") }));
  doc.add(el("circle", { cx: CX, cy: fmt(cy - 13), r: "3", fill: token("bark") }));
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX + side)} ${fmt(cy - 15)}C${fmt(CX + side * 4)} ${fmt(cy - 22)} ${fmt(CX + side * 7)} ${fmt(cy - 24)} ${fmt(CX + side * 8)} ${fmt(cy - 25)}C${fmt(CX + side * 6.4)} ${fmt(cy - 22.4)} ${fmt(CX + side * 3.4)} ${fmt(cy - 20)} ${fmt(CX + side * 1.8)} ${fmt(cy - 14.6)}Z`, fill: token("bark") }));
  }
}

function drawHummingbird(doc, rng) {
  shadow(doc, 8, 0.12);
  const cy = 70;
  // Swept wing behind.
  doc.add(el("path", { d: blobPath(rng, CX - 4, cy - 20, 7, 16, { points: 7, wobble: 0.16, phase: 0.8 }), fill: token("cloud"), opacity: "0.75" }));
  organism(doc, rng, CX, cy, 13, 10, "green_mid", "green_deep", "green_deep", { wobble: 0.08 });
  doc.add(el("circle", { cx: fmt(CX + 10), cy: fmt(cy - 8), r: "6.5", fill: token("green_mid") }));
  doc.add(el("path", { d: `M${fmt(CX + 15)} ${fmt(cy - 9)}L${fmt(CX + 34)} ${fmt(cy - 6.4)}L${fmt(CX + 15)} ${fmt(cy - 6)}Z`, fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(CX + 12), cy: fmt(cy - 10), r: "1.4", fill: token("earth_deep") }));
  doc.add(el("path", { d: blobPath(rng, CX + 7, cy + 1, 4.5, 3.5, { points: 6, wobble: 0.1 }), fill: token("accent_warm") }));
  doc.add(el("path", { d: `M${fmt(CX - 11)} ${fmt(cy + 2)}L${fmt(CX - 26)} ${fmt(cy + 14)}L${fmt(CX - 18)} ${fmt(cy + 15)}L${fmt(CX - 8)} ${fmt(cy + 7)}Z`, fill: token("green_deep") }));
}

function drawBeetle(doc, rng) {
  shadow(doc, 13);
  const cy = 106;
  for (const side of [-1, 1]) {
    for (let leg = 0; leg < 3; leg++) {
      const lx = CX + side * (8 + leg * 5);
      doc.add(el("path", { d: `M${fmt(CX + side * 6)} ${fmt(cy)}L${fmt(lx + side * 6)} ${fmt(GY - 1)}L${fmt(lx + side * 3.6)} ${fmt(GY)}L${fmt(CX + side * 5)} ${fmt(cy + 3)}Z`, fill: token("earth_deep") }));
    }
  }
  organism(doc, rng, CX, cy, 17, 12, "bark", "earth_deep", "earth_deep", { wobble: 0.06 });
  doc.add(el("path", { d: `M${fmt(CX)} ${fmt(cy - 13)}L${fmt(CX + 1.4)} ${fmt(cy + 11)}L${fmt(CX - 1.4)} ${fmt(cy + 11)}Z`, fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(CX - 7), cy: fmt(cy - 4), r: "2", fill: token("glow"), opacity: "0.8" }));
  doc.add(el("circle", { cx: fmt(CX + 8), cy: fmt(cy + 2), r: "1.6", fill: token("glow"), opacity: "0.8" }));
  doc.add(el("circle", { cx: fmt(CX + 15), cy: fmt(cy - 8), r: "4.4", fill: token("earth_deep") }));
}

function drawPollination(doc, rng) {
  shadow(doc, 15);
  stem(doc, CX - 10, GY, CX - 12, 78, 1.8);
  doc.add(el("path", { d: bladePath(CX - 11, 108, CX - 28, 92, 2.4), fill: token("green_mid") }));
  flowerHead(doc, CX - 12, 68, 8.5, "accent_cool");
  // Bee above-right with a dotted flight arc.
  const bx = CX + 20;
  const by = 46;
  doc.add(el("ellipse", { cx: fmt(bx - 3), cy: fmt(by - 7), rx: "6", ry: "4", fill: token("cloud"), opacity: "0.85" }));
  doc.add(el("ellipse", { cx: fmt(bx + 4), cy: fmt(by - 7.6), rx: "5", ry: "3.4", fill: token("cloud"), opacity: "0.75" }));
  organism(doc, rng, bx, by, 8.5, 6, "glow", "accent_warm", "earth_deep", { wobble: 0.06 });
  doc.add(el("ellipse", { cx: fmt(bx - 2), cy: fmt(by), rx: "1.6", ry: "5.6", fill: token("earth_deep") }));
  doc.add(el("ellipse", { cx: fmt(bx + 3), cy: fmt(by), rx: "1.4", ry: "5", fill: token("earth_deep") }));
  for (let i = 0; i < 3; i++) {
    doc.add(el("circle", { cx: fmt(bx - 12 - i * 7), cy: fmt(by + 10 + i * 5.5), r: "1.3", fill: token("glow"), opacity: fmt(0.7 - i * 0.15) }));
  }
}

function drawFish(doc, rng) {
  const cy = 98;
  doc.add(el("ellipse", { cx: CX, cy: "118", rx: "26", ry: "5", fill: token("water"), opacity: "0.5" }));
  doc.add(el("path", { d: `M${fmt(CX - 16)} ${fmt(cy)}L${fmt(CX - 30)} ${fmt(cy - 11)}L${fmt(CX - 27)} ${fmt(cy)}L${fmt(CX - 30)} ${fmt(cy + 11)}Z`, fill: token("accent_cool") }));
  organism(doc, rng, CX + 2, cy, 20, 12, "accent_cool", "water", "green_deep", { wobble: 0.07, points: 9 });
  doc.add(el("path", { d: blobPath(rng, CX + 2, cy - 2, 6, 8, { points: 6, wobble: 0.12 }), fill: token("water"), opacity: "0.85" }));
  doc.add(el("circle", { cx: fmt(CX + 15), cy: fmt(cy - 4), r: "2.8", fill: token("cloud") }));
  doc.add(el("circle", { cx: fmt(CX + 15.8), cy: fmt(cy - 4), r: "1.4", fill: token("earth_deep") }));
  for (let i = 0; i < 3; i++) {
    doc.add(el("circle", { cx: fmt(CX + 26 + i * 4), cy: fmt(cy - 16 - i * 9), r: fmt(1.4 + i * 0.6), fill: token("cloud"), opacity: "0.7" }));
  }
}

function drawFrog(doc, rng, params) {
  const big = params.bull;
  const rx = big ? 22 : 17;
  const ry = big ? 15 : 12;
  const cy = GY - ry - 2;
  shadow(doc, rx + 4);
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: blobPath(rng, CX + side * (rx - 2), GY - 4, 7, 4, { points: 6, wobble: 0.12 }), fill: token("green_deep") }));
  }
  organism(doc, rng, CX, cy, rx, ry, "green_mid", "green_deep", "green_deep", { wobble: 0.07, points: 9 });
  doc.add(el("path", { d: blobPath(rng, CX, cy + ry * 0.45, rx * 0.6, ry * 0.4, { points: 7, wobble: 0.1 }), fill: token("glow"), opacity: "0.35" }));
  if (big) {
    doc.add(el("circle", { cx: fmt(CX), cy: fmt(cy + ry * 0.62), r: "6.5", fill: token("accent_warm"), opacity: "0.75" }));
  }
  for (const side of [-1, 1]) {
    const ex = CX + side * rx * 0.55;
    const ey = cy - ry + 1;
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(ey), r: big ? "5.4" : "4.6", fill: token("green_mid") }));
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(ey - 1), r: big ? "3" : "2.6", fill: token("cloud") }));
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(ey - 1), r: "1.4", fill: token("earth_deep") }));
  }
}

function drawDamselfly(doc, rng) {
  shadow(doc, 8, 0.12);
  // Reed perch.
  stem(doc, CX - 8, GY, CX - 4, 58, 1.5);
  const bx = CX - 2;
  const by = 56;
  for (const side of [-1, 1]) {
    doc.add(el("ellipse", { cx: fmt(bx + side * 15), cy: fmt(by - 5), rx: "15", ry: "4", fill: token("cloud"), opacity: "0.6", transform: `rotate(${side * -12} ${fmt(bx + side * 15)} ${fmt(by - 5)})` }));
    doc.add(el("ellipse", { cx: fmt(bx + side * 13), cy: fmt(by + 1), rx: "13", ry: "3.4", fill: token("cloud"), opacity: "0.5", transform: `rotate(${side * 8} ${fmt(bx + side * 13)} ${fmt(by + 1)})` }));
  }
  doc.add(el("ellipse", { cx: fmt(bx), cy: fmt(by + 8), rx: "2.2", ry: "14", fill: doc.vGradient(by - 6, by + 22, [[token("accent_cool")], [token("water")]]) }));
  doc.add(el("circle", { cx: fmt(bx), cy: fmt(by - 8), r: "3.6", fill: token("accent_cool") }));
  doc.add(el("circle", { cx: fmt(bx - 2.4), cy: fmt(by - 9), r: "1.8", fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(bx + 2.4), cy: fmt(by - 9), r: "1.8", fill: token("earth_deep") }));
}

function drawFrogInWater(doc, rng) {
  const wy = 102;
  doc.add(el("path", { d: ringPath(CX, wy, 34, 28), fill: token("cloud"), opacity: "0.5", transform: `translate(0 ${fmt(wy)}) scale(1 0.32) translate(0 ${fmt(-wy)})` }));
  doc.add(el("path", { d: ringPath(CX, wy, 23, 18), fill: token("cloud"), opacity: "0.65", transform: `translate(0 ${fmt(wy)}) scale(1 0.32) translate(0 ${fmt(-wy)})` }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(wy), rx: "15", ry: "4.6", fill: token("water") }));
  // Just the frog's eyes and crown above the surface.
  doc.add(el("path", { d: blobPath(rng, CX, wy - 3, 11, 4.5, { points: 8, wobble: 0.08 }), fill: token("green_deep") }));
  for (const side of [-1, 1]) {
    const ex = CX + side * 6.5;
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(wy - 7), r: "4", fill: token("green_mid") }));
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(wy - 8), r: "2.2", fill: token("cloud") }));
    doc.add(el("circle", { cx: fmt(ex), cy: fmt(wy - 8), r: "1.1", fill: token("earth_deep") }));
  }
}

function drawSnail(doc, rng) {
  shadow(doc, 14);
  const cy = 104;
  doc.add(el("path", { d: blobPath(rng, CX - 2, GY - 5, 20, 5.5, { points: 8, wobble: 0.1 }), fill: token("sand") }));
  doc.add(el("circle", { cx: fmt(CX + 16), cy: fmt(GY - 12), r: "5", fill: token("sand") }));
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX + 16 + side * 2)} ${fmt(GY - 16)}C${fmt(CX + 16 + side * 4)} ${fmt(GY - 22)} ${fmt(CX + 16 + side * 5)} ${fmt(GY - 24)} ${fmt(CX + 16 + side * 6)} ${fmt(GY - 25)}C${fmt(CX + 16 + side * 5.6)} ${fmt(GY - 22.6)} ${fmt(CX + 16 + side * 4.4)} ${fmt(GY - 19)} ${fmt(CX + 16 + side * 3.4)} ${fmt(GY - 15)}Z`, fill: token("sand") }));
    doc.add(el("circle", { cx: fmt(CX + 16 + side * 6), cy: fmt(GY - 25), r: "1.2", fill: token("earth_deep") }));
  }
  organism(doc, rng, CX - 6, cy, 15, 14, "bark", "earth_deep", "earth_deep", { wobble: 0.05 });
  doc.add(el("circle", { cx: fmt(CX - 6), cy: fmt(cy), r: "9", fill: token("sand"), opacity: "0.9" }));
  doc.add(el("circle", { cx: fmt(CX - 4), cy: fmt(cy - 2), r: "5", fill: token("earth_mid") }));
  doc.add(el("circle", { cx: fmt(CX - 2.5), cy: fmt(cy - 3.5), r: "2.2", fill: token("earth_deep") }));
}

function soarBird(doc, rng, { span, bodySlot, tail }) {
  const cy = 58;
  for (const side of [-1, 1]) {
    // Wing with fingered primaries.
    const wx = CX + side * span * 0.52;
    doc.add(el("path", { d: `M${fmt(CX)} ${fmt(cy)}C${fmt(CX + side * span * 0.2)} ${fmt(cy - 16)} ${fmt(wx - side * 6)} ${fmt(cy - 18)} ${fmt(wx)} ${fmt(cy - 14)}L${fmt(wx - side * 2)} ${fmt(cy - 9)}L${fmt(wx - side * 7)} ${fmt(cy - 11)}L${fmt(wx - side * 8)} ${fmt(cy - 6)}L${fmt(wx - side * 13)} ${fmt(cy - 8)}C${fmt(CX + side * span * 0.16)} ${fmt(cy - 2)} ${fmt(CX + side * 4)} ${fmt(cy + 3)} ${fmt(CX)} ${fmt(cy + 4)}Z`, fill: token(bodySlot) }));
    doc.add(el("path", { d: `M${fmt(CX + side * 6)} ${fmt(cy - 5)}C${fmt(CX + side * span * 0.24)} ${fmt(cy - 12)} ${fmt(CX + side * span * 0.38)} ${fmt(cy - 13)} ${fmt(CX + side * span * 0.46)} ${fmt(cy - 11.6)}C${fmt(CX + side * span * 0.34)} ${fmt(cy - 9)} ${fmt(CX + side * span * 0.2)} ${fmt(cy - 6)} ${fmt(CX + side * 7)} ${fmt(cy - 3.4)}Z`, fill: token("cloud"), opacity: "0.4" }));
  }
  doc.add(el("ellipse", { cx: CX, cy: fmt(cy), rx: "6", ry: "9", fill: token(bodySlot) }));
  doc.add(el("circle", { cx: CX, cy: fmt(cy - 9), r: "3.6", fill: token(bodySlot) }));
  if (tail) {
    doc.add(el("path", { d: `M${fmt(CX - 6)} ${fmt(cy + 7)}C${fmt(CX - 5)} ${fmt(cy + 15)} ${fmt(CX + 5)} ${fmt(cy + 15)} ${fmt(CX + 6)} ${fmt(cy + 7)}Z`, fill: token("accent_warm") }));
  } else {
    doc.add(el("path", { d: `M${fmt(CX - 3.4)} ${fmt(cy + 7)}L${fmt(CX)} ${fmt(cy + 14)}L${fmt(CX + 3.4)} ${fmt(cy + 7)}Z`, fill: token(bodySlot) }));
  }
  shadow(doc, 10, 0.1);
}

function drawBirdSoar(doc, rng) {
  soarBird(doc, rng, { span: 76, bodySlot: "earth_deep", tail: false });
}

function drawHawkSoar(doc, rng) {
  soarBird(doc, rng, { span: 100, bodySlot: "bark", tail: true });
  doc.add(el("circle", { cx: fmt(CX + 1.4), cy: "48", r: "1", fill: token("glow") }));
}

function drawWorm(doc, rng) {
  doc.add(el("path", { d: blobPath(rng, CX, GY - 4, 26, 5, { points: 9, wobble: 0.12 }), fill: token("earth_mid") }));
  const pts = [];
  for (let i = 0; i <= 5; i++) {
    const t = i / 5;
    pts.push([CX - 24 + t * 48, GY - 12 - Math.sin(t * Math.PI * 2 + 0.6) * 10 - t * 4]);
  }
  for (let i = 0; i < pts.length; i++) {
    const r = 6.4 - Math.abs(i - 2.2) * 0.8;
    doc.add(el("circle", { cx: fmt(pts[i][0]), cy: fmt(pts[i][1]), r: fmt(r + 1.2), fill: token("earth_deep") }));
  }
  for (let i = 0; i < pts.length; i++) {
    const r = 6.4 - Math.abs(i - 2.2) * 0.8;
    doc.add(el("circle", { cx: fmt(pts[i][0]), cy: fmt(pts[i][1]), r: fmt(r), fill: token("accent_warm") }));
  }
  doc.add(el("circle", { cx: fmt(pts[5][0]), cy: fmt(pts[5][1] - 1), r: "1", fill: token("earth_deep") }));
  doc.add(el("path", { d: blobPath(rng, pts[1][0], pts[1][1], 5, 4.4, { points: 6, wobble: 0.08 }), fill: token("glow"), opacity: "0.25" }));
}

function drawFlyAgaric(doc, rng) {
  shadow(doc, 15);
  doc.add(el("path", { d: `M${fmt(CX - 5)} ${fmt(GY)}C${fmt(CX - 4)} ${fmt(GY - 14)} ${fmt(CX - 3.4)} ${fmt(GY - 24)} ${fmt(CX - 3)} ${fmt(GY - 30)}L${fmt(CX + 3)} ${fmt(GY - 30)}C${fmt(CX + 3.4)} ${fmt(GY - 24)} ${fmt(CX + 4)} ${fmt(GY - 14)} ${fmt(CX + 5)} ${fmt(GY)}Z`, fill: doc.vGradient(GY - 30, GY, [[token("cloud")], [token("sand")]]) }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 30), rx: "12", ry: "3", fill: token("sand") }));
  doc.add(el("path", { d: blobPath(rng, CX, GY - 36, 17, 11, { points: 9, wobble: 0.06, phase: 0.5 }), fill: token("earth_deep") }));
  doc.add(el("path", { d: `M${fmt(CX - 16)} ${fmt(GY - 33)}C${fmt(CX - 15)} ${fmt(GY - 52)} ${fmt(CX + 15)} ${fmt(GY - 52)} ${fmt(CX + 16)} ${fmt(GY - 33)}C${fmt(CX + 6)} ${fmt(GY - 29.6)} ${fmt(CX - 6)} ${fmt(GY - 29.6)} ${fmt(CX - 16)} ${fmt(GY - 33)}Z`, fill: doc.vGradient(GY - 52, GY - 30, [[token("accent_warm")], [token("earth_deep")]]) }));
  const spots = [[-9, -40, 1.8], [-2, -45, 2.2], [6, -42, 1.6], [11, -37, 1.4], [-13, -35, 1.3]];
  for (const [dx, dy, r] of spots) {
    doc.add(el("circle", { cx: fmt(CX + dx), cy: fmt(GY + dy), r: fmt(r), fill: token("cloud") }));
  }
}

function mushroom(doc, rng, x, hgt, capR, capSlot) {
  doc.add(el("rect", { x: fmt(x - capR * 0.24), y: fmt(GY - hgt), width: fmt(capR * 0.48), height: fmt(hgt), rx: fmt(capR * 0.24), fill: doc.vGradient(GY - hgt, GY, [[token("cloud")], [token("sand")]]) }));
  doc.add(el("path", { d: blobPath(rng, x, GY - hgt + 1, capR + 1.4, capR * 0.62 + 1.2, { points: 8, wobble: 0.05, phase: 0.4 }), fill: token("earth_deep"), opacity: "0.9" }));
  doc.add(el("path", { d: `M${fmt(x - capR)} ${fmt(GY - hgt)}C${fmt(x - capR * 0.9)} ${fmt(GY - hgt - capR * 1.2)} ${fmt(x + capR * 0.9)} ${fmt(GY - hgt - capR * 1.2)} ${fmt(x + capR)} ${fmt(GY - hgt)}Z`, fill: doc.vGradient(GY - hgt - capR * 1.1, GY - hgt, [[token(capSlot)], [token("earth_mid")]]) }));
  doc.add(el("circle", { cx: fmt(x - capR * 0.4), cy: fmt(GY - hgt - capR * 0.5), r: fmt(capR * 0.14), fill: token("glow"), opacity: "0.7" }));
}

function drawMushroomCluster(doc, rng, params) {
  shadow(doc, 17);
  const caps = params.caps;
  mushroom(doc, rng, CX - 12, 18, 11, caps[0]);
  mushroom(doc, rng, CX + 11, 24, 13, caps[1 % caps.length]);
  mushroom(doc, rng, CX + 1, 11, 8, caps[0]);
}

function drawFungiLog(doc, rng) {
  shadow(doc, 30, 0.16);
  doc.add(el("rect", { x: fmt(CX - 30), y: fmt(GY - 22), width: "60", height: "22", rx: "10", fill: doc.vGradient(GY - 22, GY, [[token("bark")], [token("earth_deep")]]) }));
  doc.add(el("ellipse", { cx: fmt(CX + 30), cy: fmt(GY - 11), rx: "6", ry: "11", fill: token("sand") }));
  doc.add(el("ellipse", { cx: fmt(CX + 30), cy: fmt(GY - 11), rx: "3.4", ry: "6.6", fill: token("earth_mid") }));
  doc.add(el("ellipse", { cx: fmt(CX + 30), cy: fmt(GY - 11), rx: "1.4", ry: "3", fill: token("earth_deep") }));
  // Shelf fungi along the top.
  for (let i = 0; i < 3; i++) {
    const fx = CX - 20 + i * 14;
    const r = 6.5 - i * 0.8;
    doc.add(el("path", { d: domePath(fx, GY - 22 - i * 0.6, r + 1.2), fill: token("earth_deep"), opacity: "0.9" }));
    doc.add(el("path", { d: domePath(fx, GY - 22 - i * 0.6, r), fill: doc.vGradient(GY - 30, GY - 20, [[token(i === 1 ? "accent_warm" : "sand")], [token("earth_mid")]]) }));
  }
  doc.add(el("path", { d: blobPath(rng, CX - 26, GY - 20, 6, 3, { points: 6, wobble: 0.2 }), fill: token("green_mid"), opacity: "0.8" }));
}

function drawPigeon(doc, rng) {
  shadow(doc, 13);
  const cy = 100;
  for (const side of [-1, 1]) {
    doc.add(el("rect", { x: fmt(CX + side * 4 - 1), y: fmt(GY - 7), width: "2", height: "7", rx: "1", fill: token("accent_warm") }));
  }
  organism(doc, rng, CX, cy, 15, 12, "cloud", "earth_mid", "earth_deep", { wobble: 0.06, points: 9 });
  doc.add(el("path", { d: blobPath(rng, CX - 5, cy + 1, 9, 6.5, { points: 7, wobble: 0.1 }), fill: token("earth_mid"), opacity: "0.9" }));
  doc.add(el("path", { d: `M${fmt(CX - 14)} ${fmt(cy + 3)}L${fmt(CX - 25)} ${fmt(cy + 9)}L${fmt(CX - 13)} ${fmt(cy + 9.4)}Z`, fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(CX + 11), cy: fmt(cy - 10), r: "6", fill: token("accent_cool") }));
  doc.add(el("circle", { cx: fmt(CX + 11), cy: fmt(cy - 10), r: "6", fill: token("cloud"), opacity: "0.35" }));
  doc.add(el("circle", { cx: fmt(CX + 13), cy: fmt(cy - 11.4), r: "1.3", fill: token("earth_deep") }));
  doc.add(el("path", { d: `M${fmt(CX + 16.4)} ${fmt(cy - 10)}L${fmt(CX + 21)} ${fmt(cy - 8.6)}L${fmt(CX + 16)} ${fmt(cy - 7.6)}Z`, fill: token("accent_warm") }));
}

function drawEdgeLife(doc, rng) {
  shadow(doc, 26, 0.12);
  doc.add(el("rect", { x: fmt(CX - 30), y: fmt(GY - 12), width: "27", height: "12", rx: "2.4", fill: doc.vGradient(GY - 12, GY, [[token("sand")], [token("earth_mid")]]) }));
  doc.add(el("rect", { x: fmt(CX + 3), y: fmt(GY - 12), width: "27", height: "12", rx: "2.4", fill: doc.vGradient(GY - 12, GY, [[token("sand")], [token("earth_mid")]]) }));
  doc.add(el("path", { d: `M${fmt(CX - 3)} ${fmt(GY)}L${fmt(CX - 1)} ${fmt(GY - 12)}L${fmt(CX + 3)} ${fmt(GY - 12)}L${fmt(CX + 3)} ${fmt(GY)}Z`, fill: token("earth_deep") }));
  stem(doc, CX, GY - 10, CX + 1, GY - 40, 1.6);
  doc.add(el("path", { d: bladePath(CX, GY - 24, CX - 13, GY - 36, 2), fill: token("green_mid") }));
  doc.add(el("path", { d: bladePath(CX + 1, GY - 32, CX + 12, GY - 44, 1.8), fill: token("green_mid") }));
  doc.add(el("circle", { cx: fmt(CX + 1), cy: fmt(GY - 44), r: "3.4", fill: token("glow") }));
  doc.add(el("circle", { cx: fmt(CX + 1), cy: fmt(GY - 44), r: "1.6", fill: token("accent_warm") }));
}

function drawRaccoon(doc, rng) {
  shadow(doc, 17);
  const cy = 98;
  // Ringed tail curling up the side.
  const tail = [[CX + 16, GY - 6], [CX + 26, GY - 14], [CX + 29, GY - 26], [CX + 26, GY - 36]];
  for (let i = 0; i < tail.length; i++) {
    doc.add(el("circle", { cx: fmt(tail[i][0]), cy: fmt(tail[i][1]), r: fmt(7 - i * 0.8), fill: token(i % 2 === 0 ? "earth_deep" : "sand") }));
  }
  organism(doc, rng, CX - 2, cy, 17, 15, "earth_mid", "earth_deep", "earth_deep", { wobble: 0.07, points: 9 });
  const hy = cy - 16;
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX - 2 + side * 5)} ${fmt(hy - 8)}L${fmt(CX - 2 + side * 11)} ${fmt(hy - 15)}L${fmt(CX - 2 + side * 11.6)} ${fmt(hy - 6.4)}Z`, fill: token("earth_deep") }));
  }
  doc.add(el("path", { d: blobPath(rng, CX - 2, hy, 11, 9, { points: 8, wobble: 0.06 }), fill: token("cloud") }));
  doc.add(el("ellipse", { cx: fmt(CX - 2), cy: fmt(hy - 1.6), rx: "10.4", ry: "3.6", fill: token("earth_deep") }));
  for (const side of [-1, 1]) {
    doc.add(el("circle", { cx: fmt(CX - 2 + side * 4.6), cy: fmt(hy - 1.6), r: "1.5", fill: token("cloud") }));
  }
  doc.add(el("circle", { cx: fmt(CX - 2), cy: fmt(hy + 4), r: "1.8", fill: token("earth_deep") }));
}

function drawPerchedBird(doc, rng) {
  shadow(doc, 14, 0.12);
  doc.add(el("path", { d: `M${fmt(CX - 26)} ${fmt(GY - 2)}C${fmt(CX - 10)} ${fmt(GY - 8)} ${fmt(CX + 12)} ${fmt(GY - 8)} ${fmt(CX + 26)} ${fmt(GY - 12)}C${fmt(CX + 12)} ${fmt(GY - 4.6)} ${fmt(CX - 10)} ${fmt(GY - 4.6)} ${fmt(CX - 26)} ${fmt(GY + 1)}Z`, fill: token("bark") }));
  const cy = 98;
  doc.add(el("path", { d: `M${fmt(CX - 8)} ${fmt(cy + 4)}L${fmt(CX - 22)} ${fmt(cy + 14)}L${fmt(CX - 10)} ${fmt(cy + 13)}Z`, fill: token("green_deep") }));
  organism(doc, rng, CX, cy, 12, 10, "accent_cool", "green_deep", "green_deep", { wobble: 0.07 });
  doc.add(el("path", { d: blobPath(rng, CX + 2, cy + 3, 7, 5.5, { points: 7, wobble: 0.1 }), fill: token("glow"), opacity: "0.5" }));
  doc.add(el("circle", { cx: fmt(CX + 8), cy: fmt(cy - 9), r: "5.4", fill: token("accent_cool") }));
  doc.add(el("circle", { cx: fmt(CX + 9.6), cy: fmt(cy - 10.4), r: "1.2", fill: token("earth_deep") }));
  doc.add(el("path", { d: `M${fmt(CX + 13)} ${fmt(cy - 9)}L${fmt(CX + 18)} ${fmt(cy - 7.6)}L${fmt(CX + 12.6)} ${fmt(cy - 6.4)}Z`, fill: token("accent_warm") }));
}

function drawHawkOverTrees(doc, rng) {
  // Canopy below, hawk wheeling above -- the relationship in one frame.
  doc.add(el("path", { d: blobPath(rng, CX - 14, GY - 12, 22, 12, { points: 9, wobble: 0.14 }), fill: doc.vGradient(GY - 26, GY, [[token("green_mid")], [token("green_deep")]]) }));
  doc.add(el("path", { d: blobPath(rng, CX + 16, GY - 9, 17, 10, { points: 8, wobble: 0.14 }), fill: doc.vGradient(GY - 20, GY, [[token("green_mid")], [token("green_deep")]]) }));
  doc.add(el("path", { d: blobPath(rng, CX - 20, GY - 20, 8, 4, { points: 7, wobble: 0.16 }), fill: token("glow"), opacity: "0.16" }));
  const cy = 44;
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX)} ${fmt(cy)}C${fmt(CX + side * 12)} ${fmt(cy - 9)} ${fmt(CX + side * 24)} ${fmt(cy - 10)} ${fmt(CX + side * 30)} ${fmt(cy - 7.6)}L${fmt(CX + side * 26)} ${fmt(cy - 3.4)}C${fmt(CX + side * 16)} ${fmt(cy - 3)} ${fmt(CX + side * 5)} ${fmt(cy + 2)} ${fmt(CX)} ${fmt(cy + 2.6)}Z`, fill: token("bark") }));
  }
  doc.add(el("ellipse", { cx: CX, cy: fmt(cy), rx: "4", ry: "6", fill: token("bark") }));
  doc.add(el("path", { d: `M${fmt(CX - 3.4)} ${fmt(cy + 5)}C${fmt(CX - 3)} ${fmt(cy + 10)} ${fmt(CX + 3)} ${fmt(cy + 10)} ${fmt(CX + 3.4)} ${fmt(cy + 5)}Z`, fill: token("accent_warm") }));
}

function drawSquirrel(doc, rng) {
  shadow(doc, 13);
  const cy = 102;
  // Big S-curve tail.
  doc.add(el("path", { d: `M${fmt(CX - 10)} ${fmt(GY - 4)}C${fmt(CX - 30)} ${fmt(GY - 10)} ${fmt(CX - 30)} ${fmt(GY - 42)} ${fmt(CX - 14)} ${fmt(GY - 46)}C${fmt(CX - 22)} ${fmt(GY - 38)} ${fmt(CX - 20)} ${fmt(GY - 20)} ${fmt(CX - 8)} ${fmt(GY - 12)}Z`, fill: doc.vGradient(GY - 46, GY, [[token("accent_warm")], [token("bark")]]) }));
  organism(doc, rng, CX + 2, cy, 12, 13, "accent_warm", "bark", "bark", { wobble: 0.07 });
  doc.add(el("path", { d: blobPath(rng, CX + 2, cy + 5, 6.5, 6, { points: 7, wobble: 0.08 }), fill: token("cloud"), opacity: "0.6" }));
  doc.add(el("circle", { cx: fmt(CX + 9), cy: fmt(cy - 12), r: "6.4", fill: token("accent_warm") }));
  doc.add(el("path", { d: `M${fmt(CX + 5)} ${fmt(cy - 17)}L${fmt(CX + 4)} ${fmt(cy - 23)}L${fmt(CX + 8.4)} ${fmt(cy - 19)}Z`, fill: token("bark") }));
  doc.add(el("circle", { cx: fmt(CX + 11.4), cy: fmt(cy - 13.4), r: "1.3", fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(CX + 8), cy: fmt(cy + 2), r: "3", fill: token("sand") }));
}

function drawFox(doc, rng) {
  shadow(doc, 16);
  const cy = 100;
  // Tail curled around the sitting body, cloud tip.
  doc.add(el("path", { d: `M${fmt(CX + 8)} ${fmt(GY - 4)}C${fmt(CX + 30)} ${fmt(GY - 2)} ${fmt(CX + 34)} ${fmt(GY - 18)} ${fmt(CX + 24)} ${fmt(GY - 24)}C${fmt(CX + 30)} ${fmt(GY - 14)} ${fmt(CX + 24)} ${fmt(GY - 6)} ${fmt(CX + 8)} ${fmt(GY - 8)}Z`, fill: token("accent_warm") }));
  doc.add(el("circle", { cx: fmt(CX + 26), cy: fmt(GY - 23), r: "4.4", fill: token("cloud") }));
  organism(doc, rng, CX - 2, cy, 14, 16, "accent_warm", "bark", "bark", { wobble: 0.08, points: 9 });
  doc.add(el("path", { d: blobPath(rng, CX - 3, cy + 8, 7, 7, { points: 7, wobble: 0.08 }), fill: token("cloud"), opacity: "0.75" }));
  const hy = cy - 18;
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX - 2 + side * 3.4)} ${fmt(hy - 6)}L${fmt(CX - 2 + side * 9)} ${fmt(hy - 15)}L${fmt(CX - 2 + side * 10)} ${fmt(hy - 4.4)}Z`, fill: token("accent_warm") }));
    doc.add(el("path", { d: `M${fmt(CX - 2 + side * 5.4)} ${fmt(hy - 7)}L${fmt(CX - 2 + side * 8.4)} ${fmt(hy - 12.4)}L${fmt(CX - 2 + side * 9)} ${fmt(hy - 6)}Z`, fill: token("earth_deep") }));
  }
  doc.add(el("path", { d: blobPath(rng, CX - 2, hy, 9.5, 8, { points: 8, wobble: 0.06 }), fill: token("accent_warm") }));
  doc.add(el("path", { d: `M${fmt(CX - 6)} ${fmt(hy + 2)}C${fmt(CX - 4)} ${fmt(hy + 7)} ${fmt(CX)} ${fmt(hy + 7)} ${fmt(CX + 2)} ${fmt(hy + 2)}Z`, fill: token("cloud") }));
  doc.add(el("circle", { cx: fmt(CX - 2), cy: fmt(hy + 5), r: "1.6", fill: token("earth_deep") }));
  for (const side of [-1, 1]) {
    doc.add(el("circle", { cx: fmt(CX - 2 + side * 4), cy: fmt(hy - 1), r: "1.3", fill: token("earth_deep") }));
  }
}

function drawDeer(doc, rng) {
  shadow(doc, 20);
  const bodyY = 88;
  for (const [lx, lean] of [[-12, -1], [-4, 0.4], [6, -0.4], [13, 1]]) {
    doc.add(el("path", { d: `M${fmt(CX + lx - 2)} ${fmt(bodyY + 8)}L${fmt(CX + lx + lean * 3 - 1.4)} ${fmt(GY)}L${fmt(CX + lx + lean * 3 + 1.4)} ${fmt(GY)}L${fmt(CX + lx + 2)} ${fmt(bodyY + 8)}Z`, fill: token("bark") }));
  }
  organism(doc, rng, CX, bodyY, 20, 11, "sand", "earth_mid", "bark", { wobble: 0.07, points: 9 });
  // White tail flag at the rear.
  doc.add(el("path", { d: blobPath(rng, CX - 19, bodyY - 6, 4, 5.5, { points: 6, wobble: 0.12 }), fill: token("cloud") }));
  // Neck + head up.
  doc.add(el("path", { d: `M${fmt(CX + 13)} ${fmt(bodyY - 4)}C${fmt(CX + 17)} ${fmt(bodyY - 14)} ${fmt(CX + 19)} ${fmt(bodyY - 22)} ${fmt(CX + 21)} ${fmt(bodyY - 27)}L${fmt(CX + 27)} ${fmt(bodyY - 25)}C${fmt(CX + 25)} ${fmt(bodyY - 14)} ${fmt(CX + 23)} ${fmt(bodyY - 6)} ${fmt(CX + 22)} ${fmt(bodyY + 2)}Z`, fill: token("earth_mid") }));
  doc.add(el("path", { d: blobPath(rng, CX + 25, bodyY - 28, 6.5, 5, { points: 7, wobble: 0.07 }), fill: token("earth_mid") }));
  doc.add(el("path", { d: `M${fmt(CX + 29)} ${fmt(bodyY - 30)}L${fmt(CX + 35)} ${fmt(bodyY - 29)}L${fmt(CX + 30)} ${fmt(bodyY - 26.4)}Z`, fill: token("sand") }));
  for (const dx of [-2, 3]) {
    doc.add(el("path", { d: `M${fmt(CX + 22 + dx)} ${fmt(bodyY - 32)}L${fmt(CX + 19 + dx)} ${fmt(bodyY - 40)}L${fmt(CX + 24 + dx)} ${fmt(bodyY - 33)}Z`, fill: token("bark") }));
  }
  doc.add(el("circle", { cx: fmt(CX + 27), cy: fmt(bodyY - 29.4), r: "1.2", fill: token("earth_deep") }));
  doc.add(el("circle", { cx: fmt(CX + 31), cy: fmt(bodyY - 28), r: "1.1", fill: token("earth_deep") }));
}

function drawWisp(doc, rng) {
  shadow(doc, 9, 0.1);
  const cy = 66;
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "22", fill: doc.rGradient(CX, cy, 22, [[token("glow"), 0.5], [token("glow"), 0]]) }));
  doc.add(el("path", { d: `M${fmt(CX - 3)} ${fmt(cy + 16)}C${fmt(CX - 12)} ${fmt(cy + 26)} ${fmt(CX + 2)} ${fmt(cy + 32)} ${fmt(CX - 4)} ${fmt(cy + 42)}C${fmt(CX + 6)} ${fmt(cy + 33)} ${fmt(CX - 6)} ${fmt(cy + 27)} ${fmt(CX + 2)} ${fmt(cy + 17)}Z`, fill: token("cloud"), opacity: "0.55" }));
  doc.add(el("path", { d: blobPath(rng, CX, cy, 11, 12.5, { points: 9, wobble: 0.1 }), fill: doc.vGradient(cy - 13, cy + 13, [[token("cloud")], [token("glow")]]) }));
  doc.add(el("circle", { cx: fmt(CX - 3.4), cy: fmt(cy - 2), r: "1.7", fill: token("earth_deep"), opacity: "0.8" }));
  doc.add(el("circle", { cx: fmt(CX + 3.4), cy: fmt(cy - 2), r: "1.7", fill: token("earth_deep"), opacity: "0.8" }));
  const sparks = [[-16, -12, 1.6], [15, -6, 1.3], [10, 16, 1.1]];
  for (const [dx, dy, r] of sparks) {
    doc.add(el("circle", { cx: fmt(CX + dx), cy: fmt(cy + dy), r: fmt(r), fill: token("glow"), opacity: "0.8" }));
  }
}

// ---------------------------------------------------------------------------
// Fallback motifs (dome / crystal / ring / trinket / landmark)
// ---------------------------------------------------------------------------

function drawDome(doc, rng) {
  shadow(doc, 21);
  doc.add(el("path", { d: domePath(CX, GY, 23), fill: token("green_deep") }));
  doc.add(el("path", { d: domePath(CX, GY, 20.5), fill: doc.vGradient(GY - 21, GY, [[token("accent_cool")], [token("green_deep")]]) }));
  doc.add(el("path", { d: `M${fmt(CX - 12)} ${fmt(GY - 12)}C${fmt(CX - 9)} ${fmt(GY - 19)} ${fmt(CX - 2)} ${fmt(GY - 22)} ${fmt(CX + 4)} ${fmt(GY - 21)}C${fmt(CX - 2)} ${fmt(GY - 19)} ${fmt(CX - 8)} ${fmt(GY - 15)} ${fmt(CX - 9.4)} ${fmt(GY - 10)}Z`, fill: token("glow"), opacity: "0.45" }));
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 9), r: "3.4", fill: token("glow"), opacity: "0.8" }));
}

function drawCrystal(doc, rng) {
  shadow(doc, 16);
  doc.add(el("path", { d: `M${fmt(CX - 14)} ${fmt(GY)}L${fmt(CX - 19)} ${fmt(GY - 16)}L${fmt(CX - 10)} ${fmt(GY - 28)}L${fmt(CX - 4)} ${fmt(GY)}Z`, fill: token("accent_cool"), opacity: "0.85" }));
  doc.add(el("path", { d: `M${fmt(CX - 6)} ${fmt(GY)}L${fmt(CX)} ${fmt(GY - 44)}L${fmt(CX + 12)} ${fmt(GY - 30)}L${fmt(CX + 10)} ${fmt(GY)}Z`, fill: doc.vGradient(GY - 44, GY, [[token("accent_cool")], [token("water")]]) }));
  doc.add(el("path", { d: `M${fmt(CX)} ${fmt(GY - 44)}L${fmt(CX + 4)} ${fmt(GY - 30)}L${fmt(CX + 1)} ${fmt(GY)}L${fmt(CX - 3)} ${fmt(GY)}Z`, fill: token("glow"), opacity: "0.5" }));
  doc.add(el("path", { d: `M${fmt(CX + 9)} ${fmt(GY)}L${fmt(CX + 16)} ${fmt(GY - 18)}L${fmt(CX + 21)} ${fmt(GY - 8)}L${fmt(CX + 19)} ${fmt(GY)}Z`, fill: token("water"), opacity: "0.9" }));
}

function drawRing(doc, rng) {
  shadow(doc, 15);
  doc.add(el("path", { d: ringPath(CX, GY - 26, 22, 15), fill: doc.vGradient(GY - 48, GY - 4, [[token("glow")], [token("accent_warm")]]) }));
  doc.add(el("path", { d: ringPath(CX, GY - 26, 15.8, 14.4), fill: token("glow"), opacity: "0.6" }));
  doc.add(el("circle", { cx: fmt(CX + 19), cy: fmt(GY - 42), r: "3", fill: token("accent_cool") }));
  doc.add(el("circle", { cx: fmt(CX - 20), cy: fmt(GY - 12), r: "2.2", fill: token("accent_cool"), opacity: "0.85" }));
}

function drawTrinket(doc, rng) {
  shadow(doc, 13);
  organism(doc, rng, CX, GY - 11, 14, 10.5, "accent_warm", "bark", "bark", { wobble: 0.08 });
  doc.add(el("rect", { x: fmt(CX - 5), y: fmt(GY - 26), width: "10", height: "5", rx: "2.5", fill: token("earth_deep") }));
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 28), r: "2.6", fill: token("glow") }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 11), rx: "5", ry: "6.4", fill: token("glow"), opacity: "0.75" }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 11), rx: "2.4", ry: "3.4", fill: token("accent_cool") }));
}

function drawLandmark(doc, rng) {
  shadow(doc, 17);
  doc.add(el("path", { d: `M${fmt(CX - 11)} ${fmt(GY)}C${fmt(CX - 12)} ${fmt(GY - 34)} ${fmt(CX - 8)} ${fmt(GY - 58)} ${fmt(CX - 2)} ${fmt(GY - 66)}C${fmt(CX + 6)} ${fmt(GY - 58)} ${fmt(CX + 10)} ${fmt(GY - 32)} ${fmt(CX + 10)} ${fmt(GY)}Z`, fill: doc.vGradient(GY - 66, GY, [[token("earth_mid")], [token("earth_deep")]]) }));
  doc.add(el("path", { d: `M${fmt(CX - 9)} ${fmt(GY - 46)}C${fmt(CX - 5)} ${fmt(GY - 50)} ${fmt(CX + 4)} ${fmt(GY - 50)} ${fmt(CX + 8)} ${fmt(GY - 45)}L${fmt(CX + 7.4)} ${fmt(GY - 41)}C${fmt(CX + 3)} ${fmt(GY - 45)} ${fmt(CX - 4)} ${fmt(GY - 45)} ${fmt(CX - 8.4)} ${fmt(GY - 42)}Z`, fill: token("glow"), opacity: "0.85" }));
  doc.add(el("path", { d: blobPath(rng, CX - 14, GY - 3, 6, 4, { points: 7, wobble: 0.16 }), fill: token("earth_mid") }));
  doc.add(el("path", { d: blobPath(rng, CX + 13, GY - 2.4, 5, 3.4, { points: 7, wobble: 0.16 }), fill: token("earth_mid") }));
  doc.add(el("path", { d: blobPath(rng, CX + 8, GY - 5, 5, 2.6, { points: 6, wobble: 0.2 }), fill: token("green_mid"), opacity: "0.9" }));
}

// ---------------------------------------------------------------------------
// Souvenir motifs
// ---------------------------------------------------------------------------

function drawSpecimenJar(doc, rng) {
  shadow(doc, 15);
  doc.add(el("rect", { x: fmt(CX - 14), y: fmt(GY - 34), width: "28", height: "34", rx: "6", fill: token("water"), opacity: "0.35" }));
  doc.add(el("rect", { x: fmt(CX - 14), y: fmt(GY - 34), width: "28", height: "34", rx: "6", fill: token("cloud"), opacity: "0.25" }));
  doc.add(el("rect", { x: fmt(CX - 16), y: fmt(GY - 40), width: "32", height: "7", rx: "3", fill: token("bark") }));
  doc.add(el("rect", { x: fmt(CX - 13), y: fmt(GY - 41.4), width: "26", height: "3", rx: "1.5", fill: token("sand") }));
  doc.add(el("path", { d: bladePath(CX - 4, GY - 4, CX - 9, GY - 20, 1.8), fill: token("green_mid") }));
  doc.add(el("circle", { cx: fmt(CX + 5), cy: fmt(GY - 22), r: "3", fill: token("glow") }));
  doc.add(el("circle", { cx: fmt(CX + 5), cy: fmt(GY - 22), r: "6", fill: doc.rGradient(CX + 5, GY - 22, 6, [[token("glow"), 0.6], [token("glow"), 0]]) }));
  doc.add(el("rect", { x: fmt(CX - 11), y: fmt(GY - 31), width: "3.4", height: "26", rx: "1.7", fill: token("cloud"), opacity: "0.5" }));
}

function drawExpeditionFlag(doc, rng) {
  shadow(doc, 14);
  doc.add(el("path", { d: blobPath(rng, CX, GY - 3, 16, 5, { points: 8, wobble: 0.14 }), fill: token("green_mid") }));
  doc.add(el("rect", { x: fmt(CX - 2), y: fmt(GY - 62), width: "3.4", height: "60", rx: "1.7", fill: doc.vGradient(GY - 62, GY, [[token("sand")], [token("bark")]]) }));
  doc.add(el("circle", { cx: fmt(CX - 0.3), cy: fmt(GY - 63), r: "2.4", fill: token("glow") }));
  doc.add(el("path", { d: `M${fmt(CX + 2)} ${fmt(GY - 60)}C${fmt(CX + 14)} ${fmt(GY - 58)} ${fmt(CX + 22)} ${fmt(GY - 60)} ${fmt(CX + 32)} ${fmt(GY - 53)}L${fmt(CX + 2)} ${fmt(GY - 45)}Z`, fill: doc.vGradient(GY - 60, GY - 45, [[token("accent_warm")], [token("earth_deep")]]) }));
  doc.add(el("circle", { cx: fmt(CX + 10), cy: fmt(GY - 53), r: "2.6", fill: token("glow") }));
}

function drawPressedFlower(doc, rng) {
  shadow(doc, 17);
  doc.add(el("rect", { x: fmt(CX - 21), y: fmt(GY - 46), width: "42", height: "46", rx: "3", fill: doc.vGradient(GY - 46, GY, [[token("bark")], [token("earth_deep")]]) }));
  doc.add(el("rect", { x: fmt(CX - 16), y: fmt(GY - 41), width: "32", height: "36", rx: "2", fill: token("sand") }));
  doc.add(el("rect", { x: fmt(CX - 16), y: fmt(GY - 41), width: "32", height: "36", rx: "2", fill: token("cloud"), opacity: "0.35" }));
  stem(doc, CX, GY - 8, CX - 1, GY - 28, 1.2);
  doc.add(el("path", { d: bladePath(CX, GY - 16, CX - 8, GY - 24, 1.5), fill: token("green_deep"), opacity: "0.85" }));
  flowerHead(doc, CX - 1, GY - 32, 5, "accent_cool");
  for (const [dx, dy] of [[-18, -43], [18, -43], [-18, -3], [18, -3]]) {
    doc.add(el("circle", { cx: fmt(CX + dx), cy: fmt(GY + dy), r: "1.6", fill: token("earth_deep") }));
  }
}

function drawRibbon(doc, rng) {
  shadow(doc, 12);
  for (const side of [-1, 1]) {
    doc.add(el("path", { d: `M${fmt(CX + side * 3)} ${fmt(GY - 34)}L${fmt(CX + side * 12)} ${fmt(GY - 4)}L${fmt(CX + side * 5)} ${fmt(GY - 8)}L${fmt(CX + side * 1)} ${fmt(GY - 26)}Z`, fill: token(side < 0 ? "accent_warm" : "earth_deep") }));
  }
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    doc.add(el("circle", { cx: fmt(CX + Math.cos(a) * 13), cy: fmt(GY - 44 + Math.sin(a) * 13), r: "5.4", fill: token("accent_warm") }));
  }
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 44), r: "10", fill: doc.rGradient(CX, GY - 46, 11, [[token("glow")], [token("accent_warm")]]) }));
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 44), r: "4", fill: token("glow") }));
}

function drawCompassRose(doc, rng) {
  shadow(doc, 15);
  const cy = GY - 22;
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "21", fill: token("bark") }));
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "18", fill: doc.rGradient(CX - 5, cy - 6, 24, [[token("cloud")], [token("sand")]]) }));
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2;
    doc.add(el("circle", { cx: fmt(CX + Math.cos(a) * 14), cy: fmt(cy + Math.sin(a) * 14), r: "1.3", fill: token("earth_deep") }));
  }
  doc.add(el("path", { d: `M${fmt(CX)} ${fmt(cy - 15)}L${fmt(CX + 4)} ${fmt(cy)}L${fmt(CX)} ${fmt(cy + 15)}L${fmt(CX - 4)} ${fmt(cy)}Z`, fill: token("accent_warm") }));
  doc.add(el("path", { d: `M${fmt(CX - 15)} ${fmt(cy)}L${fmt(CX)} ${fmt(cy - 3.4)}L${fmt(CX + 15)} ${fmt(cy)}L${fmt(CX)} ${fmt(cy + 3.4)}Z`, fill: token("accent_cool") }));
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "2.6", fill: token("glow") }));
}

function drawMagnifier(doc, rng) {
  shadow(doc, 15);
  doc.add(el("rect", { x: fmt(CX + 8), y: fmt(GY - 26), width: "7", height: "26", rx: "3.4", fill: doc.vGradient(GY - 26, GY, [[token("bark")], [token("earth_deep")]]), transform: `rotate(-35 ${fmt(CX + 11.5)} ${fmt(GY - 13)})` }));
  const cy = GY - 40;
  const cx = CX - 4;
  doc.add(el("path", { d: ringPath(cx, cy, 19, 14.5), fill: doc.vGradient(cy - 19, cy + 19, [[token("sand")], [token("bark")]]) }));
  doc.add(el("circle", { cx: fmt(cx), cy: fmt(cy), r: "14.5", fill: token("water"), opacity: "0.3" }));
  doc.add(el("path", { d: bladePath(cx + 1, cy + 9, cx - 4, cy - 6, 2.2), fill: token("green_mid") }));
  doc.add(el("path", { d: `M${fmt(cx - 10)} ${fmt(cy - 4)}C${fmt(cx - 8)} ${fmt(cy - 9)} ${fmt(cx - 4)} ${fmt(cy - 11)} ${fmt(cx + 1)} ${fmt(cy - 11)}C${fmt(cx - 3)} ${fmt(cy - 9)} ${fmt(cx - 6)} ${fmt(cy - 7)} ${fmt(cx - 7.4)} ${fmt(cy - 3)}Z`, fill: token("cloud"), opacity: "0.75" }));
}

function drawPollinatorBadge(doc, rng) {
  shadow(doc, 13);
  doc.add(el("path", { d: `M${fmt(CX - 7)} ${fmt(GY - 22)}L${fmt(CX - 12)} ${fmt(GY)}L${fmt(CX)} ${fmt(GY - 7)}L${fmt(CX + 12)} ${fmt(GY)}L${fmt(CX + 7)} ${fmt(GY - 22)}Z`, fill: token("accent_cool") }));
  const cy = GY - 36;
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "20", fill: token("bark") }));
  doc.add(el("circle", { cx: CX, cy: fmt(cy), r: "17", fill: doc.rGradient(CX - 5, cy - 7, 24, [[token("glow")], [token("accent_warm")]]) }));
  doc.add(el("ellipse", { cx: fmt(CX - 5), cy: fmt(cy - 4), rx: "5.4", ry: "3.6", fill: token("cloud"), opacity: "0.85", transform: `rotate(-24 ${fmt(CX - 5)} ${fmt(cy - 4)})` }));
  doc.add(el("ellipse", { cx: fmt(CX + 4), cy: fmt(cy - 5), rx: "4.6", ry: "3", fill: token("cloud"), opacity: "0.75", transform: `rotate(18 ${fmt(CX + 4)} ${fmt(cy - 5)})` }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(cy + 2), rx: "7", ry: "5", fill: token("glow") }));
  doc.add(el("ellipse", { cx: fmt(CX - 2), cy: fmt(cy + 2), rx: "1.4", ry: "4.6", fill: token("earth_deep") }));
  doc.add(el("ellipse", { cx: fmt(CX + 2.6), cy: fmt(cy + 2), rx: "1.2", ry: "4.2", fill: token("earth_deep") }));
}

function drawStreetlampBloom(doc, rng) {
  shadow(doc, 13);
  doc.add(el("path", { d: blobPath(rng, CX, GY - 2, 13, 4, { points: 7, wobble: 0.14 }), fill: token("earth_mid") }));
  doc.add(el("rect", { x: fmt(CX - 2.4), y: fmt(GY - 78), width: "4.8", height: "78", rx: "2.4", fill: doc.vGradient(GY - 78, GY, [[token("earth_mid")], [token("earth_deep")]]) }));
  doc.add(el("path", { d: `M${fmt(CX - 7)} ${fmt(GY - 78)}C${fmt(CX - 6)} ${fmt(GY - 86)} ${fmt(CX + 6)} ${fmt(GY - 86)} ${fmt(CX + 7)} ${fmt(GY - 78)}Z`, fill: token("earth_deep") }));
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 76), r: "5.4", fill: token("glow") }));
  doc.add(el("circle", { cx: CX, cy: fmt(GY - 76), r: "12", fill: doc.rGradient(CX, GY - 76, 12, [[token("glow"), 0.5], [token("glow"), 0]]) }));
  // Vine spiraling up, blooming where the light falls.
  const vine = [];
  for (let i = 0; i <= 8; i++) {
    const t = i / 8;
    vine.push([CX + Math.sin(t * Math.PI * 3.2) * (7 - t * 2), GY - 6 - t * 62]);
  }
  for (let i = 0; i < vine.length - 1; i++) {
    doc.add(el("path", { d: `M${fmt(vine[i][0] - 1.2)} ${fmt(vine[i][1])}L${fmt(vine[i + 1][0] - 1)} ${fmt(vine[i + 1][1])}L${fmt(vine[i + 1][0] + 1)} ${fmt(vine[i + 1][1])}L${fmt(vine[i][0] + 1.2)} ${fmt(vine[i][1])}Z`, fill: token("green_deep") }));
  }
  for (const i of [2, 4, 6]) {
    doc.add(el("path", { d: bladePath(vine[i][0], vine[i][1], vine[i][0] + (i % 4 === 0 ? 8 : -8), vine[i][1] - 5, 1.4), fill: token("green_mid") }));
  }
  doc.add(el("circle", { cx: fmt(vine[7][0] - 3), cy: fmt(vine[7][1]), r: "2.6", fill: token("accent_warm") }));
  doc.add(el("circle", { cx: fmt(vine[8][0] + 2), cy: fmt(vine[8][1] + 3), r: "2.2", fill: token("accent_warm") }));
}

function drawCensusClipboard(doc, rng) {
  shadow(doc, 15);
  doc.add(el("rect", { x: fmt(CX - 18), y: fmt(GY - 50), width: "36", height: "50", rx: "3.4", fill: doc.vGradient(GY - 50, GY, [[token("bark")], [token("earth_deep")]]) }));
  doc.add(el("rect", { x: fmt(CX - 14), y: fmt(GY - 44), width: "28", height: "40", rx: "1.5", fill: token("sand") }));
  doc.add(el("rect", { x: fmt(CX - 14), y: fmt(GY - 44), width: "28", height: "40", rx: "1.5", fill: token("cloud"), opacity: "0.4" }));
  doc.add(el("rect", { x: fmt(CX - 7), y: fmt(GY - 54), width: "14", height: "7", rx: "2.4", fill: token("earth_deep") }));
  for (let row = 0; row < 4; row++) {
    const ry = GY - 37 + row * 8.5;
    doc.add(el("circle", { cx: fmt(CX - 9), cy: fmt(ry), r: "2.2", fill: token(row < 3 ? "green_mid" : "cloud") }));
    doc.add(el("rect", { x: fmt(CX - 4), y: fmt(ry - 1.4), width: fmt(15 - row * 1.5), height: "2.8", rx: "1.4", fill: token("earth_mid"), opacity: "0.7" }));
  }
}

function drawCairn(doc, rng) {
  shadow(doc, 17);
  const stones = [
    { cy: GY - 7, rx: 19, ry: 7.5 },
    { cy: GY - 18, rx: 14, ry: 6 },
    { cy: GY - 27.4, rx: 10, ry: 5 },
    { cy: GY - 35.4, rx: 6.4, ry: 3.8 },
  ];
  for (const stone of stones) {
    doc.add(el("path", { d: blobPath(rng, CX + (rng() * 2 - 1) * 2.4, stone.cy, stone.rx + 1.6, stone.ry + 1.4, { points: 8, wobble: 0.07 }), fill: token("earth_deep") }));
    doc.add(el("path", { d: blobPath(rng, CX + (rng() * 2 - 1) * 2, stone.cy, stone.rx, stone.ry, { points: 8, wobble: 0.09 }), fill: doc.vGradient(stone.cy - stone.ry, stone.cy + stone.ry, [[token("earth_mid")], [token("earth_deep")]]) }));
  }
  doc.add(el("path", { d: blobPath(rng, CX - 12, GY - 14, 4.4, 2.6, { points: 6, wobble: 0.2 }), fill: token("green_mid"), opacity: "0.9" }));
  doc.add(el("circle", { cx: fmt(CX + 2), cy: fmt(GY - 41), r: "1.8", fill: token("glow"), opacity: "0.85" }));
}

// ---------------------------------------------------------------------------
// Scenery motifs
// ---------------------------------------------------------------------------

function trunk(doc, x, topY, baseW) {
  doc.add(el("path", { d: `M${fmt(x - baseW)} ${fmt(GY)}C${fmt(x - baseW * 0.7)} ${fmt(GY - (GY - topY) * 0.5)} ${fmt(x - baseW * 0.42)} ${fmt(topY + 8)} ${fmt(x - baseW * 0.34)} ${fmt(topY)}L${fmt(x + baseW * 0.34)} ${fmt(topY)}C${fmt(x + baseW * 0.42)} ${fmt(topY + 8)} ${fmt(x + baseW * 0.7)} ${fmt(GY - (GY - topY) * 0.5)} ${fmt(x + baseW)} ${fmt(GY)}Z`, fill: doc.vGradient(topY, GY, [[token("bark")], [token("earth_deep")]]) }));
}

function canopy(doc, rng, cx, cy, rx, ry) {
  doc.add(el("path", { d: blobPath(rng, cx + 2.4, cy + 3, rx, ry, { points: 9, wobble: 0.14 }), fill: token("green_deep") }));
  doc.add(el("path", { d: blobPath(rng, cx, cy, rx, ry, { points: 9, wobble: 0.15 }), fill: doc.vGradient(cy - ry, cy + ry, [[token("green_mid")], [token("green_deep")]]) }));
  doc.add(el("path", { d: blobPath(rng, cx - rx * 0.3, cy - ry * 0.42, rx * 0.42, ry * 0.36, { points: 7, wobble: 0.18 }), fill: token("glow"), opacity: "0.16" }));
}

function drawTree(doc, rng, params) {
  const variant = params.variant;
  if (variant === "thin") {
    shadow(doc, 12);
    trunk(doc, CX, 46, 5);
    canopy(doc, rng, CX, 40, 15, 22);
    canopy(doc, rng, CX + 2, 20, 10, 12);
  } else if (variant === "oak") {
    shadow(doc, 22);
    trunk(doc, CX, 62, 9);
    canopy(doc, rng, CX - 16, 58, 20, 16);
    canopy(doc, rng, CX + 15, 56, 21, 17);
    canopy(doc, rng, CX, 36, 24, 18);
  } else if (variant === "detailed") {
    shadow(doc, 20);
    trunk(doc, CX, 58, 8);
    doc.add(el("path", { d: `M${fmt(CX)} ${fmt(GY - 30)}C${fmt(CX + 7)} ${fmt(GY - 42)} ${fmt(CX + 12)} ${fmt(GY - 50)} ${fmt(CX + 16)} ${fmt(GY - 56)}L${fmt(CX + 18)} ${fmt(GY - 52)}C${fmt(CX + 13)} ${fmt(GY - 46)} ${fmt(CX + 7)} ${fmt(GY - 36)} ${fmt(CX + 3)} ${fmt(GY - 26)}Z`, fill: token("bark") }));
    canopy(doc, rng, CX - 12, 62, 16, 13);
    canopy(doc, rng, CX + 16, 58, 15, 12);
    canopy(doc, rng, CX - 2, 40, 21, 16);
    canopy(doc, rng, CX + 4, 24, 13, 9);
    doc.add(el("circle", { cx: fmt(CX - 8), cy: "34", r: "1.8", fill: token("accent_warm"), opacity: "0.9" }));
    doc.add(el("circle", { cx: fmt(CX + 12), cy: "48", r: "1.6", fill: token("accent_warm"), opacity: "0.9" }));
  } else {
    shadow(doc, 18);
    trunk(doc, CX, 56, 7);
    canopy(doc, rng, CX - 8, 52, 17, 14);
    canopy(doc, rng, CX + 8, 38, 19, 16);
  }
}

function drawPine(doc, rng, params) {
  const tall = params.variant === "tall";
  const tiers = tall ? 4 : 3;
  const baseW = tall ? 20 : 26;
  const topY = tall ? 18 : 40;
  shadow(doc, baseW * 0.8);
  trunk(doc, CX, GY - 16, tall ? 5 : 6);
  for (let i = tiers - 1; i >= 0; i--) {
    const t = i / (tiers - 1);
    const w = baseW * (0.42 + t * 0.58);
    const y = topY + (GY - 22 - topY) * t;
    const h = (GY - 20 - topY) / tiers + 10;
    doc.add(el("path", { d: `M${fmt(CX - w - 1.4)} ${fmt(y + h)}C${fmt(CX - w * 0.4)} ${fmt(y + h - 3)} ${fmt(CX - w * 0.2)} ${fmt(y + 2)} ${fmt(CX)} ${fmt(y - 1.4)}C${fmt(CX + w * 0.2)} ${fmt(y + 2)} ${fmt(CX + w * 0.4)} ${fmt(y + h - 3)} ${fmt(CX + w + 1.4)} ${fmt(y + h)}Z`, fill: token("green_deep") }));
    doc.add(el("path", { d: `M${fmt(CX - w)} ${fmt(y + h - 1.4)}C${fmt(CX - w * 0.4)} ${fmt(y + h - 4)} ${fmt(CX - w * 0.2)} ${fmt(y + 2)} ${fmt(CX)} ${fmt(y)}C${fmt(CX + w * 0.2)} ${fmt(y + 2)} ${fmt(CX + w * 0.4)} ${fmt(y + h - 4)} ${fmt(CX + w)} ${fmt(y + h - 1.4)}Z`, fill: doc.vGradient(y, y + h, [[token("green_mid")], [token("green_deep")]]) }));
  }
  doc.add(el("path", { d: blobPath(rng, CX - baseW * 0.34, topY + 16, 5, 3.4, { points: 6, wobble: 0.2 }), fill: token("glow"), opacity: "0.18" }));
}

function drawStump(doc, rng) {
  shadow(doc, 16);
  doc.add(el("path", { d: `M${fmt(CX - 13)} ${fmt(GY - 22)}C${fmt(CX - 14)} ${fmt(GY - 6)} ${fmt(CX - 16)} ${fmt(GY - 3)} ${fmt(CX - 19)} ${fmt(GY)}L${fmt(CX + 19)} ${fmt(GY)}C${fmt(CX + 16)} ${fmt(GY - 3)} ${fmt(CX + 14)} ${fmt(GY - 6)} ${fmt(CX + 13)} ${fmt(GY - 22)}Z`, fill: doc.vGradient(GY - 22, GY, [[token("bark")], [token("earth_deep")]]) }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 22), rx: "13", ry: "5.4", fill: token("sand") }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 22), rx: "8.4", ry: "3.4", fill: token("earth_mid"), opacity: "0.55" }));
  doc.add(el("ellipse", { cx: CX, cy: fmt(GY - 22), rx: "4", ry: "1.6", fill: token("earth_deep"), opacity: "0.6" }));
  doc.add(el("path", { d: blobPath(rng, CX + 11, GY - 25, 4, 2.2, { points: 6, wobble: 0.2 }), fill: token("green_mid"), opacity: "0.9" }));
}

function drawBush(doc, rng, params) {
  const lobes = params.lobes;
  shadow(doc, lobes === 3 ? 22 : 17);
  const spread = lobes === 3 ? 16 : 10;
  for (let i = 0; i < lobes; i++) {
    const t = lobes === 1 ? 0 : i / (lobes - 1) - 0.5;
    const cx = CX + t * spread * 2;
    const ry = 12 + (i === Math.floor(lobes / 2) ? 5 : 0);
    canopy(doc, rng, cx, GY - ry + 1, 14, ry);
  }
  if (params.berries) {
    for (let i = 0; i < 5; i++) {
      doc.add(el("circle", { cx: fmt(CX + (rng() * 2 - 1) * 18), cy: fmt(GY - 8 - rng() * 14), r: "1.7", fill: token("accent_cool") }));
    }
  }
}

function drawFlowerCluster(doc, rng, params) {
  shadow(doc, 14, 0.14);
  const accent = params.accent;
  const stems = [
    { x: CX - 11, hgt: 26, r: 5 },
    { x: CX + 1, hgt: 36, r: 6.4 },
    { x: CX + 12, hgt: 22, r: 4.4 },
  ];
  for (const s of stems) {
    stem(doc, s.x, GY, s.x + (rng() * 2 - 1) * 4, GY - s.hgt, 1.3);
  }
  doc.add(el("path", { d: bladePath(CX - 6, GY, CX - 18, GY - 14, 2.2), fill: token("green_mid") }));
  doc.add(el("path", { d: bladePath(CX + 7, GY, CX + 18, GY - 12, 2), fill: token("green_mid") }));
  for (const s of stems) {
    flowerHead(doc, s.x + (rng() * 2 - 1) * 2, GY - s.hgt - s.r * 0.8, s.r, accent);
  }
}

function drawRock(doc, rng, params) {
  const { rx, ry } = params;
  shadow(doc, rx + 3, 0.18);
  const cy = GY - ry + 2;
  doc.add(el("path", { d: blobPath(rng, CX, cy, rx + 2, ry + 2, { points: 9, wobble: 0.08, phase: 0.6 }), fill: token("earth_deep") }));
  doc.add(el("path", { d: blobPath(rng, CX, cy, rx, ry, { points: 9, wobble: 0.11, phase: 0.6 }), fill: doc.vGradient(cy - ry, cy + ry, [[token("earth_mid")], [token("earth_deep")]]) }));
  doc.add(el("path", { d: blobPath(rng, CX - rx * 0.32, cy - ry * 0.45, rx * 0.4, ry * 0.3, { points: 7, wobble: 0.16 }), fill: token("glow"), opacity: "0.2" }));
  if (params.mossy) {
    doc.add(el("path", { d: blobPath(rng, CX + rx * 0.4, cy - ry * 0.55, rx * 0.34, ry * 0.24, { points: 7, wobble: 0.2 }), fill: token("green_mid"), opacity: "0.85" }));
  }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const MOTIFS = {
  "flowering-plant": drawFloweringPlant,
  butterfly: drawButterfly,
  hummingbird: drawHummingbird,
  beetle: drawBeetle,
  pollination: drawPollination,
  fish: drawFish,
  frog: drawFrog,
  damselfly: drawDamselfly,
  "frog-in-water": drawFrogInWater,
  snail: drawSnail,
  "bird-soar": drawBirdSoar,
  "hawk-soar": drawHawkSoar,
  worm: drawWorm,
  "fly-agaric": drawFlyAgaric,
  "mushroom-cluster": drawMushroomCluster,
  "fungi-log": drawFungiLog,
  pigeon: drawPigeon,
  "edge-life": drawEdgeLife,
  raccoon: drawRaccoon,
  "perched-bird": drawPerchedBird,
  "hawk-over-trees": drawHawkOverTrees,
  squirrel: drawSquirrel,
  fox: drawFox,
  deer: drawDeer,
  wisp: drawWisp,
  dome: drawDome,
  crystal: drawCrystal,
  ring: drawRing,
  trinket: drawTrinket,
  landmark: drawLandmark,
  "specimen-jar": drawSpecimenJar,
  "expedition-flag": drawExpeditionFlag,
  "pressed-flower": drawPressedFlower,
  ribbon: drawRibbon,
  "compass-rose": drawCompassRose,
  magnifier: drawMagnifier,
  "pollinator-badge": drawPollinatorBadge,
  "streetlamp-bloom": drawStreetlampBloom,
  "census-clipboard": drawCensusClipboard,
  cairn: drawCairn,
  tree: drawTree,
  pine: drawPine,
  stump: drawStump,
  bush: drawBush,
  "flower-cluster": drawFlowerCluster,
  rock: drawRock,
};

export async function generateSprites() {
  const assets = JSON.parse(await readFile(path.join(ROOT, "assets.json"), "utf8"));
  const { sprites: recipes } = JSON.parse(
    await readFile(path.join(HERE, "recipes", "sprites.json"), "utf8"),
  );

  const spriteEntries = assets.entries.filter((entry) => entry.kind === "sprite");
  for (const entry of spriteEntries) {
    if (!recipes[entry.name]) {
      throw new Error(`assets.json sprite '${entry.name}' has no recipe in sprites.json`);
    }
  }
  const entryNames = new Set(spriteEntries.map((entry) => entry.name));
  for (const name of Object.keys(recipes)) {
    if (!entryNames.has(name)) {
      throw new Error(`sprites.json recipe '${name}' has no assets.json entry (stale recipe)`);
    }
  }

  const written = [];
  for (const entry of spriteEntries) {
    const recipe = recipes[entry.name];
    const draw = MOTIFS[recipe.motif];
    if (!draw) throw new Error(`sprite '${entry.name}': unknown motif '${recipe.motif}'`);

    const idPrefix = `${entry.name.replace(/[^A-Za-z0-9]+/g, "-")}-`;
    const doc = createDoc({ width: S, height: S, idPrefix });
    draw(doc, rngFor(`sprite:${entry.name}`), recipe);

    const outPath = path.join(ROOT, entry.out);
    await mkdir(path.dirname(outPath), { recursive: true });
    await writeFile(outPath, doc.render(), "utf8");
    written.push(entry.out);
  }
  return written;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const written = await generateSprites();
  console.log(`sprites: ${written.length} svg files regenerated`);
}
