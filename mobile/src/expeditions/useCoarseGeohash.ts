import * as Location from "expo-location";
import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";

import { encodeGeohash4 } from "@/src/expeditions/geohash";

/**
 * Passive coarse-location cell for expedition relevance.
 *
 * CHECKS the foreground-location permission -- it NEVER requests it.
 * observe-submit is the only screen that prompts for location, and
 * keeping it that way is the risk-0007 posture (coarse-only,
 * foreground-only on play-internal). This hook just reuses a grant the
 * kid already made there. Denied, undetermined, or errored all resolve
 * to ``null`` and the expeditions list behaves exactly as before.
 *
 * Re-checks on every screen focus (not just mount) so a kid who grants
 * location during observe-submit and comes back sees ranked results
 * without an app restart -- tab screens stay mounted in expo-router.
 */
export function useCoarseGeohash(): string | null {
  const [geohash, setGeohash] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        try {
          const perm = await Location.getForegroundPermissionsAsync();
          if (cancelled || !perm.granted) return;
          // A last-known fix is free and instant; only fall back to an
          // active low-accuracy read when the OS has nothing cached.
          const pos =
            (await Location.getLastKnownPositionAsync()) ??
            (await Location.getCurrentPositionAsync({
              accuracy: Location.Accuracy.Low,
            }));
          if (cancelled) return;
          setGeohash(encodeGeohash4(pos.coords.latitude, pos.coords.longitude));
        } catch {
          // Location unavailable -- stay null; the list renders as today.
        }
      })();
      return () => {
        cancelled = true;
      };
    }, []),
  );

  return geohash;
}
