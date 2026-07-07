import { useQuery } from "@tanstack/react-query";

import { getObservation } from "@/src/api/observations";

export function useObservationDetail(observationId: string | null) {
  return useQuery({
    queryKey: ["observations", "detail", observationId],
    queryFn: () => getObservation(observationId ?? ""),
    enabled: observationId !== null,
  });
}
