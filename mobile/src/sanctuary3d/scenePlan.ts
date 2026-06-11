/**
 * The pure firewall between Sanctuary data and the renderer (ADR 0011):
 * SanctuarySnapshotDto -> ScenePlan, with no three.js imports and no
 * randomness. The same snapshot always produces a deep-equal plan, on any
 * platform -- this is what makes the scene deterministic across visits and
 * devices, and what would let the renderer be swapped without touching
 * product logic.
 *
 * Tier semantics: SanctuaryZoneDto.depth_tier carries the highest crossed
 * THRESHOLD VALUE (0 | 1 | 3 | 5 | 10 | 20 | 50), not an index -- the same
 * convention the 2D tier dots and the manifest's `tierMin` use.
 */

import type {
  SanctuaryElementDto,
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import { SANCTUARY_ZONE_ORDER } from "@/src/api/sanctuary";
import {
  resolveElementAsset,
  type SanctuaryElementAsset,
} from "@/src/sanctuary3d/assets/manifest";
import {
  placeElement,
  type ElementTransform,
} from "@/src/sanctuary3d/placement/seededLayout";
import {
  scenePalette,
  type ScenePalette,
} from "@/src/sanctuary3d/season/palette";

export type PlacedElement = {
  element: SanctuaryElementDto;
  /** Manifest asset when modeled; null -> render the typed fallback shape. */
  asset: SanctuaryElementAsset | null;
  transform: ElementTransform;
};

export type ZonePlan = {
  zoneId: SanctuaryZoneId;
  title: string;
  unlocked: boolean;
  /** Threshold value: 0 | 1 | 3 | 5 | 10 | 20 | 50. */
  depthTier: number;
  observationCount: number;
  elements: PlacedElement[];
  /** True when the API surfaced a mystery cue for this (locked) zone. */
  silhouette: boolean;
};

export type ScenePlan = {
  palette: ScenePalette;
  zones: ZonePlan[];
  /** True when nothing is awake yet: render the invitation state. */
  isInvitationState: boolean;
};

export function buildScenePlan(snapshot: SanctuarySnapshotDto): ScenePlan {
  const zoneById = new Map(snapshot.zones.map((z) => [z.zone_id, z] as const));
  const cueZones = new Set(snapshot.mystery_cues.map((c) => c.zone_id));

  // Group elements per zone, sorted by element_id so the (index -> slot)
  // assignment is stable regardless of API ordering.
  const elementsByZone = new Map<SanctuaryZoneId, SanctuaryElementDto[]>();
  for (const element of snapshot.elements) {
    const list = elementsByZone.get(element.zone_id) ?? [];
    list.push(element);
    elementsByZone.set(element.zone_id, list);
  }
  for (const list of elementsByZone.values()) {
    list.sort((a, b) => a.element_id.localeCompare(b.element_id));
  }

  const zones: ZonePlan[] = SANCTUARY_ZONE_ORDER.flatMap((zoneId) => {
    const zone = zoneById.get(zoneId);
    if (!zone) return [];
    const zoneElements = elementsByZone.get(zoneId) ?? [];
    const placed: PlacedElement[] = zoneElements.map((element, index) => ({
      element,
      asset: resolveElementAsset(element.icon),
      transform: placeElement(
        zoneId,
        element.element_id,
        index,
        zoneElements.length,
      ),
    }));
    return [
      {
        zoneId,
        title: zone.title,
        unlocked: zone.unlocked,
        depthTier: zone.depth_tier,
        observationCount: zone.observation_count,
        elements: placed,
        silhouette: !zone.unlocked && cueZones.has(zoneId),
      },
    ];
  });

  const anyAwake =
    zones.some((z) => z.unlocked) || snapshot.elements.length > 0;

  return {
    palette: scenePalette(
      snapshot.season.season,
      snapshot.season.background_tone,
    ),
    zones,
    isInvitationState: !anyAwake,
  };
}
