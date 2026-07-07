import { useInfiniteQuery } from "@tanstack/react-query";

import { listMyDex, type DexListResponse } from "@/src/api/dex";

const PAGE_SIZE = 20;

export function useMyDex() {
  return useInfiniteQuery<DexListResponse, Error>({
    queryKey: ["dex", "me"],
    queryFn: ({ pageParam }) =>
      listMyDex({
        limit: PAGE_SIZE,
        before: typeof pageParam === "string" ? pageParam : null,
      }),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
  });
}
