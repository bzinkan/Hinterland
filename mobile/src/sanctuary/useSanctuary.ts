import { useQuery } from "@tanstack/react-query";

import { getMySanctuary, type SanctuarySnapshotDto } from "@/src/api/sanctuary";
import { useAuthSession } from "@/src/auth/session";

/**
 * TanStack Query hook over ``GET /v1/sanctuary/me``.
 *
 * The Sanctuary surface is opt-in (the kid sees it only when they open the
 * tab), so the default ``refetchOnMount`` behavior is what we want: the
 * snapshot is fresh-enough on tab focus, and pull-to-refresh forces an
 * immediate refetch via the returned ``refetch`` callable.
 */
export function useSanctuary() {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  return useQuery<SanctuarySnapshotDto, Error>({
    queryKey: ["sanctuary", ownerUserId ?? "anonymous"],
    queryFn: ({ signal }) => getMySanctuary(signal),
    enabled: ownerUserId != null,
  });
}
