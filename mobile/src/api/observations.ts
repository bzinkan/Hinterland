import { apiRequest } from "@/src/api/client";

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

export type PhotoPresignResponse = {
  photo_id: string;
  upload_url: string | null;
  /** Current Observation upload-header contract. Apply verbatim. */
  upload_headers?: Record<string, string>;
  /** One-release compatibility with the existing Azure API. */
  required_headers?: Record<string, string>;
  object_name: string;
  bucket: string;
  content_type: string;
  expires_at: string | null;
  attachment_status?: "reserved" | "attached" | "deleted";
  observation_id?: string | null;
};

export function presignPhoto(
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<PhotoPresignResponse> {
  return apiRequest<PhotoPresignResponse>("/v1/photos/presign", {
    method: "POST",
    body: { content_type: "image/jpeg" },
    headers: { "Idempotency-Key": idempotencyKey },
    signal,
  });
}

export type IdentificationSource =
  | "catalog"
  | "cv"
  | "manual_text"
  | "unknown"
  | "legacy";

export type LocationSource =
  | "device_coarse"
  | "manual_coarse"
  | "none"
  | "legacy_coarsened";

export type DispatchStatus = "pending" | "partial" | "complete" | "unverified";

export type ObservationCreate = {
  photo_id: string;
  /** Accepted only for one legacy release; new clients send geohash4. */
  latitude?: number | null;
  longitude?: number | null;
  observed_at?: string;
  geohash4?: string | null;
  location_source?: Exclude<LocationSource, "legacy_coarsened">;
  taxon_id?: number | null;
  species_name?: string | null;
  identification_source?: Exclude<IdentificationSource, "cv" | "legacy">;
  place_name?: string | null;
  ecology_tags?: Record<string, string>;
};

export type Observation = {
  id: string;
  user_id: string;
  group_id: string;
  photo_id: string;
  latitude: number | null;
  longitude: number | null;
  geohash4: string | null;
  observed_at: string | null;
  location_source: LocationSource;
  taxon_id: number | null;
  species_name: string | null;
  identification_source: IdentificationSource;
  identification_revision: number;
  place_name: string | null;
  ecology_tags?: Record<string, string>;
  moderation_status: string;
  dispatch_status: DispatchStatus;
  rewards: ObservationReward[];
};

export function createObservation(
  payload: ObservationCreate,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<Observation> {
  return apiRequest<Observation>("/v1/observations", {
    method: "POST",
    body: payload,
    headers: { "Idempotency-Key": idempotencyKey },
    signal,
  });
}

export function getObservation(
  observationId: string,
  signal?: AbortSignal,
): Promise<Observation> {
  return apiRequest<Observation>(`/v1/observations/${observationId}`, { signal });
}

/** Generic PATCH is intentionally limited to non-derived display metadata. */
export type ObservationPatch = { place_name?: string | null };

export function patchObservation(
  observationId: string,
  payload: ObservationPatch,
  signal?: AbortSignal,
): Promise<Observation> {
  return apiRequest<Observation>(`/v1/observations/${observationId}`, {
    method: "PATCH",
    body: payload,
    signal,
  });
}

export type CvSuggestion = {
  taxon_id: number;
  common_name: string | null;
  scientific_name: string | null;
  score: number;
  source?: "inat" | "fallback";
};

export type IdentifyResponse = {
  observation_id: string;
  suggestions: CvSuggestion[];
  cv_unavailable: boolean;
  no_matches: boolean;
};

export function identifyObservation(
  observationId: string,
  signal?: AbortSignal,
): Promise<IdentifyResponse> {
  return apiRequest<IdentifyResponse>(
    `/v1/observations/${observationId}/identify`,
    { method: "POST", signal },
  );
}

export type ObservationListItem = {
  id: string;
  user_id: string;
  group_id: string;
  photo_id: string;
  photo_object_name: string;
  photo_status: string;
  latitude: number | null;
  longitude: number | null;
  geohash4: string | null;
  observed_at: string | null;
  location_source: LocationSource;
  taxon_id: number | null;
  species_name: string | null;
  identification_source: IdentificationSource;
  place_name: string | null;
  ecology_tags?: Record<string, string>;
  moderation_status: string;
  dispatch_status: DispatchStatus;
  created_at: string;
};

export type ObservationListResponse = {
  items: ObservationListItem[];
  next_cursor: string | null;
};

export function listMyObservations(
  opts: { limit?: number; before?: string | null } = {},
  signal?: AbortSignal,
): Promise<ObservationListResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before) params.set("before", opts.before);
  const query = params.toString();
  return apiRequest<ObservationListResponse>(
    `/v1/observations/me${query ? `?${query}` : ""}`,
    { signal },
  );
}

export type IdentificationUpdate = {
  taxon_id?: number | null;
  manual_text?: string | null;
  source: "catalog" | "cv" | "manual_text" | "unknown";
  expected_revision: number;
};

export type IdentificationUpdateResponse = {
  observation: Observation;
  rebuild_id: string;
  rebuild_status: "queued" | "running" | "succeeded" | "failed";
};

export function updateObservationIdentification(
  observationId: string,
  payload: IdentificationUpdate,
  signal?: AbortSignal,
): Promise<IdentificationUpdateResponse> {
  return apiRequest<IdentificationUpdateResponse>(
    `/v1/observations/${observationId}/identification`,
    { method: "POST", body: payload, signal },
  );
}
