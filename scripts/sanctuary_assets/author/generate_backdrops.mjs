/**
 * Deterministic full-scene backdrop generator (ADR 0012 addendum: the
 * biome-scene composition pivot). For every `kind: "backdrop"` entry in
 * assets.json, renders one depth band of a zone's full-bleed 2.5D scene
 * from author/recipes/backdrops.json:
 *
 *   far    -- distant rolling ridge silhouettes dissolving into horizon
 *             haze, with a soft treeline on the nearer ridge
 *   mid    -- big readable hill bands with canopy clusters and the first
 *             wildflower drifts, hazed at the base
 *   ground -- the playable ground plane: wobbled crest line, painterly
 *             gradient grass, a winding pale path, flower drift clusters,
 *             grass tufts (sprites stand on this band at runtime)
 *   fore   -- near framing accents only: corner grass clumps, oversized
 *             blades and flower heads along the bottom edge (this band
 *             sways and outruns the camera; everything above stays clear)
 *
 * Art direction: WoW-meadow vocabulary x Nintendo softness -- big shapes,
 * seeded silhouette wobble (never jelly-bean ellipses), 2-stop gradients
 * for form, alpha haze for depth. All geometry flows through the seeded
 * PRNG (label `backdrop:<zone>:<band>`) so reruns are byte-identical; all
 * color is {{palette-slot}} tokens.
 *
 * Canvas: 1024x640. The renderer bottom-anchors each band at its own
 * screen fraction (mobile/src/sanctuary/diorama/sceneLayout.ts), so bands
 * paint their subject low on the canvas and leave the top transparent.
 *
 * Usage: node author/generate_backdrops.mjs   (from scripts/sanctuary_assets)
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { rngFor } from "./lib/rand.mjs";
import { token } from "./lib/tokens.mjs";
import { blobPath, createDoc, el, fmt, smoothOpen } from "./lib/svg.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const W = 1024;
const H = 640;

// ---------------------------------------------------------------------------
// Shared painterly vocabulary
// ---------------------------------------------------------------------------

/** Wobbled ridge line from x=-16 to x=W+16, filled to the canvas bottom. */
function ridgeFill(rng, topY, humps, humpHeight) {
  const pts = [[-16, topY + humpHeight * 0.35]];
  const n = humps * 2;
  for (let i = 0; i <= n; i++) {
    const x = (i / n) * (W + 32) - 16;
    const rise =
      i % 2 === 1
        ? humpHeight * (0.62 + rng() * 0.38)
        : humpHeight * (0.08 + rng() * 0.22);
    pts.push([x, topY + humpHeight * 0.35 - rise + (rng() * 2 - 1) * 6]);
  }
  pts.push([W + 16, topY + humpHeight * 0.35]);
  return `${smoothOpen(pts)}L${fmt(W + 16)} ${fmt(H + 8)}L-16 ${fmt(H + 8)}Z`;
}

/** Soft haze wash: transparent at hazeTop, peaking toward the bottom. */
function hazeWash(doc, hazeTop, opacity) {
  doc.add(
    el("rect", {
      x: "-16",
      y: fmt(hazeTop),
      width: fmt(W + 32),
      height: fmt(H - hazeTop + 8),
      fill: doc.vGradient(hazeTop, H, [
        [token("horizon"), 0],
        [token("horizon"), opacity],
      ]),
    }),
  );
}

/**
 * Canopy stand: one shadow base, then 2-4 overlapping lumps of varying
 * height, then one glow kiss. Overlapping masses read as a distant tree
 * GROUP; a single blob read as a jelly-bean (taste pass i1).
 */
