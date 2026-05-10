import { apiRequest } from "@/src/api/client";

export type ReverseGeocodeResponse = {
  lat: number;
  lng: number;
  place_name: string | null;
};

export function reverseGeocode(lat: number, lng: number): Promise<ReverseGeocodeResponse> {
  const params = new URLSearchParams({
    lat: String(lat),
    lng: String(lng),
  });
  return apiRequest<ReverseGeocodeResponse>(`/v1/geocode/reverse?${params}`);
}
