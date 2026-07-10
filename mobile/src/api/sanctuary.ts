/**
 * Sanctuary read API client.
 *
 * Mirrors the snake_case wire shape of ``GET /v1/sanctuary/me`` from
 * ``docs/sanctuary.md`` section 9. Field names match the backend DTOs in
 * ``backend/app/api/routes/sanctuary.py`` so the JSON deserializes by
 * structural assignment (no key renaming on the client).
 */

import { apiRequest } from "@/src/api/client";

// ---------------------------------------------------------------------------
// Vocabularies
// ---------------------------------------------------------------------------

export type SanctuaryZoneId =
  | "meadow"
  | "woodland"
  | "pond"
  | "sky"
  | "soil"
  | "urban"
  | "elsewhere";

export type SanctuaryElementType =
  | "coarse"
  | "charismatic"
  | "relationship"
  | "surprise"
  | "signature";

export type SanctuaryEventType =
  | "world_unlock"
  | "world_evolution"
  | "relationship"
  | "surprise";

export type SanctuarySeason = "spring" | "summer" | "autumn" | "winter";

export type SanctuarySoundKind =
  | "bird_chirp"
  | "pond_ripple"
  | "meadow_buzz"
  | "wind"
  | "frog_croak";

/** Authored zone order. The backend always returns zones in this order. */
export const SANCTUARY_ZONE_ORDER: readonly SanctuaryZoneId[] = [
  "meadow",
  "woodland",
  "pond",
  "sky",
  "soil",
  "urban",
  "elsewhere",
];

// ---------------------------------------------------------------------------
// DTOs (match backend response 1:1)
// ---------------------------------------------------------------------------

export type SanctuaryZoneDto = {
  zone_id: SanctuaryZoneId;
  title: string;
  mood: string;
  description: string;
  observation_count: number;
  depth_tier: number;
  unlocked: boolean;
  next_threshold: number | null;
  accent: string | null;
};

export type SanctuaryElementDto = {
  element_id: string;
  zone_id: SanctuaryZoneId;
  element_type: SanctuaryElementType;
  title: string;
  detail: string;
  icon: string;
  taxon_id: number | null;
  source_observation_id: string | null;
  unlocked_at: string;
  payload: Record<string, unknown>;
};

export type SanctuaryEventDto = {
  event_type: SanctuaryEventType;
  zone_id: SanctuaryZoneId | null;
  element_id: string | null;
  title: string;
  detail: string | null;
  created_at: string;
  payload: Record<string, unknown>;
};

export type SanctuaryGuideMessageDto = {
  speaker: "guide";
  text: string;
};

export type SanctuaryMysteryCueDto = {
  zone_id: SanctuaryZoneId;
  title: string;
  detail: string;
};

export type SanctuaryJournalEntryDto = {
  event_type: SanctuaryEventType;
  zone_id: SanctuaryZoneId | null;
  element_id: string | null;
  title: string;
  detail: string | null;
  created_at: string;
};

export type SanctuaryIdentityReflectionDto = {
  id: string;
  text: string;
};

export type SanctuaryRelationshipMomentDto = {
  element_id: string;
  zone_id: SanctuaryZoneId;
  title: string;
  detail: string;
  icon: string;
  unlocked_at: string;
};

export type SanctuaryTinySurpriseDto = {
  element_id: string;
  zone_id: SanctuaryZoneId;
  threshold: number | null;
  title: string;
  detail: string;
  icon: string;
  unlocked_at: string;
};

/**
 * Date-based seasonal info attached to every Sanctuary response.
 *
 * `season` and `background_tone` come from a server-side palette keyed on
 * the current Northern-Hemisphere meteorological calendar (see
 * `docs/sanctuary.md` "Seasonal variants & sound placeholders" for the
 * documented limitation). `zone_accents` is a per-zone short label for
 * the current season -- the mobile screen renders it as a small italic
 * tag under each band's mood line.
 *
 * `variant_copy` is the only kid-facing prose; it comes verbatim from
 * authored `content/sanctuary/seasonal_variants.json` when an authored
 * variant matches the current season (preferring one whose `element_ref`
 * the kid has already unlocked). May be null when no variant is authored
 * for the current season.
 */
export type SanctuarySeasonDto = {
  season: SanctuarySeason;
  background_tone: string;
  zone_accents: Record<SanctuaryZoneId, string>;
  variant_copy: string | null;
};

/**
 * A placeholder describing a future ambient sound for the Sanctuary.
 *
 * No audio assets ship with this DTO -- the mobile screen renders
 * `label` + `description` as a quiet "coming soon" entry. The screen
 * NEVER autoplays sound, requests microphone permission, or wires this
 * DTO to an analytics SDK. When `SanctuarySnapshotDto.sound_assets_available`
 * flips to true in a later PR, the same DTO will gain real asset
 * resolution at the client; the wire shape stays stable.
 */
export type SanctuarySoundscapeDto = {
  id: string;
  kind: SanctuarySoundKind;
  zone_id: SanctuaryZoneId | null;
  label: string;
  description: string;
};

export type SanctuarySnapshotDto = {
  zones: SanctuaryZoneDto[];
  elements: SanctuaryElementDto[];
  recent_events: SanctuaryEventDto[];
  guide_message: SanctuaryGuideMessageDto;
  mystery_cues: SanctuaryMysteryCueDto[];
  journal: SanctuaryJournalEntryDto[];
  identity_reflection: SanctuaryIdentityReflectionDto | null;
  relationship_moments: SanctuaryRelationshipMomentDto[];
  tiny_surprises: SanctuaryTinySurpriseDto[];
  season: SanctuarySeasonDto;
  soundscapes: SanctuarySoundscapeDto[];
  sound_assets_available: boolean;
};

// ---------------------------------------------------------------------------
// GET /v1/sanctuary/me
// ---------------------------------------------------------------------------

/**
 * Fetch the signed-in user's Sanctuary snapshot.
 *
 * Read-only, current-user-scoped. The route takes no parameters; a hostile
 * caller cannot pass `?user_id=...` to fetch another user's Sanctuary.
 */
export function getMySanctuary(signal?: AbortSignal): Promise<SanctuarySnapshotDto> {
  return apiRequest<SanctuarySnapshotDto>("/v1/sanctuary/me", { signal });
}
