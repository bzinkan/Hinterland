/**
 * DEV-ONLY synthesized Sanctuary snapshot for previewing the diorama
 * without an account (and for stepping zones through tiers 0->50 on
 * device). Uses REAL content icon keys so manifest coverage is exercised
 * exactly like production data. Never imported outside the dev controls
 * path; never shown to a signed-in user's real Sanctuary.
 */

import type {
  SanctuaryElementDto,
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";

const TS = "2026-06-01T12:00:00Z";

function element(
  id: string,
  zone: SanctuaryZoneId,
  type: SanctuaryElementDto["element_type"],
  title: string,
  icon: string,
): SanctuaryElementDto {
  return {
    element_id: id,
    zone_id: zone,
    element_type: type,
    title,
    detail: "Dev preview element. Real copy comes from authored content.",
    icon,
    taxon_id: null,
    source_observation_id: null,
    unlocked_at: TS,
    payload: {},
  };
}

/** Zone tier ladder: the preview tier applies fully to the "front three"
 * zones; the others trail behind so one screen shows multiple states. */
function zoneTiers(tier: number): Record<SanctuaryZoneId, number> {
  const trail = (steps: number): number => {
    const ladder = [0, 1, 3, 5, 10, 20, 50];
    const idx = Math.max(0, ladder.indexOf(tier) - steps);
    return ladder[Math.max(idx, 0)];
  };
  return {
    meadow: tier,
    woodland: tier,
    pond: trail(1),
    sky: trail(2),
    soil: trail(2),
    urban: trail(3),
    elsewhere: 0, // stays dormant with a mystery cue
  };
}

const ZONE_TITLES: Record<SanctuaryZoneId, string> = {
  meadow: "Meadow",
  woodland: "Woodland",
  pond: "Pond",
  sky: "Sky",
  soil: "Soil",
  urban: "Urban",
  elsewhere: "Elsewhere",
};

const NEXT: Record<number, number | null> = {
  0: 1, 1: 3, 3: 5, 5: 10, 10: 20, 20: 50, 50: null,
};

export function makeSampleSnapshot(tier: number): SanctuarySnapshotDto {
  const tiers = zoneTiers(tier);
  const zones = (Object.keys(tiers) as SanctuaryZoneId[]).map((zoneId) => ({
    zone_id: zoneId,
    title: ZONE_TITLES[zoneId],
    mood: "Dev preview zone.",
    description: "",
    observation_count: tiers[zoneId],
    depth_tier: tiers[zoneId],
    unlocked: tiers[zoneId] >= 1,
    next_threshold: NEXT[tiers[zoneId]] ?? null,
    accent: null,
  }));

  const elements: SanctuaryElementDto[] = [];
  if (tiers.meadow >= 1) {
    elements.push(
      element("dev_meadow_plantae", "meadow", "coarse", "Plants in the meadow", "sanctuary.meadow.plantae"),
      element("dev_meadow_insecta", "meadow", "coarse", "Insects in the meadow", "sanctuary.meadow.insecta"),
    );
  }
  if (tiers.meadow >= 3) {
    elements.push(
      element("dev_meadow_monarch", "meadow", "charismatic", "A monarch visits your meadow", "sanctuary.meadow.monarch"),
      element("dev_meadow_surprise", "meadow", "surprise", "A drifting petal", "sanctuary.meadow.plantae"),
    );
  }
  if (tiers.woodland >= 1) {
    elements.push(
      element("dev_wood_mammalia", "woodland", "coarse", "A quiet shape between trees", "sanctuary.woodland.mammalia"),
    );
  }
  if (tiers.woodland >= 3) {
    elements.push(
      element("dev_wood_fox", "woodland", "charismatic", "A red fox steps out", "sanctuary.woodland.red_fox"),
      element("dev_wood_aves", "woodland", "coarse", "Birds in the woodland", "sanctuary.woodland.aves"),
    );
  }
  if (tiers.pond >= 1) {
    elements.push(
      element("dev_pond_amphibia", "pond", "coarse", "Frogs at the pond", "sanctuary.pond.amphibia"),
    );
  }
  if (tiers.pond >= 3) {
    elements.push(
      element("dev_pond_dragonfly", "pond", "charismatic", "A dragonfly dances", "sanctuary.pond.dragonfly"),
    );
  }
  if (tiers.sky >= 1) {
    elements.push(
      element("dev_sky_aves", "sky", "coarse", "Birds overhead", "sanctuary.sky.aves"),
    );
  }
  if (tiers.soil >= 1) {
    elements.push(
      element("dev_soil_fungi", "soil", "coarse", "Fungi underfoot", "sanctuary.soil.fungi"),
    );
  }
  if (tiers.urban >= 1) {
    elements.push(
      element("dev_urban_raccoon", "urban", "charismatic", "A raccoon ambles by", "sanctuary.urban.raccoon"),
    );
  }
  if (tiers.meadow >= 10) {
    elements.push(
      element("dev_meadow_pollination", "meadow", "relationship", "Flower meets wing", "sanctuary.meadow.pollination_moment"),
    );
  }
  if (tiers.meadow >= 50) {
    elements.push(
      element("dev_meadow_signature", "meadow", "signature", "The wildflower spiral", "sanctuary.meadow.plantae"),
    );
  }

  return {
    zones,
    elements,
    recent_events: [],
    guide_message: {
      speaker: "dragonfly",
      text:
        tier === 0
          ? "Your Sanctuary is quiet. One real observation can wake it up."
          : "Dev preview: stepping the island through its tiers.",
    },
    mystery_cues: [
      { zone_id: "elsewhere", title: "A far-off shimmer", detail: "Something unnamed waits." },
      ...(tiers.urban === 0
        ? [{ zone_id: "urban" as SanctuaryZoneId, title: "A corner of the city", detail: "Life finds the edges." }]
        : []),
    ],
    journal: [],
    identity_reflection: null,
    relationship_moments: [],
    tiny_surprises: [],
    season: {
      season: "spring",
      background_tone: "fresh",
      zone_accents: {
        meadow: "", woodland: "", pond: "", sky: "", soil: "", urban: "", elsewhere: "",
      },
      variant_copy: null,
    },
    soundscapes: [],
    sound_assets_available: false,
  };
}

/** The tier ladder the dev stepper walks. */
export const DEV_TIER_LADDER = [0, 1, 3, 5, 10, 20, 50] as const;
