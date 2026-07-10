import { apiRequest } from "@/src/api/client";

export type DexListItem = {
  id: string;
  taxon_id: number;
  species_name: string | null;
  common_name: string | null;
  scientific_name: string | null;
  iconic_taxon: string | null;
  first_observation_id: string;
  first_photo_id: string;
  first_photo_status: string;
  first_seen_at: string;
  observation_count: number;
  latest_seen_at: string;
};

export type DexListResponse = {
  items: DexListItem[];
  next_cursor: string | null;
};

export function listMyDex(
  opts: { limit?: number; before?: string | null } = {},
  signal?: AbortSignal,
): Promise<DexListResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.before) params.set("before", opts.before);
  const query = params.toString();
  return apiRequest<DexListResponse>(`/v1/dex/me${query ? `?${query}` : ""}`, {
    signal,
  });
}