function canopyCluster(doc, rng, cx, cy, rx, ry, opacity) {
  doc.add(
    el("path", {
      d: blobPath(rng, cx + 6, cy + 8, rx * 1.05, ry * 0.9, {
        points: 9,
        wobble: 0.17,
      }),
      fill: token("green_deep"),
      opacity: fmt(Math.min(1, opacity * 0.95)),
    }),
  );
  const lumps = 2 + Math.floor(rng() * 2);
  for (let i = 0; i < lumps; i++) {
    const t = lumps === 1 ? 0 : i / (lumps - 1) - 0.5;
    const lx = cx + t * rx * 1.1 + (rng() * 2 - 1) * 6;
    const ly = cy + (rng() * 2 - 1) * ry * 0.25 - (i % 2) * ry * 0.35;
    const lrx = rx * (0.42 + rng() * 0.3);
    const lry = ry * (0.6 + rng() * 0.45);
    doc.add(
      el("path", {
        d: blobPath(rng, lx, ly, lrx, lry, { points: 8, wobble: 0.21 }),
        fill: doc.vGradient(ly - lry, ly + lry, [
          [token("green_mid")],
          [token("green_deep")],
        ]),
        opacity: fmt(opacity),
      }),
    );
  }
  doc.add(
    el("path", {
      d: blobPath(rng, cx - rx * 0.25, cy - ry * 0.5, rx * 0.4, ry * 0.38, {
        points: 7,
        wobble: 0.2,
      }),
      fill: token("glow"),
      opacity: "0.13",
    }),
  );
}

/** A loose drift of wildflower specks around (cx, cy). */
function flowerDrift(doc, rng, cx, cy, spreadX, spreadY, count, opacity) {
  const accents = ["accent_warm", "accent_cool", "glow"];
  for (let i = 0; i < count; i++) {
    const fx = cx + (rng() * 2 - 1) * spreadX;
    const fy = cy + (rng() * 2 - 1) * spreadY;
    doc.add(
      el("circle", {
        cx: fmt(fx),
        cy: fmt(fy),
        r: fmt(1.6 + rng() * 2.4),
        fill: token(accents[Math.floor(rng() * accents.length)]),
        opacity: fmt(opacity * (0.7 + rng() * 0.3)),
      }),
    );
  }
}

/** One leaning grass blade with a closed two-edge body. */
function grassBlade(doc, rng, x, y, height, lean, fill, opacity) {
  const tipX = x + lean * height * 0.45 + (rng() * 2 - 1) * 4;
  const w = 3 + rng() * 3;
  const d =
    `M${fmt(x - w / 2)} ${fmt(y)}` +
    `C${fmt(x - w / 2 + lean * 6)} ${fmt(y - height * 0.5)} ${fmt(
      tipX - lean * 8,
    )} ${fmt(y - height * 0.82)} ${fmt(tipX)} ${fmt(y - height)}` +
    `C${fmt(tipX + 2)} ${fmt(y - height * 0.72)} ${fmt(
      x + w / 2 + lean * 7,
    )} ${fmt(y - height * 0.34)} ${fmt(x + w / 2)} ${fmt(y)}Z`;
  doc.add(
    el("path", {
      d,
      fill,
      ...(opacity !== undefined ? { opacity: fmt(opacity) } : {}),
    }),
  );
}

// ---------------------------------------------------------------------------
// far: distant ridges + treeline dissolving into haze
// ---------------------------------------------------------------------------

