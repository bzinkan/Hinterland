/**
 * Biome chooser: the Sanctuary tab's landing view under the diorama flag
 * (ADR 0012 addendum -- the kid picks a destination, then enters its
 * full-bleed scene; the archipelago vista is retired as the default).
 *
 * Deliberately a NATIVE RN scrollable list, not a canvas: seven large
 * biome cards, palette-derived colors per zone (dormant cards bake the
 * same desaturation the scene art uses), awake cards show depth-tier
 * progress, dormant cards a calm mystery hint. 44dp++ targets, full
 * TalkBack labels, plain ScrollView -- kid-first and warm, nothing
 * clever. All state derivation is pure (biomeCards.ts, unit-tested).
 */

import { useMemo } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type {
  SanctuarySnapshotDto,
  SanctuaryZoneId,
} from "@/src/api/sanctuary";
import {
  deriveBiomeCards,
  TIER_LADDER,
  type BiomeCardModel,
} from "@/src/sanctuary/dioramaui/biomeCards";

export function BiomeChooserScreen({
  snapshot,
  onOpenZone,
  bottomInset = 0,
}: {
  snapshot: SanctuarySnapshotDto;
  onOpenZone: (zoneId: SanctuaryZoneId) => void;
  /** Extra scroll padding (dev harness overlays live above the nav bar). */
  bottomInset?: number;
}) {
  const cards = useMemo(() => deriveBiomeCards(snapshot), [snapshot]);
  const guide = snapshot.guide_message?.text ?? null;

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        bottomInset > 0 ? { paddingBottom: 24 + bottomInset } : null,
      ]}
    >
      <Text style={styles.heading}>Your Sanctuary</Text>
      {guide !== null ? <Text style={styles.guide}>{guide}</Text> : null}
      {cards.map((card) => (
        <BiomeCard key={card.zoneId} card={card} onOpenZone={onOpenZone} />
      ))}
    </ScrollView>
  );
}

function BiomeCard({
  card,
  onOpenZone,
}: {
  card: BiomeCardModel;
  onOpenZone: (zoneId: SanctuaryZoneId) => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={card.a11yLabel}
      accessibilityHint="Opens this biome"
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: card.colors.background },
        pressed && styles.cardPressed,
      ]}
      onPress={() => onOpenZone(card.zoneId)}
    >
      <View style={styles.cardBody}>
        <Text style={[styles.cardTitle, card.dormant && styles.cardTitleDormant]}>
          {card.title}
        </Text>
        {card.dormant ? (
          <View style={styles.stateRow}>
            <Text style={styles.dormantText}>Still sleeping</Text>
            {card.cueCount > 0 ? (
              <View style={[styles.cueBadge, { borderColor: card.colors.accent }]}>
                <Text style={[styles.cueBadgeText, { color: card.colors.accent }]}>
                  ? {card.cueCount}
                </Text>
              </View>
            ) : null}
          </View>
        ) : (
          <View style={styles.stateRow}>
            <View style={styles.tierDots}>
              {TIER_LADDER.slice(1).map((threshold, i) => (
                <View
                  key={threshold}
                  style={[
                    styles.tierDot,
                    {
                      backgroundColor:
                        i < card.tierIndex ? card.colors.accent : "rgba(0,0,0,0.12)",
                    },
                  ]}
                />
              ))}
            </View>
            <Text style={[styles.tierText, { color: card.colors.accent }]}>
              depth {card.depthTier}
            </Text>
          </View>
        )}
      </View>
      <Text style={[styles.chevron, { color: card.colors.accent }]}>›</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F2EA" },
  content: { padding: 16, paddingBottom: 24, gap: 12 },
  heading: { fontSize: 24, fontWeight: "700", color: "#2A332B", marginTop: 8 },
  guide: { fontSize: 14, color: "#59635A", lineHeight: 20, marginBottom: 4 },
  card: {
    minHeight: 92,
    borderRadius: 18,
    paddingHorizontal: 18,
    paddingVertical: 16,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  cardPressed: { opacity: 0.85 },
  cardBody: { flex: 1, gap: 8 },
  cardTitle: { fontSize: 19, fontWeight: "700", color: "#26302A" },
  cardTitleDormant: { color: "#4E534E" },
  stateRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  dormantText: { fontSize: 13, color: "#6B6F69", fontStyle: "italic" },
  cueBadge: {
    borderWidth: 1.5,
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 2,
  },
  cueBadgeText: { fontSize: 13, fontWeight: "700" },
  tierDots: { flexDirection: "row", gap: 5, alignItems: "center" },
  tierDot: { width: 9, height: 9, borderRadius: 5 },
  tierText: { fontSize: 13, fontWeight: "600" },
  chevron: { fontSize: 28, fontWeight: "600" },
});
