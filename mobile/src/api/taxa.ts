import { apiRequest } from "@/src/api/client";

export type TaxonCatalogItem = {
  taxon_id: number;
  scientific_name: string | null;
  common_name: string | null;
  iconic_taxon: string | null;
  rank?: string | null;
  ancestor_ids?: number[];
  catalog_version?: string;
};

export type TaxonSearchResponse = {
  items: TaxonCatalogItem[];
};

export type TaxonPackManifest = {
  pack_id: string;
  version: string;
  scope: string;
  checksum_sha256: string;
  size_bytes: number;
  taxon_count: number;
  download_url: string;
  expires_at: string;
};

/** Search the project-owned canonical catalog; this never calls iNaturalist. */
export function searchTaxa(
  query: string,
  signal?: AbortSignal,
): Promise<TaxonSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: "20" });
  return apiRequest<TaxonSearchResponse>(`/v1/taxa/search?${params.toString()}`, {
    signal,
  });
}

export function getTaxonPackManifest(packId: string): Promise<TaxonPackManifest> {
  return apiRequest<TaxonPackManifest>(`/v1/taxa/packs/${encodeURIComponent(packId)}`);
}