function paintFar(doc, rng, recipe) {
  const { ridgeTops, humps, humpHeight, treeline, hazeTop, hazeOpacity } =
    recipe;

  // Slow cloud wisps hanging above the ridges. Band-locked, so they
  // drift at far parallax -- reads as distance, not weather.
  if (recipe.clouds) {
    for (let i = 0; i < recipe.clouds.count; i++) {
      const cx = 80 + rng() * (W - 160);
      const cy = 70 + rng() * 130;
      const rx = 60 + rng() * 70;
      const o = recipe.clouds.opacity * (0.75 + rng() * 0.4);
      // Nintendo cloud: flat wide base, two puffy domes stacked on top.
      doc.add(
        el("ellipse", {
          cx: fmt(cx),
          cy: fmt(cy),
          rx: fmt(rx),
          ry: fmt(13 + rng() * 6),
          fill: token("cloud"),
          opacity: fmt(o),
        }),
      );
      doc.add(
        el("ellipse", {
          cx: fmt(cx - rx * 0.3),
          cy: fmt(cy - 12 - rng() * 5),
          rx: fmt(rx * 0.42),
          ry: fmt(15 + rng() * 7),
          fill: token("cloud"),
          opacity: fmt(o * 0.95),
        }),
      );
      doc.add(
        el("ellipse", {
          cx: fmt(cx + rx * 0.28),
          cy: fmt(cy - 9 - rng() * 4),
          rx: fmt(rx * 0.34),
          ry: fmt(11 + rng() * 6),
          fill: token("cloud"),
          opacity: fmt(o * 0.9),
        }),
      );
    }
  }

  // Farthest ridge: barely more than horizon-colored air.
  doc.add(
    el("path", {
      d: ridgeFill(rng, ridgeTops[0], humps[0], humpHeight[0]),
      fill: doc.vGradient(ridgeTops[0] - humpHeight[0], H, [
        [token("green_mid")],
        [token("horizon")],
      ]),
      opacity: "0.5",
    }),
  );

  // Nearer ridge, a touch deeper.
  const ridge2 = ridgeFill(rng, ridgeTops[1], humps[1], humpHeight[1]);
  doc.add(
    el("path", {
      d: ridge2,
      fill: doc.vGradient(ridgeTops[1] - humpHeight[1], H, [
        [token("green_mid")],
        [token("green_deep")],
      ]),
      opacity: "0.62",
    }),
  );

  // Soft treeline riding the second ridge: clustered STANDS with gaps
  // between them, not an even picket row (taste pass i1 -- the uniform
  // run read as bubble wrap). Each stand is 2-4 lumps of varied width,
  // height, and opacity around a jittered group center.
  const stands = treeline.groups;
  for (let g = 0; g < stands; g++) {
    const gx = ((g + 0.5) / stands) * (W + 24) - 12 + (rng() * 2 - 1) * 34;
    const lumps = 2 + Math.floor(rng() * 3);
    const standH = treeline.height * (0.7 + rng() * 0.7);
    for (let i = 0; i < lumps; i++) {
      const x = gx + (i - (lumps - 1) / 2) * (18 + rng() * 8);
      // Hug the ridge crest: bottoms sink into the ridge fill, tops
      // break the skyline. Wider than tall -- distant broadleaf mass.
      const y = ridgeTops[1] - humpHeight[1] * 0.12 + (rng() * 2 - 1) * 5;
      const rx = 16 + rng() * 12;
      const ry = standH * (0.55 + rng() * 0.35);
      doc.add(
        el("path", {
          d: blobPath(rng, x, y, Math.max(rx, ry * 1.15), ry, {
            points: 7,
            wobble: 0.24,
          }),
          fill: token("green_deep"),
          opacity: fmt(treeline.opacity * (1.1 + rng() * 0.35)),
        }),
      );
    }
  }

  hazeWash(doc, hazeTop, hazeOpacity);
}

// ---------------------------------------------------------------------------
// mid: big hill bands + canopy clusters + first flower drifts
// ---------------------------------------------------------------------------

