/**
 * Sanctuary reveal -- post-submit "something changed" beat.
 *
 * Renders when ``createObservation()`` returns at least one ``world_unlock``
 * or ``world_evolution`` reward (per ``docs/sanctuary.md`` section 10
 * "new-arrival reveal"). Pure data renderer: every visible string except
 * the modal chrome comes from the dispatcher-returned reward, so the
 * client never fabricates a Sanctuary unlock (sanctuary.md §10 "no
 * fabricated rewards").
 *
 * Safety posture:
 * - No precise location text. The reward payload may carry ``zone`` /
 *   ``element_id`` / ``threshold`` / ``taxon_id`` / ``tier_hint`` per
 *   the WorldHandler shape; this component only renders ``title`` /
 *   ``detail`` / ``icon`` (a string asset key) -- no payload field.
 * - No social copy. No share / friend / DM / like / follow.
 * - No streak / FOMO copy.
 * - No auto-dismiss timer. The kid can sit on the reveal for as long as
 *   they want; it dismisses on button tap or backdrop tap only.
 * - No auto-navigation -- "See Sanctuary" is opt-in (sanctuary.md §10
 *   "no auto-navigation, no hijacking the post-submit flow").
 */

import React from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";

import type { ObservationReward } from "@/src/api/observations";

export type SanctuaryRevealModalProps = {
  visible: boolean;
  reward: ObservationReward | null;
  extraRewardCount: number;
  onSeeSanctuary: () => void;
  onDone: () => void;
};

export function SanctuaryRevealModal({
  visible,
  reward,
  extraRewardCount,
  onSeeSanctuary,
  onDone,
}: SanctuaryRevealModalProps) {
  // ``Modal`` rendering with ``visible=false`` keeps the tree mounted but
  // skips presentation -- the parent can keep state in place across the
  // submit flow without re-creating this component.
  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={onDone}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Close reveal"
        style={styles.backdrop}
        onPress={onDone}
      >
        {/* Inner Pressable swallows taps so the backdrop close handler
            is not invoked when the kid taps inside the card. React
            Native has no DOM-style event.stopPropagation; the child
            Pressable consumes the gesture before the parent sees it. */}
        <Pressable
          accessibilityRole="none"
          style={styles.card}
          onPress={() => {
            /* intentional no-op: swallow taps inside the card */
          }}
        >
          <Text style={styles.eyebrow}>Sanctuary</Text>
          <Text style={styles.header}>Something changed in your Sanctuary</Text>

          {reward ? (
            <>
              <Text style={styles.rewardTitle}>{reward.title}</Text>
              {reward.detail ? (
                <Text style={styles.rewardDetail}>{reward.detail}</Text>
              ) : null}
              <Text style={styles.iconKey}>{reward.icon}</Text>
            </>
          ) : null}

          {extraRewardCount > 0 ? (
            <Text style={styles.extras}>
              {extraRewardCount === 1
                ? "+ 1 more change"
                : `+ ${extraRewardCount} more changes`}
            </Text>
          ) : null}

          <View style={styles.buttonRow}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Done"
              style={({ pressed }) => [
                styles.button,
                styles.buttonSecondary,
                pressed ? styles.buttonPressed : null,
              ]}
              onPress={onDone}
            >
              <Text style={styles.buttonSecondaryText}>Done</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="See Sanctuary"
              style={({ pressed }) => [
                styles.button,
                styles.buttonPrimary,
                pressed ? styles.buttonPressed : null,
              ]}
              onPress={onSeeSanctuary}
            >
              <Text style={styles.buttonPrimaryText}>See Sanctuary</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
    justifyContent: "center",
    alignItems: "center",
    padding: 24,
  },
  card: {
    backgroundColor: "#FFFFFF",
    borderRadius: 12,
    padding: 20,
    width: "100%",
    maxWidth: 360,
    gap: 8,
  },
  eyebrow: {
    fontSize: 11,
    fontWeight: "600",
    color: "#7BA45A",
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  header: {
    fontSize: 18,
    fontWeight: "600",
    color: "#2A2A2A",
    marginBottom: 4,
  },
  rewardTitle: {
    fontSize: 16,
    fontWeight: "500",
    color: "#2A2A2A",
    marginTop: 4,
  },
  rewardDetail: {
    fontSize: 14,
    color: "#3A3A3A",
    lineHeight: 20,
    marginTop: 2,
  },
  iconKey: {
    fontSize: 11,
    color: "#888",
    fontFamily: "Courier",
    marginTop: 6,
  },
  extras: {
    fontSize: 12,
    color: "#666",
    marginTop: 6,
  },
  buttonRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 16,
    justifyContent: "flex-end",
  },
  button: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 8,
  },
  buttonPressed: { opacity: 0.7 },
  buttonPrimary: { backgroundColor: "#3F6B40" },
  buttonPrimaryText: { color: "#FFFFFF", fontSize: 14, fontWeight: "500" },
  buttonSecondary: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: "#CCCCCC",
  },
  buttonSecondaryText: { color: "#444", fontSize: 14, fontWeight: "500" },
});
