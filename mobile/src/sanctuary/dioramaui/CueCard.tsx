/**
 * Light cue card for a silhouette tap: authored mystery-cue title/detail
 * only (never reveals the answer -- docs/sanctuary.md cue copy rules).
 * Shared by the biome scene and the retired vista renderer.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";

import type { SanctuaryMysteryCueDto } from "@/src/api/sanctuary";

export function CueCard({
  cue,
  onClose,
}: {
  cue: SanctuaryMysteryCueDto;
  onClose: () => void;
}) {
  return (
    <View style={styles.cueCardWrap} pointerEvents="box-none">
      <View style={styles.cueCard}>
        <Text style={styles.cueCardTitle}>{cue.title}</Text>
        <Text style={styles.cueCardDetail}>{cue.detail}</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={styles.cueCardClose}
          onPress={onClose}
        >
          <Text style={styles.cueCardCloseText}>Close</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  cueCardWrap: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: "flex-end",
    padding: 16,
  },
  cueCard: {
    backgroundColor: "#FBF9F2",
    borderRadius: 14,
    padding: 16,
    gap: 8,
  },
  cueCardTitle: { fontSize: 16, fontWeight: "600", color: "#2A2A2A" },
  cueCardDetail: { fontSize: 14, color: "#4A4A4A", lineHeight: 20 },
  cueCardClose: {
    alignSelf: "flex-end",
    minHeight: 44,
    minWidth: 44,
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 14,
    borderRadius: 8,
    backgroundColor: "#3F6B40",
  },
  cueCardCloseText: { color: "#FFFFFF", fontSize: 14, fontWeight: "500" },
});
