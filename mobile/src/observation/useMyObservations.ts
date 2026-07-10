import { useInfiniteQuery } from "@tanstack/react-query";

import { listMyObservations, type ObservationListResponse } from "@/src/api/observations";
import { useAuthSession } from "@/src/auth/session";

const PAGE_SIZE = 20;

/**
 * Cursor-paginated infinite query over GET /v1/observations/me.
 *
 * Pages flatten via `data.pages.flatMap(p => p.items)`. `getNextPageParam`
 * returns the server's `next_cursor`, which the next page passes back as
 * `before`.
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
        before: typeof pageParam === "string" ? pageParam : null,
      }, signal),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: ownerUserId != null,
  });
}
