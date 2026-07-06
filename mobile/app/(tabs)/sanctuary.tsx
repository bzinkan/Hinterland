/**
 * Sanctuary tab route: renders the 2.5D diorama experience (biome chooser
 * -> full-bleed biome scene, ADR 0012 addendum) when
 * decideSanctuaryDiorama says so (build flag SANCTUARY_DIORAMA + no screen
 * reader + no Simple view + crash latch below 3 strikes, all via
 * useSanctuaryDioramaFlag), else the classic text-first screen -- the
 * permanent fallback per ADR 0011/0012.
 *
 * The route waits for the prefs store to hydrate so a persisted crash
 * latch can never flash the diorama for one frame before pinning to the
 * classic screen.
 */

import React from "react";

import { useSanctuaryDioramaFlag } from "@/src/config/featureFlags";
import { useSanctuaryDioramaPrefs } from "@/src/sanctuary/diorama/prefs";
import { DioramaScreen } from "@/src/sanctuary/dioramaui/DioramaScreen";
import { Sanctuary2DScreen } from "@/src/sanctuary/Sanctuary2DScreen";

export default function SanctuaryTab() {
  const hydrated = useSanctuaryDioramaPrefs((s) => s.hydrated);
  const diorama = useSanctuaryDioramaFlag();
  if (!hydrated) return null;
  return diorama ? <DioramaScreen /> : <Sanctuary2DScreen />;
}
