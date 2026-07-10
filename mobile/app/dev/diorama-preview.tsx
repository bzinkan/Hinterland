import { Stack } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
} from "react-native";

import { Text as ThemedText, View as ThemedView } from "@/components/Themed";
import type { SanctuaryZoneId } from "@/src/api/sanctuary";
import { makeSampleSnapshot } from "@/src/sanctuary/diorama/dev/sampleSnapshot";
import { BiomeChooserScreen } from "@/src/sanctuary/dioramaui/BiomeChooserScreen";
import { BiomeScene } from "@/src/sanctuary/dioramaui/BiomeSceneScreen";

type SkiaModule = typeof import("@shopify/react-native-skia");

/** Depth tiers the sample snapshot understands (see sampleSnapshot.ts). */
const TIERS = [0, 1, 3, 5, 10, 20, 50] as const;

/**
 * Dev-only diorama preview (ADR 0012 addendum): the REAL chooser + biome
 * scene flow rendered from the deterministic sample snapshot -- no auth,
 * no network. This is the eyeball/taste-pass harness: step the tier chips
 * to see dormant scenes with silhouettes (tier 0), first wakes (step 0 ->
 * 1 while inside a scene), and fully grown biomes; tap a card to enter
 * its full-bleed scene, meadow first for the real backdrop art. Reach it
 * at exp+hinterland://dev/diorama-preview. Same guarded require as
 * skia-smoke so pre-rebuild clients see a note instead of crashing.
 */
export default function DioramaPreviewScreen() {
  let skia: SkiaModule | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    skia = require("@shopify/react-native-skia");
  } catch {
    skia = null;
  }

  if (skia === null) {
    return (
      <ThemedView style={styles.center}>
        <Stack.Screen options={{ title: "Diorama preview" }} />
        <ThemedText style={styles.heading}>Skia native module missing</ThemedText>
        <ThemedText style={styles.body}>
          This dev client predates the D3 native rebuild. Run a fresh
          development build and reinstall it, then revisit this screen.
        </ThemedText>
      </ThemedView>
    );
  }

  return <PreviewBody skia={skia} />;
}

function PreviewBody({ skia }: { skia: SkiaModule }) {
  const [tier, setTier] = useState<number>(20);
  const [zoneId, setZoneId] = useState<SanctuaryZoneId | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const onLayout = useCallback((e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize((prev) => prev ?? (width > 0 && height > 0 ? { w: width, h: height } : prev));
  }, []);

  const snapshot = useMemo(() => makeSampleSnapshot(tier), [tier]);
  const onFirstFrame = useCallback(() => {}, []);
  const onBack = useCallback(() => setZoneId(null), []);

  return (
    <View style={styles.root} onLayout={onLayout}>
      <Stack.Screen options={{ title: "Diorama preview" }} />
      {zoneId === null ? (
        <BiomeChooserScreen
          snapshot={snapshot}
          onOpenZone={setZoneId}
          bottomInset={130}
        />
      ) : size ? (
        <BiomeScene
          key={zoneId}
          skia={skia}
          w={size.w}
          h={size.h}
          snapshot={snapshot}
          zoneId={zoneId}
          onBack={onBack}
          onFirstFrame={onFirstFrame}
        />
      ) : null}
      <View style={styles.tierRow} pointerEvents="box-none">
        {TIERS.map((t) => (
          <Pressable
            key={t}
            style={[styles.tierChip, t === tier && styles.tierChipActive]}
            onPress={() => setTier(t)}
          >
            <Text style={styles.tierText}>{t}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0B0F0D" },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.7 },
  tierRow: {
    // Clear the edge-to-edge system nav bar; this is a dev-only overlay.
    position: "absolute",
    bottom: 110,
    alignSelf: "center",
    flexDirection: "row",
    gap: 6,
  },
  tierChip: {
    minWidth: 44,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: "rgba(11, 15, 13, 0.72)",
  },
  tierChipActive: { backgroundColor: "rgba(63, 107, 64, 0.95)" },
  tierText: { color: "#EDEFEA", fontSize: 13, fontWeight: "600" },
});
