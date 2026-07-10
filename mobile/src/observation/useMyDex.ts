import { useInfiniteQuery } from "@tanstack/react-query";

import { listMyDex, type DexListResponse } from "@/src/api/dex";
import { useAuthSession } from "@/src/auth/session";

const PAGE_SIZE = 20;

export function useMyDex() {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  return useInfiniteQuery<DexListResponse, Error>({
    queryKey: ["dex", ownerUserId ?? "anonymous"],
    queryFn: ({ pageParam, signal }) =>
      listMyDex({
        limit: PAGE_SIZE,
        before: typeof pageParam === "string" ? pageParam : null,
      }, signal),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: ownerUserId != null,
  });
}
