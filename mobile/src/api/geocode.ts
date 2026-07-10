import { apiRequest } from "@/src/api/client";

export type ReverseGeocodeResponse = {
  geohash4: string;
  place_name: string | null;
};

export function reverseGeocode(
  geohash4: string,
  signal?: AbortSignal,
): Promise<ReverseGeocodeResponse> {
  return apiRequest<ReverseGeocodeResponse>("/v1/geocode/reverse", {
    method: "POST",
    body: { geohash4 },
    signal,
  });
}
