/**
 * SkPicture recording for one full-bleed biome scene (ADR 0012 addendum:
 * chooser + scenes; same render contract as islandArt.ts -- record once,
 * replay per frame, never a per-frame ImageSVG).
 *
 * Two art sources per zone:
 *  - Migrated zones (meadow first) draw four generated backdrop bands
 *    (backdrops.gen.ts, 1024x640) scaled to their sceneLayout band rects.
 *  - Un-migrated zones draw the documented interim: their existing island
 *    layer art (islandLayers.gen.ts) centered in ground-band space over a
 *    ground gradient the component paints (isInterim flags it).
 *
 * Ground sprites and silhouettes come from the pure ground remap
 * (groundRemap.ts) and are recorded in ground-band scene coordinates; the
 * component pans that picture 1:1 with the camera, so groundHitTest and
 * the drawn pixels share one transform.
 *
 * Palette handling matches islandArt.ts exactly: colors reach the art
 * ONLY through svgCache.substitute slot hexes -- dormant scenes are baked
 * desaturated-slot recordings, the wake lerp is the component's transient
 * saveLayer. No React here; Skia is injected so the module stays loadable
 * (and its callers testable) without the native module.
 */

import type { SkPicture, SkSVG } from "@shopify/react-native-skia";

import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { SANCTUARY_BACKDROPS } from "@/src/sanctuary/art/backdrops.gen";
import { SANCTUARY_ISLAND_LAYERS } from "@/src/sanctuary/art/islandLayers.gen";
import { SPRITE_UNITS_PER_PX, spriteScaleMultiplier } from "@/src/sanctuary/diorama/artFit";
import {
  getScenerySprite,
  resolveElementSprite,
  type SanctuaryElementSprite,
} from "@/src/sanctuary/diorama/assets/manifest";
import type { GroundPlan, ScenePlacedSprite } from "@/src/sanctuary/diorama/groundRemap";
import {
  bandRect,
  interimIslandRect,
  SCENE_BANDS,
  type SceneBand,
  type SceneMetrics,
} from "@/src/sanctuary/diorama/sceneLayout";
import { ZONE_ACCENT_COLOR } from "@/src/sanctuary/diorama/scene/zoneColors";
import { SPRITE_HALF_EXTENT } from "@/src/sanctuary/diorama/vistaPlan";
import { SILHOUETTE_DRAW_SCALE, type SkiaApi } from "@/src/sanctuary/dioramaui/islandArt";
import { ZONE_SILHOUETTE_ICON } from "@/src/sanctuary/dioramaui/silhouetteIcons";
import type { SlotHexes, SvgCache } from "@/src/sanctuary/dioramaui/svgCache";

/** Everything the biome scene replays per frame. */
export type ScenePictures = {
  /** Backdrop bands (null when the zone runs the island-art interim). */
  bands: Record<SceneBand, SkPicture> | null;
  /** Interim centered island art (null for migrated zones). */
  interimIsland: SkPicture | null;
  /** Ground-remapped inhabitants, ground-band scene coordinates. */
  sprites: SkPicture;
  /** Cued dormant zones only; drawn above the sprites. */
  silhouette: SkPicture | null;
};

/** True when a zone has a full generated backdrop band set. */
export function hasBackdrop(zoneId: SanctuaryZoneId): boolean {
  return SANCTUARY_BACKDROPS[zoneId] !== undefined;
}

/** Island layer painter order for the interim centerpiece. */
const INTERIM_BANDS = ["back", "base", "mid", "fore"] as const;

/** Resolve a placed sprite to its generated art record (islandArt idiom). */
function spriteRecordFor(
  sprite: ScenePlacedSprite,
): { cacheKey: string; record: SanctuaryElementSprite } | null {
  if (sprite.kind === "element" && sprite.iconKey !== null) {
    const res = resolveElementSprite(sprite.iconKey, "coarse");
    return res.kind === "sprite"
      ? { cacheKey: `sprite:${res.spriteKey}`, record: res.sprite }
      : null;
  }
  if (sprite.kind === "scenery") {
    const name = sprite.key.split("#")[0];
    const record = getScenerySprite(name);
    return record ? { cacheKey: `sprite:${name}`, record } : null;
  }
  // Souvenirs render as fallback shapes until D9 assigns their art.
  return null;
}

