import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";

import {
  listQueuedObservations,
  subscribeObservationQueue,
} from "@/src/observation/observationQueue";
import type { QueuedObservation } from "@/src/observation/queueTypes";

/** Owner-scoped queue view with stale-read suppression during account swaps. */
export function useObservationQueue(ownerUserId: string | null) {
  const [items, setItems] = useState<QueuedObservation[]>([]);
  const [loading, setLoading] = useState(ownerUserId != null);
  const requestGeneration = useRef(0);
  const activeOwnerUserId = useRef(ownerUserId);
  const itemsOwnerUserId = useRef<string | null>(null);
  // Update during render so a retained callback from the previous account is
  // invalid before effects (and their cleanup) get a chance to run.
  activeOwnerUserId.current = ownerUserId;

  const reload = useCallback(async () => {
    const requestedOwnerUserId = ownerUserId;
    if (activeOwnerUserId.current !== requestedOwnerUserId) return;
    const generation = ++requestGeneration.current;
    if (!requestedOwnerUserId || Platform.OS === "web") {
      itemsOwnerUserId.current = requestedOwnerUserId;
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const next = await listQueuedObservations(requestedOwnerUserId);
      if (
        generation === requestGeneration.current &&
        activeOwnerUserId.current === requestedOwnerUserId
      ) {
        itemsOwnerUserId.current = requestedOwnerUserId;
        setItems(next);
      }
    } catch {
      if (
        generation === requestGeneration.current &&
        activeOwnerUserId.current === requestedOwnerUserId
      ) {
        itemsOwnerUserId.current = requestedOwnerUserId;
        setItems([]);
      }
    } finally {
      if (
        generation === requestGeneration.current &&
        activeOwnerUserId.current === requestedOwnerUserId
      ) {
        setLoading(false);
      }
    }
  }, [ownerUserId]);

  useEffect(() => {
    // Clear synchronously before the new owner's async SQLite read starts.
    requestGeneration.current += 1;
    itemsOwnerUserId.current = ownerUserId;
    setItems([]);
    setLoading(ownerUserId != null && Platform.OS !== "web");
    void reload();
    return subscribeObservationQueue(() => {
      void reload();
    });
  }, [ownerUserId, reload]);

  const ownerMatches = itemsOwnerUserId.current === ownerUserId;
  return {
    items: ownerMatches ? items : [],
    loading:
      ownerMatches ? loading : ownerUserId != null && Platform.OS !== "web",
    reload,
  };
}
