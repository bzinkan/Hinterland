import { useQuery } from "@tanstack/react-query";

import { getSpeciesFacts } from "@/src/api/species";
import { useAuthSession } from "@/src/auth/session";

/**
 * Species fact sheet for an identified observation. The server-side
 * cache is fresh-indefinitely (ADR 0006: taxa change rarely), so a
 * session-long client cache is fine.
 */
export function useSpeciesFacts(taxonId: number | null) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  return useQuery({
    queryKey: ["species-facts", ownerUserId ?? "anonymous", taxonId],
    queryFn: () => {
      if (taxonId === null) throw new Error("useSpeciesFacts disabled without taxonId");
      return getSpeciesFacts(taxonId);
    },
    enabled: ownerUserId != null && taxonId !== null,
    staleTime: Infinity,
  });
}
