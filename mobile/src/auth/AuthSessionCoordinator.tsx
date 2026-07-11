import NetInfo from "@react-native-community/netinfo";
import { useQueryClient } from "@tanstack/react-query";
import { AppState, Platform } from "react-native";
import { useEffect, useRef } from "react";

import { CurrentUserContractError, getMe } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import { getBearerToken, subscribeBearerTokenChanges } from "@/src/auth/token";
import { rotateImperativeRequestBoundary } from "@/src/auth/requestBoundary";
import {
  clearPersistedSessionUser,
  getPersistedSessionUser,
  persistSessionUser,
  useAuthSession,
} from "@/src/auth/session";
import {
  resumeOwnerObservationQueue,
  setObservationQueueOwner,
} from "@/src/observation/queueSync";
import { useDraftStore } from "@/src/observation/draftStore";

/**
 * Makes account changes a privacy boundary: requests are cancelled, cached
 * observations/photo URLs are removed, then the canonical /me identity is
 * resolved before child surfaces render again.
 */
export function AuthSessionCoordinator() {
  const queryClient = useQueryClient();
  const setInitializing = useAuthSession((state) => state.setInitializing);
  const setAnonymous = useAuthSession((state) => state.setAnonymous);
  const setAuthenticated = useAuthSession((state) => state.setAuthenticated);
  const refreshGeneration = useRef(0);
  const authController = useRef<AbortController | null>(null);

  useEffect(() => {
    async function refresh(allowOfflineSnapshot: boolean): Promise<void> {
      const generation = ++refreshGeneration.current;
      rotateImperativeRequestBoundary();
      authController.current?.abort();
      const controller = new AbortController();
      authController.current = controller;
      setInitializing();
      setObservationQueueOwner(null);
      useDraftStore.getState().clear();
      await queryClient.cancelQueries();
      queryClient.clear();
      const token = await getBearerToken();
      if (generation !== refreshGeneration.current) return;
      if (!token) {
        setObservationQueueOwner(null);
        await clearPersistedSessionUser();
        setAnonymous();
        return;
      }
      try {
        const user = await getMe(controller.signal);
        if (generation === refreshGeneration.current) {
          await persistSessionUser(user, token);
          if (generation === refreshGeneration.current) {
            setObservationQueueOwner(user.id);
            setAuthenticated(user);
          }
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        const authRejected =
          error instanceof ApiError && (error.status === 401 || error.status === 403);
        const contractRejected = error instanceof CurrentUserContractError;
        const retryableOfflineFailure = canRestoreOfflineIdentity(error);
        const cached =
          allowOfflineSnapshot && retryableOfflineFailure
            ? await getPersistedSessionUser(token)
            : null;
        if (generation === refreshGeneration.current) {
          if (cached) {
            setObservationQueueOwner(cached.id);
            setAuthenticated(cached);
          }
          else {
            if (authRejected || contractRejected || !retryableOfflineFailure) {
              await clearPersistedSessionUser();
            }
            if (generation === refreshGeneration.current) setAnonymous();
          }
        }
      }
    }

    void refresh(true);
    const unsubscribe = subscribeBearerTokenChanges((change) => {
      setObservationQueueOwner(null);
      if (change === "cleared") void clearPersistedSessionUser();
      void refresh(false);
    });
    return () => {
      refreshGeneration.current += 1;
      authController.current?.abort();
      setObservationQueueOwner(null);
      unsubscribe();
    };
  }, [queryClient, setAnonymous, setAuthenticated, setInitializing]);

  return null;
}

/** Only genuine transport/server outages may reuse a token-bound offline snapshot. */
export function canRestoreOfflineIdentity(error: unknown): boolean {
  if (error instanceof CurrentUserContractError) return false;
  if (error instanceof ApiError) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return error instanceof TypeError;
}

/** Foreground-only W1 recovery for submissions whose payload is frozen. */
export function ObservationQueueCoordinator() {
  const queryClient = useQueryClient();
  const userId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const activeRun = useRef<{
    ownerUserId: string;
    promise: Promise<void>;
  } | null>(null);

  useEffect(() => {
    if (!userId || Platform.OS === "web") return;
    const activeUserId = userId;
    let cancelled = false;

    function resume(): void {
      if (activeRun.current?.ownerUserId === activeUserId) return;
      const promise = resumeOwnerObservationQueue(activeUserId)
        .then(async (records) => {
          if (
            !cancelled &&
            records.some((record) => record.stage === "complete")
          ) {
            await Promise.all([
              queryClient.invalidateQueries({ queryKey: ["observations", activeUserId] }),
              queryClient.invalidateQueries({ queryKey: ["dex", activeUserId] }),
              queryClient.invalidateQueries({ queryKey: ["expeditions", activeUserId] }),
              queryClient.invalidateQueries({ queryKey: ["sanctuary", activeUserId] }),
            ]);
          }
        })
        .finally(() => {
          if (activeRun.current?.promise === promise) activeRun.current = null;
        });
      activeRun.current = { ownerUserId: activeUserId, promise };
    }

    resume();
    const appState = AppState.addEventListener("change", (state) => {
      if (state === "active") resume();
    });
    const network = NetInfo.addEventListener((state) => {
      if (state.isConnected && state.isInternetReachable !== false) resume();
    });
    const retryTimer = setInterval(() => {
      if (AppState.currentState === "active") resume();
    }, 30_000);
    return () => {
      cancelled = true;
      appState.remove();
      network();
      clearInterval(retryTimer);
    };
  }, [queryClient, userId]);

  return null;
}
