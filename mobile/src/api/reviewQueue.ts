import { apiRequest } from "@/src/api/client";

export type ReviewQueueItem = {
  id: string;
  group_id: string;
  photo_id: string;
  observation_id: string | null;
  status: string;
  reason: string | null;
  created_at: string;
};

export type ReviewQueueListResponse = {
  items: ReviewQueueItem[];
  next_cursor: string | null;
};

export function listReviewQueue(): Promise<ReviewQueueListResponse> {
  return apiRequest<ReviewQueueListResponse>("/v1/review-queue?status=pending");
}

export type ResolveResponse = {
  id: string;
  status: string;
  photo_status: string | null;
};

export function approveReview(id: string): Promise<ResolveResponse> {
  return apiRequest<ResolveResponse>(`/v1/review-queue/${id}/approve`, {
    method: "POST",
  });
}

export function rejectReview(id: string): Promise<ResolveResponse> {
  return apiRequest<ResolveResponse>(`/v1/review-queue/${id}/reject`, {
    method: "POST",
  });
}

export type PhotoUrlResponse = {
  photo_id: string;
  url: string;
  expires_at: string;
};

export function getPhotoUrl(photoId: string): Promise<PhotoUrlResponse> {
  return apiRequest<PhotoUrlResponse>(`/v1/photos/${photoId}/url`);
}