function paintMid(doc, rng, recipe) {
  const {
    hillTops,
    humps,
    humpHeight,
    canopyClusters,
    canopyRx,
    canopyRy,
    flowerDrifts,
    hazeTop,
    hazeOpacity,
  } = recipe;

  // Two rolling hill bands, form from a 2-stop gradient each.
  doc.add(
    el("path", {
      d: ridgeFill(rng, hillTops[0], humps[0], humpHeight[0]),
      fill: doc.vGradient(hillTops[0] - humpHeight[0], H, [
        [token("green_mid")],
        [token("green_deep")],
      ]),
      opacity: "0.85",
    }),
  );
  const hillB = ridgeFill(rng, hillTops[1], humps[1], humpHeight[1]);
  doc.add(
    el("path", {
      d: hillB,
      fill: doc.vGradient(hillTops[1] - humpHeight[1], H + 40, [
        [token("green_mid")],
        [token("green_deep")],
      ]),
    }),
  );

  // Canopy stands riding the nearer hill's CREST, tops breaking the
  // crest line so they silhouette against the far band instead of
  // dissolving green-on-green mid-slope (taste pass i3).
  for (let i = 0; i < canopyClusters; i++) {
    const t = canopyClusters === 1 ? 0.5 : i / (canopyClusters - 1);
    const cx = t * (W - 80) + 40 + (rng() * 2 - 1) * 30;
    const cy = hillTops[1] - humpHeight[1] * (0.8 + rng() * 0.3);
    canopyCluster(
      doc,
      rng,
      cx,
      cy,
      canopyRx * (0.75 + rng() * 0.55),
      canopyRy * (0.85 + rng() * 0.55),
      0.95,
    );
  }

  // Wildflower drifts on the hillside below the crest.
  for (let i = 0; i < flowerDrifts; i++) {
    flowerDrift(
      doc,
      rng,
      80 + rng() * (W - 160),
      hillTops[1] + 40 + rng() * 120,
      90 + rng() * 60,
      18 + rng() * 14,
      8 + Math.floor(rng() * 5),
      0.55,
    );
  }

  hazeWash(doc, hazeTop, hazeOpacity);
}

// ---------------------------------------------------------------------------
// ground: the playable plane -- crest, gradient grass, path, drifts, tufts
// ---------------------------------------------------------------------------