/** Record all pictures for one biome scene under one palette. */
export function recordScenePictures(opts: {
  Skia: SkiaApi;
  zoneId: SanctuaryZoneId;
  dormant: boolean;
  ground: GroundPlan;
  metrics: SceneMetrics;
  slots: SlotHexes;
  silhouetteSlots: SlotHexes;
  svgCache: SvgCache<SkSVG>;
}): ScenePictures {
  const { Skia, zoneId, dormant, ground, metrics, slots, silhouetteSlots, svgCache } = opts;
  const bounds = Skia.XYWHRect(
    -metrics.sceneWidth * 0.25,
    -metrics.h * 0.25,
    metrics.sceneWidth * 1.75,
    metrics.h * 1.75,
  );

  // --- Backdrop bands (migrated zones). Recorded band-local: the picture
  // draws at the band's rect; the component's group applies the parallax
  // translate only. ---
  const backdrop = SANCTUARY_BACKDROPS[zoneId];
  let bands: Record<SceneBand, SkPicture> | null = null;
  if (backdrop !== undefined) {
    const out = {} as Record<SceneBand, SkPicture>;
    for (const band of SCENE_BANDS) {
      const layer = backdrop[band];
      const rect = bandRect(metrics, band);
      const rec = Skia.PictureRecorder();
      const canvas = rec.beginRecording(bounds);
      const svg = svgCache.makeSvg(`backdrop:${zoneId}:${band}`, layer.svg, slots);
      if (svg !== null) {
        canvas.save();
        canvas.translate(rect.x, rect.y);
        canvas.drawSvg(svg, rect.width, rect.height);
        canvas.restore();
      }
      out[band] = rec.finishRecordingAsPicture();
    }
    bands = out;
  }

  // --- Interim centerpiece (un-migrated zones): the four island layer
  // bands drawn into one picture at the centered scene rect. ---
  let interimIsland: SkPicture | null = null;
  if (backdrop === undefined) {
    const layers = SANCTUARY_ISLAND_LAYERS[zoneId];
    const rect = interimIslandRect(metrics);
    const rec = Skia.PictureRecorder();
    const canvas = rec.beginRecording(bounds);
    for (const band of INTERIM_BANDS) {
      const svg = svgCache.makeSvg(`layer:${zoneId}:${band}`, layers[band].svg, slots);
      if (svg === null) continue;
      canvas.save();
      canvas.translate(rect.x, rect.y);
      canvas.drawSvg(svg, rect.width, rect.height);
      canvas.restore();
    }
    interimIsland = rec.finishRecordingAsPicture();
  }

  // --- Ground sprites: painter order from the remap plan; unmapped icon
  // keys degrade to accent-colored fallback circles (islandArt idiom).
  // Positions/scales are already scene dp -- no unit conversion here
  // beyond the sprite art's own px->unit factor. ---
  const spriteRec = Skia.PictureRecorder();
  const spriteCanvas = spriteRec.beginRecording(bounds);
  const fallbackPaint = Skia.Paint();
  fallbackPaint.setColor(Skia.Color(ZONE_ACCENT_COLOR[zoneId]));
  for (const s of ground.sprites) {
    const resolved = spriteRecordFor(s);
    const classMul = spriteScaleMultiplier(s.kind, s.key.split("#")[0]);
    if (!resolved) {
      const r = SPRITE_HALF_EXTENT * s.scale * classMul;
      spriteCanvas.drawCircle(s.x, s.y - r, r, fallbackPaint);
      continue;
    }
    const { cacheKey, record } = resolved;
    const svg = svgCache.makeSvg(cacheKey, record.svg, slots);
    if (svg === null) continue;
    const drawW =
      record.viewBox.width * record.scale * s.scale * classMul * SPRITE_UNITS_PER_PX;
    const drawH =
      record.viewBox.height * record.scale * s.scale * classMul * SPRITE_UNITS_PER_PX;
    const ax = record.anchor?.x ?? 0.5; // null anchor = bottom-center
    const ay = record.anchor?.y ?? 1;
    spriteCanvas.save();
    spriteCanvas.translate(s.x - drawW * ax, s.y - drawH * ay);
    spriteCanvas.drawSvg(svg, drawW, drawH);
    spriteCanvas.restore();
  }

  // --- Silhouette hint on cued dormant scenes (islandArt idiom, remapped
  // markers, silhouette slot collapse). ---
  let silhouette: SkPicture | null = null;
  if (dormant && ground.silhouettes.length > 0) {
    const iconKey = ZONE_SILHOUETTE_ICON[zoneId];
    const res = resolveElementSprite(iconKey, "coarse");
    if (res.kind === "sprite") {
      const svg = svgCache.makeSvg(
        `silhouette:${res.spriteKey}`,
        res.sprite.svg,
        silhouetteSlots,
      );
      if (svg !== null) {
        const rec = Skia.PictureRecorder();
        const canvas = rec.beginRecording(bounds);
        for (const marker of ground.silhouettes) {
          const drawW =
            res.sprite.viewBox.width *
            res.sprite.scale *
            marker.scale *
            SILHOUETTE_DRAW_SCALE *
            SPRITE_UNITS_PER_PX;
          const drawH =
            res.sprite.viewBox.height *
            res.sprite.scale *
            marker.scale *
            SILHOUETTE_DRAW_SCALE *
            SPRITE_UNITS_PER_PX;
          const ax = res.sprite.anchor?.x ?? 0.5;
          const ay = res.sprite.anchor?.y ?? 1;
          canvas.save();
          canvas.translate(marker.x - drawW * ax, marker.y - drawH * ay);
          canvas.drawSvg(svg, drawW, drawH);
          canvas.restore();
        }
        silhouette = rec.finishRecordingAsPicture();
      }
    }
  }

  return {
    bands,
    interimIsland,
    sprites: spriteRec.finishRecordingAsPicture(),
    silhouette,
  };
}
