import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/src/api/client";

/**
 * Single QueryClient for the app.
 *
 * Defaults are tuned for Hinterland's mobile shape:
 * - `staleTime: 30s` -- list screens don't refetch on every navigation
 * - `retry: false` on 4xx (don't loop on auth/validation errors); retry once on 5xx + network
 * - `refetchOnWindowFocus: false` -- doesn't apply on RN, but kills it on web
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 1;
      },
    },
  },
});
