import { apiRequest } from "@/src/api/client";

export type ApiMeta = {
  name: string;
  env: string;
  version: string;
  capabilities?: {
    observation?: {
      photo_helper_enabled?: boolean;
    };
    groups?: {
      shared_groups_enabled?: boolean;
    };
  };
};

export function getApiMeta(signal?: AbortSignal): Promise<ApiMeta> {
  return apiRequest<ApiMeta>("/v1/meta", { unauthenticated: true, signal });
}
