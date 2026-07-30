import { apiRequest } from "@/src/api/client";
import type { ReleaseLane } from "@/src/config/env";

export type ApiMeta = {
  name: string;
  env: string;
  release_lane: ReleaseLane;
  version: string;
  capabilities?: {
    observation?: {
      photo_helper_enabled?: boolean;
    };
    groups?: {
      shared_groups_enabled?: boolean;
    };
    integrations?: {
      content_safety_enabled?: boolean;
      public_inat_submission_enabled?: boolean;
    };
  };
};

export function getApiMeta(signal?: AbortSignal): Promise<ApiMeta> {
  return apiRequest<ApiMeta>("/v1/meta", { unauthenticated: true, signal });
}
