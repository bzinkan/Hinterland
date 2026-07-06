/**
 * Emit the app-side art manifests from assets.json + the generated svg/.
 *
 * Outputs (all under mobile/src/sanctuary/art/):
 *   islandLayers.gen.ts -- palette-slot vocabulary + per-zone island layer
 *                          bands (back/base/mid/fore) as inline SVG
 *   sprites.gen.ts      -- element/fallback/souvenir/scenery sprites as
 *                          inline SVG (the D2 stub's exported names, kept)
 *   backdrops.gen.ts    -- full-bleed biome-scene depth bands
 *                          (far/mid/ground/fore, ADR 0012 addendum) for
 *                          zones that have graduated from the island-art
 *                          interim (meadow first)
 *
 * Every file is linted against the ADR 0012 Skia allowlist before it is
 * inlined, output ordering is byte-stable, and CI runs `git diff
 * --exit-code` on the generated TS so drift cannot merge (same trick as
 * content/schema/). validate.mjs re-renders these strings in-memory and
 * compares them to the committed files.
 *
 * Usage: node build_manifest.mjs   (from scripts/sanctuary_assets)
 */

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { lintSvgSource } from "./author/lib/svg.mjs";
import { DEFAULT_SLOT_HEX, PALETTE_SLOTS } from "./author/lib/tokens.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART_DIR = path.join(HERE, "..", "..", "mobile", "src", "sanctuary", "art");

const ZONE_ORDER = ["meadow", "woodland", "pond", "sky", "soil", "urban", "elsewhere"];
const BAND_ORDER = ["back", "base", "mid", "fore"];
const SCENE_BAND_ORDER = ["far", "mid", "ground", "fore"];
const ELEMENT_TYPE_ORDER = ["coarse", "charismatic", "relationship", "surprise", "signature"];
const LAYER_VIEWBOX = { width: 512, height: 384 };
const SPRITE_VIEWBOX = { width: 128, height: 128 };
const BACKDROP_VIEWBOX = { width: 1024, height: 640 };

const HEADER = `// GENERATED — do not edit; regenerate via scripts/sanctuary_assets
// (node author/generate_layers.mjs && node author/generate_sprites.mjs &&
//  node build_manifest.mjs). Source of truth: assets.json + author/recipes/.
// CI re-runs the pipeline and diffs this file, so hand-edits cannot merge.`;

const SLOT_DOC = `// Palette-token tinting (ADR 0012): art references {{slot}} placeholders
// only -- the renderer substitutes hexes per season/zone, so seasonal and
// dormant looks are token remaps, never duplicate art. Baseline mapping
// (spring-neutral, from author/lib/tokens.mjs):
${PALETTE_SLOTS.map((slot) => `//   ${slot.padEnd(12)} ${DEFAULT_SLOT_HEX[slot]}`).join("\n")}
// horizon tracks ScenePalette.horizon and glow the ScenePalette.sunColor
// family (season/palette.ts); green_mid tracks ScenePalette.ground; the
// accent_* slots track season.zone_accents at dive time.`;

async function loadSvg(entry, expectViewBox) {
  const svgPath = path.join(HERE, entry.out);
  const source = await readFile(svgPath, "utf8");
  const errors = lintSvgSource(source, { expectViewBox });
  if (errors.length > 0) {
    throw new Error(`${entry.out}:\n  ${errors.join("\n  ")}`);
  }
  return source;
}

function spriteFields(entry, source, viewBox) {
  return [
    `    zone: ${JSON.stringify(entry.zone)},`,
    `    anchor: ${JSON.stringify(entry.anchor ?? null)},`,
    `    scale: ${entry.scale ?? 1},`,
    ...(entry.spriteClass === "scenery" ? [`    tierMin: ${entry.tierMin},`] : []),
    `    viewBox: { width: ${viewBox.width}, height: ${viewBox.height} },`,
    `    svg: ${JSON.stringify(source)},`,
  ];
}

function record(indent, entries) {
  return entries.map(([key, lines]) => `${indent}${JSON.stringify(key)}: {\n${lines.join("\n")}\n${indent}},`).join("\n");
}

