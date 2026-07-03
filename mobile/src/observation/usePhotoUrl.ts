import { useQuery } from "@tanstack/react-query";

import { getPhotoUrl } from "@/src/api/photos";

/**
 * Signed GET URL for one photo, cached just under the server's 5-minute
 * SAS TTL so a rendered <Image> never holds a URL that expires mid-load,
 * and scrolling back to a card within the window reuses the same URL
 * (which also lets the native image cache hit instead of refetching
 * bytes -- the SAS query string is part of the cache key).
 */
export function usePhotoUrl(photoId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["photo-url", photoId],
    queryFn: () => getPhotoUrl(photoId),
    enabled,
    staleTime: 4 * 60 * 1000,
    gcTime: 5 * 60 * 1000,
    retry: 1,
  });
}
