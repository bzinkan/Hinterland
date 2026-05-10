import { apiRequest } from "@/src/api/client";

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
};

export function createObservation(payload: ObservationCreate): Promise<Observation> {
  return apiRequest<Observation>("/v1/observations", {
    method: "POST",
    body: payload,
  });
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
