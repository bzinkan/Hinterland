import { apiRequest } from "@/src/api/client";
import type { CvSuggestion } from "@/src/api/observations";

// ---------------------------------------------------------------------------
// GET /v1/photos/{id}/url
//
// Short-lived (5 min) signed GET URL for rendering a photo. The server
// signs whatever bucket/object the Photo row currently points at, so this
// works across the moderation lifecycle: pending/ before moderation,
// observations/ after a clean decision, quarantine/ for adult review.
// Callers cache via usePhotoUrl (staleTime under the SAS TTL) rather than
// storing URLs -- a stored URL is a future 403.
// ---------------------------------------------------------------------------

export type PhotoUrlResponse = {
  photo_id: string;
  url: string;
  expires_at: string;
};

export function getPhotoUrl(photoId: string): Promise<PhotoUrlResponse> {
  return apiRequest<PhotoUrlResponse>(`/v1/photos/${photoId}/url`);
}

export type PhotoIdentifyResponse = {
  photo_id: string;
  suggestions: CvSuggestion[];
  cv_unavailable: boolean;
  no_matches: boolean;
};

export function identifyPhoto(photoId: string): Promise<PhotoIdentifyResponse> {
  return apiRequest<PhotoIdentifyResponse>(`/v1/photos/${photoId}/identify`, {
    method: "POST",
  });
}
