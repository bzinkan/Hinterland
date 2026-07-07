import { apiRequest } from "@/src/api/client";

// ---------------------------------------------------------------------------
// Dispatcher reward shape (mirrors backend ``RewardType`` Literal in
// ``backend/app/dispatcher/types.py``).
//
// Sanctuary reward types (``world_unlock``, ``world_evolution``) were added
// in PR #99 when ``WorldHandler`` was wired into the dispatcher. The mobile
// reveal modal filters for these two types after a successful submit.
// ---------------------------------------------------------------------------

export type RewardType =
  | "first_find"
  | "repeat_find"
  | "expedition_step"
  | "expedition_complete"
  | "rarity_tier"
  | "unrecorded"
  | "world_unlock"
  | "world_evolution"
  | "territory_claimed"
  | "season_hit"
  | "mission_progress"
  | "mission_complete";

export type ObservationReward = {
  type: RewardType;
  title: string;
  detail: string;
  icon: string;
  weight: number;
  payload: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// POST /v1/photos/presign
// ---------------------------------------------------------------------------

export type PhotoPresignResponse = {
  photo_id: string;
  upload_url: string;
  object_name: string;
  bucket: string;
  content_type: string;
  expires_at: string;
  // Headers to send verbatim on the PUT to upload_url (e.g. Azure's
  // x-ms-blob-type). Optional because deployed API builds may predate
  // this field; callers fall back to legacyPutHeaders().
  required_headers?: Record<string, string>;
};

export function presignPhoto(): Promise<PhotoPresignResponse> {
  return apiRequest<PhotoPresignResponse>("/v1/photos/presign", {
    method: "POST",
    body: { content_type: "image/jpeg" },
  });
}

// ---------------------------------------------------------------------------
// POST /v1/observations
// ---------------------------------------------------------------------------

export type ObservationCreate = {
  photo_id: string;
  latitude: number;
  longitude: number;
  taxon_id?: number | null;
  species_name?: string | null;
  place_name?: string | null;
};

export type Observation = {
  id: string;
  user_id: string;
  group_id: string;
  photo_id: string;
  latitude: number;
  longitude: number;
  geohash4: string | null;
  taxon_id: number | null;
  species_name: string | null;
  place_name: string | null;
  // Dispatcher-returned rewards. Present on the create response, and on
  // PATCH responses when the patch sets or changes ``taxon_id`` -- that
  // second dispatch is what advances taxon-based expedition steps. Absent
  // or [] when nothing was dispatched. Field is optional so list /
  // list-item types do not break.
  rewards?: ObservationReward[];
};

export function createObservation(payload: ObservationCreate): Promise<Observation> {
  return apiRequest<Observation>("/v1/observations", {
    method: "POST",
    body: payload,
  });
}

// ---------------------------------------------------------------------------
// PATCH /v1/observations/{id}
// ---------------------------------------------------------------------------

export type ObservationPatch = {
  taxon_id?: number | null;
  species_name?: string | null;
  place_name?: string | null;
};

export function patchObservation(
  observationId: string,
  payload: ObservationPatch,
): Promise<Observation> {
  return apiRequest<Observation>(`/v1/observations/${observationId}`, {
    method: "PATCH",
    body: payload,
  });
}

// ---------------------------------------------------------------------------
// POST /v1/observations/{id}/identify
// ---------------------------------------------------------------------------

export type CvSuggestion = {
  taxon_id: number;
  common_name: string | null;
  scientific_name: string | null;
  score: number;
};

export type IdentifyResponse = {
  observation_id: string;
  suggestions: CvSuggestion[];
  cv_unavailable: boolean;
  no_matches: boolean;
};

export function identifyObservation(observationId: string): Promise<IdentifyResponse> {
  return apiRequest<IdentifyResponse>(
    `/v1/observations/${observationId}/identify`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// GET /v1/observations/me
// ---------------------------------------------------------------------------

export type ObservationListItem = {
  id: string;
  user_id: string;
  group_id: string;
  photo_id: string;
  photo_object_name: string;
  photo_status: string;
  latitude: number;
  longitude: number;
  geohash4: string | null;
  taxon_id: number | null;
  species_name: string | null;
  place_name: string | null;
  created_at: string;
};

export type ObservationListResponse = {
  items: ObservationListItem[];
  next_cursor: string | null;
};

export function listMyObservations(
  opts: { limit?: number; before?: string | null } = {},
): Promise<ObservationListResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before) params.set("before", opts.before);
  const query = params.toString();
  return apiRequest<ObservationListResponse>(
    `/v1/observations/me${query ? `?${query}` : ""}`,
  );
}
