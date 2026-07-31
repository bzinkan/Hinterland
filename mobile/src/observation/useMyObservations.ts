import { useInfiniteQuery } from "@tanstack/react-query";

import { listMyObservations, type ObservationListResponse } from "@/src/api/observations";
import { useAuthSession } from "@/src/auth/session";
import { listPresentationPollInterval } from "@/src/observation/moderationReconciliation";

const PAGE_SIZE = 20;

/**
 * Cursor-paginated infinite query over GET /v1/observations/me.
 *
 * Pages flatten via `data.pages.flatMap(p => p.items)`. `getNextPageParam`
 * returns the server's opaque observed-time cursor, which is passed back
 * verbatim. The mobile client never mixes this contract with legacy `before`.
 */
export function useMyObservations() {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  return useInfiniteQuery<ObservationListResponse, Error>({
    queryKey: ["observations", ownerUserId ?? "anonymous"],
    queryFn: ({ pageParam, signal }) =>
      listMyObservations({
        limit: PAGE_SIZE,
        order: "observed",
        cursor: typeof pageParam === "string" ? pageParam : null,
      }, signal),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: ownerUserId != null,
    // Poll only while a visible server row is still pending/processing.
    // Terminal/private rows fall back to the screen's ordinary 30s refresh.
    refetchInterval: (query) =>
      listPresentationPollInterval(query.state.data),
    refetchIntervalInBackground: false,
  });
}