function paintGround(doc, rng, recipe) {
  const { crestY, crestWobble, path: pathSpec, mottles, flowerClusters, tufts } =
    recipe;

  // Wobbled crest line, filled to the bottom with painterly grass.
  const pts = [];
  const steps = 12;
  for (let i = 0; i <= steps; i++) {
    const x = (i / steps) * (W + 32) - 16;
    pts.push([x, crestY + (rng() * 2 - 1) * crestWobble]);
  }
  const groundD = `${smoothOpen(pts)}L${fmt(W + 16)} ${fmt(H + 8)}L-16 ${fmt(
    H + 8,
  )}Z`;
  doc.add(
    el("path", {
      d: groundD,
      fill: doc.vGradient(crestY, H, [
        [token("green_mid")],
        [token("green_deep")],
      ]),
    }),
  );

  // Sun kiss along the crest so the meadow reads lit, not flat.
  doc.add(
    el("rect", {
      x: "-16",
      y: fmt(crestY - 6),
      width: fmt(W + 32),
      height: "70",
      fill: doc.vGradient(crestY - 6, crestY + 64, [
        [token("glow"), 0.2],
        [token("glow"), 0],
      ]),
    }),
  );

  // Winding pale path: two smoothed edges from a narrow crest entry to a
  // wide bottom mouth, with a soft glow highlight down its middle.
  const { topX, topWidth, bottomX, bottomWidth } = pathSpec;
  const bends = 4;
  const leftPts = [];
  const rightPts = [];
  for (let i = 0; i <= bends; i++) {
    const t = i / bends;
    const y = crestY + 8 + t * (H - crestY + 8);
    const sway = Math.sin(t * Math.PI * 1.5) * 90 * (0.6 + rng() * 0.6);
    const cx = topX + (bottomX - topX) * t + sway * t;
    const half = (topWidth + (bottomWidth - topWidth) * t * t) / 2;
    leftPts.push([cx - half, y]);
    rightPts.push([cx + half, y]);
  }
  const pathD = `${smoothOpen(leftPts)}L${fmt(rightPts[rightPts.length - 1][0])} ${fmt(
    rightPts[rightPts.length - 1][1],
  )}${smoothOpen([...rightPts].reverse()).replace(/^M/, "L")}Z`;
  doc.add(el("path", { d: pathD, fill: token("sand"), opacity: "0.85" }));
  const midPts = leftPts.map((p, i) => [
    (p[0] + rightPts[i][0]) / 2,
    p[1],
  ]);
  doc.add(
    el("path", {
      d: `${smoothOpen(midPts)}L${fmt(midPts[midPts.length - 1][0] + 3)} ${fmt(
        H + 8,
      )}L${fmt(midPts[0][0] + 2)} ${fmt(midPts[0][1] + 2)}Z`,
      fill: token("glow"),
      opacity: "0.12",
    }),
  );

  // Painterly mottling: soft deep-green blobs breaking up the gradient.
  for (let i = 0; i < mottles; i++) {
    doc.add(
      el("path", {
        d: blobPath(
          rng,
          40 + rng() * (W - 80),
          crestY + 70 + rng() * (H - crestY - 110),
          70 + rng() * 90,
          16 + rng() * 14,
          { points: 9, wobble: 0.18 },
        ),
        fill: token("green_deep"),
        opacity: fmt(0.1 + rng() * 0.1),
      }),
    );
  }

  // Wildflower drift clusters: denser than mid, with visible stems.
  for (let i = 0; i < flowerClusters; i++) {
    const cx = 60 + rng() * (W - 120);
    const cy = crestY + 90 + rng() * (H - crestY - 140);
    for (let s = 0; s < 3; s++) {
      const sx = cx + (rng() * 2 - 1) * 40;
      const sh = 10 + rng() * 12;
      doc.add(
        el("path", {
          d: `M${fmt(sx - 1)} ${fmt(cy + 8)}C${fmt(sx - 1.4)} ${fmt(
            cy + 8 - sh * 0.6,
          )} ${fmt(sx + 0.6)} ${fmt(cy + 8 - sh * 0.85)} ${fmt(sx + 0.5)} ${fmt(
            cy + 8 - sh,
          )}L${fmt(sx + 1.8)} ${fmt(cy + 8 - sh + 0.7)}C${fmt(sx + 2)} ${fmt(
            cy + 8 - sh * 0.5,
          )} ${fmt(sx + 1.2)} ${fmt(cy + 8)} ${fmt(sx + 1.2)} ${fmt(cy + 8)}Z`,
          fill: token("green_deep"),
          opacity: "0.8",
        }),
      );
    }
    flowerDrift(
      doc,
      rng,
      cx,
      cy,
      55 + rng() * 35,
      14 + rng() * 10,
      9 + Math.floor(rng() * 5),
      0.9,
    );
  }

  // Grass tufts scattered across the plane.
  for (let i = 0; i < tufts; i++) {
    const x = 30 + rng() * (W - 60);
    const y = crestY + 60 + rng() * (H - crestY - 80);
    const blades = 3 + Math.floor(rng() * 3);
    for (let b = 0; b < blades; b++) {
      const lean = (b / Math.max(blades - 1, 1) - 0.5) * 2;
      grassBlade(
        doc,
        rng,
        x + lean * 5,
        y,
        14 + rng() * 12,
        lean,
        token("green_deep"),
        0.75,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// fore: near framing accents only (this band sways and outruns the camera)
// ---------------------------------------------------------------------------

function paintFore(doc, rng, recipe) {
  const { baseY, cornerClumps, edgeBlades, flowers, vignetteTop } = recipe;

  // Soft dark base vignette anchoring the frame.
  doc.add(
    el("rect", {
      x: "-16",
      y: fmt(vignetteTop),
      width: fmt(W + 32),
      height: fmt(H - vignetteTop + 8),
      fill: doc.vGradient(vignetteTop, H, [
        [token("green_deep"), 0],
        [token("green_deep"), 0.28],
      ]),
    }),
  );

  // Corner clumps: big confident blades fanning from each corner anchor.
  for (const clump of cornerClumps) {
    for (let b = 0; b < clump.blades; b++) {
      const lean = (b / Math.max(clump.blades - 1, 1) - 0.5) * 2.2;
      grassBlade(
        doc,
        rng,
        clump.x + lean * 26,
        baseY + 6,
        clump.height * (0.55 + rng() * 0.5),
        lean * 0.8,
        doc.vGradient(baseY - clump.height, baseY, [
          [token("green_mid")],
          [token("green_deep")],
        ]),
      );
    }
  }

  // Loose blades along the whole bottom edge.
  for (let i = 0; i < edgeBlades; i++) {
    const x = 40 + rng() * (W - 80);
    grassBlade(
      doc,
      rng,
      x,
      baseY + 4,
      50 + rng() * 60,
      (rng() * 2 - 1) * 0.9,
      token("green_deep"),
      0.9,
    );
  }

  // A few oversized flower heads rising into the frame.
  const accents = ["accent_warm", "accent_cool", "accent_warm"];
  for (let i = 0; i < flowers; i++) {
    const x = 120 + rng() * (W - 240);
    const stemH = 90 + rng() * 70;
    doc.add(
      el("path", {
        d: `M${fmt(x - 2.4)} ${fmt(baseY + 4)}C${fmt(x - 3.4)} ${fmt(
          baseY - stemH * 0.55,
        )} ${fmt(x + 1.4)} ${fmt(baseY - stemH * 0.8)} ${fmt(x + 1)} ${fmt(
          baseY - stemH,
        )}L${fmt(x + 4)} ${fmt(baseY - stemH + 1.4)}C${fmt(x + 4.6)} ${fmt(
          baseY - stemH * 0.5,
        )} ${fmt(x + 3)} ${fmt(baseY + 4)} ${fmt(x + 3)} ${fmt(baseY + 4)}Z`,
        fill: token("green_deep"),
      }),
    );
    const r = 9 + rng() * 6;
    const petals = 5;
    for (let p = 0; p < petals; p++) {
      const a = (p / petals) * Math.PI * 2 + rng() * 0.3;
      doc.add(
        el("ellipse", {
          cx: fmt(x + 1 + Math.cos(a) * r),
          cy: fmt(baseY - stemH - 2 + Math.sin(a) * r),
          rx: fmt(r * 0.72),
          ry: fmt(r * 0.5),
          fill: token(accents[i % accents.length]),
          opacity: "0.95",
        }),
      );
    }
    doc.add(
      el("circle", {
        cx: fmt(x + 1),
        cy: fmt(baseY - stemH - 2),
        r: fmt(r * 0.45),
        fill: token("glow"),
      }),
    );
  }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const PAINTERS = {
  far: paintFar,
  mid: paintMid,
  ground: paintGround,
  fore: paintFore,
};

export async function generateBackdrops() {
  const assets = JSON.parse(await readFile(path.join(ROOT, "assets.json"), "utf8"));
  const recipes = JSON.parse(
    await readFile(path.join(HERE, "recipes", "backdrops.json"), "utf8"),
  );
  const written = [];

  for (const entry of assets.entries) {
    if (entry.kind !== "backdrop") continue;
    const zoneRecipe = recipes[entry.zone];
    if (!zoneRecipe) {
      throw new Error(`entry '${entry.name}': no backdrops.json recipe for zone '${entry.zone}'`);
    }
    const bandRecipe = zoneRecipe[entry.sceneBand];
    if (!bandRecipe) {
      throw new Error(`entry '${entry.name}': recipe has no '${entry.sceneBand}' band`);
    }
    const paint = PAINTERS[entry.sceneBand];
    if (!paint) throw new Error(`entry '${entry.name}': unknown sceneBand '${entry.sceneBand}'`);

    const doc = createDoc({
      width: W,
      height: H,
      idPrefix: `${entry.zone}-scene-${entry.sceneBand}-`,
    });
    paint(doc, rngFor(`backdrop:${entry.zone}:${entry.sceneBand}`), bandRecipe);

    const outPath = path.join(ROOT, entry.out);
    await mkdir(path.dirname(outPath), { recursive: true });
    await writeFile(outPath, doc.render(), "utf8");
    written.push(entry.out);
  }
  return written;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const written = await generateBackdrops();
  console.log(`backdrops: ${written.length} svg files regenerated`);
}
