import { useInfiniteQuery } from "@tanstack/react-query";

import { listMyObservations, type ObservationListResponse } from "@/src/api/observations";

const PAGE_SIZE = 20;

/**
 * Cursor-paginated infinite query over GET /v1/observations/me.
 *
 * Pages flatten via `data.pages.flatMap(p => p.items)`. `getNextPageParam`
 * returns the server's `next_cursor`, which the next page passes back as
 * `before`.
 */
export function useMyObservations() {
  return useInfiniteQuery<ObservationListResponse, Error>({
    queryKey: ["observations", "me"],
    queryFn: ({ pageParam }) =>
      listMyObservations({
        limit: PAGE_SIZE,
        before: typeof pageParam === "string" ? pageParam : null,
      }),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
  });
}
