/**
 * Runtime feature-flag resolution. Currently: the Sanctuary 2.5D diorama
 * (ADR 0011/0012).
 *
 * Layering: build-time SANCTUARY_DIORAMA (eas.json -> app.config.ts extra)
 * is the hard gate; runtime conditions (screen reader, Simple view pref,
 * renderer crash latch) can only turn the diorama OFF, never on.
 */

import Constants from "expo-constants";
import { useEffect, useState } from "react";
import { AccessibilityInfo } from "react-native";

import { decideSanctuaryDiorama } from "@/src/sanctuary/diorama/decideSanctuaryDiorama";
import { useSanctuaryDioramaPrefs } from "@/src/sanctuary/diorama/prefs";

const extra = Constants.expoConfig?.extra as
  | { sanctuaryDiorama?: boolean }
  | undefined;

/** Build-time flag value (stable for the life of the binary). */
export const SANCTUARY_DIORAMA_BUILD_FLAG = extra?.sanctuaryDiorama === true;

function useScreenReaderEnabled(): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isScreenReaderEnabled().then((value) => {
      if (mounted) setEnabled(value);
    });
    const subscription = AccessibilityInfo.addEventListener(
      "screenReaderChanged",
      setEnabled,
    );
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);
  return enabled;
}

/** True when the Sanctuary tab should render the diorama. D7 consumes this from the Sanctuary screen; nothing calls it yet. */
export function useSanctuaryDioramaFlag(): boolean {
  const screenReaderEnabled = useScreenReaderEnabled();
  const simpleViewPreferred = useSanctuaryDioramaPrefs((s) => s.simpleView);
  const crashCount = useSanctuaryDioramaPrefs((s) => s.crashCount);
  return decideSanctuaryDiorama({
    buildFlagEnabled: SANCTUARY_DIORAMA_BUILD_FLAG,
    screenReaderEnabled,
    simpleViewPreferred,
    crashCount,
  });
}
