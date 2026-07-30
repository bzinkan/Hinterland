import { useQuery } from "@tanstack/react-query";

import { getApiMeta, type ApiMeta } from "@/src/api/meta";

export function sharedGroupsIsEnabled(meta: ApiMeta | null | undefined): boolean {
  return meta?.capabilities?.groups?.shared_groups_enabled === true;
}

/** Shared-group invitations fail closed when metadata is absent or unavailable. */
export function useGroupCapabilities() {
  const query = useQuery({
    queryKey: ["meta", "group-capabilities"],
    queryFn: ({ signal }) => getApiMeta(signal),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  return {
    sharedGroupsEnabled: query.isSuccess && sharedGroupsIsEnabled(query.data),
    query,
  };
}
