/**
 * Dev-only spot-render: compose one island (all four layers + a few
 * sprites, palette tokens substituted with the baseline hexes) into a
 * single standalone SVG under .preview/ (git-ignored) so a human can
 * open it in a browser. Never feeds the app or CI -- purely for eyes.
 *
 * Usage: node preview.mjs   (from scripts/sanctuary_assets)
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { DEFAULT_SLOT_HEX } from "./author/lib/tokens.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(HERE, ".preview");

/** zone -> a few sprites to place: [entry out path, x, y, scale]. */
const SPRITE_PLACEMENTS = {
  meadow: [
    ["svg/sprites/scenery/bush.svg", 130, 252, 0.55],
    ["svg/sprites/elements/meadow-plantae.svg", 196, 250, 0.55],
    ["svg/sprites/scenery/flower-purple.svg", 300, 256, 0.4],
    ["svg/sprites/elements/meadow-butterfly.svg", 350, 236, 0.35],
    ["svg/sprites/scenery/rock-small-a.svg", 402, 252, 0.35],
  ],
  pond: [
    ["svg/sprites/scenery/rock-large-a.svg", 122, 254, 0.7],
    ["svg/sprites/elements/pond-bullfrog.svg", 190, 252, 0.5],
    ["svg/sprites/elements/pond-dragonfly.svg", 340, 240, 0.4],
    ["svg/sprites/scenery/rock-small-d.svg", 396, 252, 0.35],
  ],
};

function substituteTokens(svg) {
  return svg.replace(/\{\{([a-z_]+)\}\}/g, (_, slot) => {
    const hex = DEFAULT_SLOT_HEX[slot];
    if (!hex) throw new Error(`no baseline hex for slot '${slot}'`);
    return hex;
  });
}

/** Strip the outer <svg> wrapper, keeping inner markup (defs included). */
function innerMarkup(svg) {
  return svg.replace(/^<svg[^>]*>\n?/, "").replace(/\n?<\/svg>\n?$/, "");
}

async function composeIsland(zone) {
  const parts = [];
  for (const band of ["back", "base", "mid", "fore"]) {
    const svg = await readFile(path.join(HERE, "svg", "layers", zone, `${band}.svg`), "utf8");
    const chunk = innerMarkup(svg);
    // fore draws over sprites; hold it back until the sprites are placed.
    parts.push({ band, chunk });
  }
  const spriteChunks = [];
  for (const [rel, x, y, scale] of SPRITE_PLACEMENTS[zone]) {
    const svg = await readFile(path.join(HERE, rel), "utf8");
    // Anchor bottom-center: sprite (64,124) lands on (x,y).
    spriteChunks.push(
      `<g transform="translate(${x} ${y}) scale(${scale}) translate(-64 -124)">${innerMarkup(svg)}</g>`,
    );
  }
  const layered = [
    ...parts.filter((p) => p.band !== "fore").map((p) => p.chunk),
    ...spriteChunks,
    ...parts.filter((p) => p.band === "fore").map((p) => p.chunk),
  ];
  const sky = `<rect x="0" y="0" width="512" height="384" fill="#CFE3F2"/><rect x="0" y="230" width="512" height="154" fill="#E4EAE0"/>`;
  return substituteTokens(
    `<svg viewBox="0 0 512 384" xmlns="http://www.w3.org/2000/svg">\n${sky}\n${layered.join("\n")}\n</svg>\n`,
  );
}

await mkdir(OUT_DIR, { recursive: true });
for (const zone of Object.keys(SPRITE_PLACEMENTS)) {
  const svg = await composeIsland(zone);
  const outPath = path.join(OUT_DIR, `${zone}.svg`);
  await writeFile(outPath, svg, "utf8");
  console.log(`preview: ${path.relative(HERE, outPath)}`);
}