/** Render both generated TS modules (also used by validate.mjs for drift). */
export async function renderManifests() {
  const assets = JSON.parse(await readFile(path.join(HERE, "assets.json"), "utf8"));

  const layers = assets.entries.filter((entry) => entry.kind === "layer");
  const sprites = assets.entries.filter((entry) => entry.kind === "sprite");
  const backdrops = assets.entries.filter((entry) => entry.kind === "backdrop");
  const byClass = (spriteClass) =>
    sprites
      .filter((entry) => entry.spriteClass === spriteClass)
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

  // --- islandLayers.gen.ts -------------------------------------------------

  const zoneBlocks = [];
  for (const zone of ZONE_ORDER) {
    const bandBlocks = [];
    for (const band of BAND_ORDER) {
      const entry = layers.find((candidate) => candidate.zone === zone && candidate.layerBand === band);
      if (!entry) throw new Error(`assets.json is missing layer ${zone}/${band}`);
      const source = await loadSvg(entry, `0 0 ${LAYER_VIEWBOX.width} ${LAYER_VIEWBOX.height}`);
      bandBlocks.push(
        `    ${band}: {\n      viewBox: { width: ${LAYER_VIEWBOX.width}, height: ${LAYER_VIEWBOX.height} },\n      svg: ${JSON.stringify(source)},\n    },`,
      );
    }
    zoneBlocks.push(`  ${zone}: {\n${bandBlocks.join("\n")}\n  },`);
  }
  const extraLayers = layers.filter(
    (entry) => !ZONE_ORDER.includes(entry.zone) || !BAND_ORDER.includes(entry.layerBand),
  );
  if (extraLayers.length > 0) {
    throw new Error(`unexpected layer entries: ${extraLayers.map((entry) => entry.name).join(", ")}`);
  }

  const islandLayersTs = `${HEADER}
//
${SLOT_DOC}

import type { SanctuaryZoneId } from "@/src/api/sanctuary";

/** Canonical palette-slot vocabulary (author/lib/tokens.mjs). */
export const SANCTUARY_PALETTE_SLOTS = [
${PALETTE_SLOTS.map((slot) => `  "${slot}",`).join("\n")}
] as const;

export type SanctuaryPaletteSlot = (typeof SANCTUARY_PALETTE_SLOTS)[number];

/** Painter-order parallax bands of one zone island (back first). */
export type SanctuaryIslandLayerBand = "back" | "base" | "mid" | "fore";

export type SanctuaryIslandLayer = {
  viewBox: { width: number; height: number };
  /** Inline SVG source; {{slot}} placeholders tint at draw time. */
  svg: string;
};

/** Zone island art, 512x384 canvas, bottom-center anchored. */
export const SANCTUARY_ISLAND_LAYERS: Record<
  SanctuaryZoneId,
  Record<SanctuaryIslandLayerBand, SanctuaryIslandLayer>
> = {
${zoneBlocks.join("\n")}
};
`;

  // --- sprites.gen.ts --------------------------------------------------------

  const spriteViewBox = `0 0 ${SPRITE_VIEWBOX.width} ${SPRITE_VIEWBOX.height}`;
  const load = async (entries) => {
    const out = [];
    for (const entry of entries) {
      const source = await loadSvg(entry, spriteViewBox);
      out.push([entry.name, spriteFields(entry, source, SPRITE_VIEWBOX)]);
    }
    return out;
  };

  const elements = await load(byClass("element"));
  const souvenirs = await load(byClass("souvenir"));
  const scenery = await load(byClass("scenery"));
  const fallbackEntries = byClass("fallback");
  const fallbacks = [];
  for (const type of ELEMENT_TYPE_ORDER) {
    const entry = fallbackEntries.find((candidate) => candidate.elementType === type);
    if (!entry) throw new Error(`assets.json is missing the '${type}' fallback sprite`);
    const source = await loadSvg(entry, spriteViewBox);
    fallbacks.push([type, spriteFields(entry, source, SPRITE_VIEWBOX)]);
  }
  if (fallbackEntries.length !== ELEMENT_TYPE_ORDER.length) {
    throw new Error("assets.json has an unexpected extra fallback sprite");
  }

  const spritesTs = `${HEADER}
//
// Sprite atlas: 128x128 canvases, feet on the bottom-center anchor, colors
// as {{palette-slot}} placeholders (see islandLayers.gen.ts for the slot
// vocabulary and baseline hexes).

import type { SanctuaryElementType, SanctuaryZoneId } from "@/src/api/sanctuary";

export type SanctuaryElementSprite = {
  /** Inline SVG source; {{slot}} placeholders tint at draw time. */
  svg: string;
  viewBox: { width: number; height: number };
  zone: SanctuaryZoneId | "shore" | null;
  /** Foot anchor within the sprite, 0..1 from the top-left (null = bottom-center). */
  anchor: { x: number; y: number } | null;
  /** Authored semantic scale multiplier (1 = drawn at native sprite size). */
  scale: number;
};

export type SanctuaryScenerySprite = SanctuaryElementSprite & {
  /** Renders once the zone's depth_tier reaches this threshold. */
  tierMin: number;
};

/** Content icon key (content/sanctuary/*.json) -> placed sprite. */
export const SANCTUARY_ELEMENT_SPRITES: Record<string, SanctuaryElementSprite> = {
${record("  ", elements)}
};

/** element_type -> fallback motif (dome/crystal/ring/trinket/landmark). */
export const SANCTUARY_FALLBACK_SPRITES: Record<SanctuaryElementType, SanctuaryElementSprite> = {
${record("  ", fallbacks)}
};

/** Expedition souvenirs, keyed sanctuary.souvenir.<expedition_id>. */
export const SANCTUARY_SOUVENIR_SPRITES: Record<string, SanctuaryElementSprite> = {
${record("  ", souvenirs)}
};

/** Zone tier-dressing sprites, keyed by entry name. */
export const SANCTUARY_SCENERY_SPRITES: Record<string, SanctuaryScenerySprite> = {
${record("  ", scenery)}
};
`;

  // --- backdrops.gen.ts ------------------------------------------------------

  const backdropZones = ZONE_ORDER.filter((zone) =>
    backdrops.some((entry) => entry.zone === zone),
  );
  const extraBackdrops = backdrops.filter(
    (entry) => !ZONE_ORDER.includes(entry.zone) || !SCENE_BAND_ORDER.includes(entry.sceneBand),
  );
  if (extraBackdrops.length > 0) {
    throw new Error(`unexpected backdrop entries: ${extraBackdrops.map((entry) => entry.name).join(", ")}`);
  }

  const backdropViewBox = `0 0 ${BACKDROP_VIEWBOX.width} ${BACKDROP_VIEWBOX.height}`;
  const backdropBlocks = [];
  for (const zone of backdropZones) {
    const bandBlocks = [];
    for (const band of SCENE_BAND_ORDER) {
      const entry = backdrops.find((candidate) => candidate.zone === zone && candidate.sceneBand === band);
      if (!entry) throw new Error(`assets.json backdrop set for '${zone}' is missing the '${band}' band (a scene ships all four or none)`);
      const source = await loadSvg(entry, backdropViewBox);
      bandBlocks.push(
        `    ${band}: {\n      viewBox: { width: ${BACKDROP_VIEWBOX.width}, height: ${BACKDROP_VIEWBOX.height} },\n      svg: ${JSON.stringify(source)},\n    },`,
      );
    }
    backdropBlocks.push(`  ${zone}: {\n${bandBlocks.join("\n")}\n  },`);
  }

  const backdropsTs = `${HEADER}
//
// Full-bleed biome-scene depth bands (ADR 0012 addendum: chooser +
// scenes). 1024x640 canvases, bottom-anchored by the renderer per band
// (sceneLayout.ts), colors as {{palette-slot}} placeholders (see
// islandLayers.gen.ts for the slot vocabulary and baseline hexes).
// Zones absent here render the island-art interim in their scene.

import type { SanctuaryZoneId } from "@/src/api/sanctuary";

/** Painter-order scene depth bands (back first; fore outruns the camera). */
export type SanctuarySceneBand = ${SCENE_BAND_ORDER.map((band) => `"${band}"`).join(" | ")};

export type SanctuaryBackdropLayer = {
  viewBox: { width: number; height: number };
  /** Inline SVG source; {{slot}} placeholders tint at draw time. */
  svg: string;
};

/** Zone scene backdrops, 1024x640 canvas, subject painted low. */
export const SANCTUARY_BACKDROPS: Partial<
  Record<SanctuaryZoneId, Record<SanctuarySceneBand, SanctuaryBackdropLayer>>
> = {
${backdropBlocks.join("\n")}
};
`;

  return { islandLayersTs, spritesTs, backdropsTs };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { islandLayersTs, spritesTs, backdropsTs } = await renderManifests();
  await writeFile(path.join(ART_DIR, "islandLayers.gen.ts"), islandLayersTs, "utf8");
  await writeFile(path.join(ART_DIR, "sprites.gen.ts"), spritesTs, "utf8");
  await writeFile(path.join(ART_DIR, "backdrops.gen.ts"), backdropsTs, "utf8");
  console.log(
    "manifest: wrote mobile/src/sanctuary/art/{islandLayers.gen.ts,sprites.gen.ts,backdrops.gen.ts}",
  );
}
