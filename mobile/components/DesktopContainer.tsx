import type { PropsWithChildren } from "react";
import { Platform, StyleSheet } from "react-native";

import { View } from "@/components/Themed";

const MAX_WIDTH_WEB = 880;

/**
 * Centers + constrains content on web to a readable column. No-op on
 * native -- phones are already the right width. Used by the adult-
 * console screens (Home / Settings / Review Queue) so a parent on a
 * laptop sees a focused column instead of a stretched-edge layout.
 */
export default function DesktopContainer({ children }: PropsWithChildren) {
  if (Platform.OS !== "web") {
    return <View style={styles.native}>{children}</View>;
  }
  return (
    <View style={styles.webOuter}>
      <View style={styles.webInner}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  native: { flex: 1 },
  webOuter: { flex: 1, alignItems: "center" },
  webInner: { flex: 1, width: "100%", maxWidth: MAX_WIDTH_WEB },
});
