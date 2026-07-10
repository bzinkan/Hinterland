import { useQuery } from "@tanstack/react-query";

import { getObservation } from "@/src/api/observations";
import { useAuthSession } from "@/src/auth/session";

export function useObservationDetail(observationId: string | null) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  return useQuery({
    queryKey: ["observations", ownerUserId ?? "anonymous", "detail", observationId],
    queryFn: ({ signal }) => getObservation(observationId ?? "", signal),
    enabled: ownerUserId != null && observationId !== null,
  });
}
