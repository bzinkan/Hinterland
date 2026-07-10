import { apiRequest } from "@/src/api/client";

// ---------------------------------------------------------------------------
// GET /v1/photos/{id}/url
//
// Short-lived signed GET URL for rendering a photo. The server enforces the
// lifecycle/role matrix; kid surfaces request only clean photos.
// Callers cache via usePhotoUrl (staleTime under the SAS TTL) rather than
// storing URLs -- a stored URL is a future 403.
// ---------------------------------------------------------------------------

export type PhotoUrlResponse = {
  photo_id: string;
  url: string;
  expires_at: string;
};

export function getPhotoUrl(
  photoId: string,
  signal?: AbortSignal,
): Promise<PhotoUrlResponse> {
  return apiRequest<PhotoUrlResponse>(`/v1/photos/${photoId}/url`, { signal });
}
export function abandonPhotoReservation(
  photoId: string,
  signal?: AbortSignal,
): Promise<void> {
  return apiRequest<void>(`/v1/photos/${photoId}`, {
    method: "DELETE",
    signal,
  });
}
