import { Stack } from "expo-router";
import { StyleSheet } from "react-native";

import { Text, View } from "@/components/Themed";

/**
 * Dev-only Skia smoke screen (D3 of the Sanctuary diorama plan, ADR 0012).
 *
 * Purpose: prove the freshly built dev client carries the
 * @shopify/react-native-skia native module before any diorama work
 * begins, and host the earliest D4 spike experiments. Not linked from
 * any navigator -- reach it by typing the route in the dev client
 * (exp+hinterland://dev/skia-smoke) or via the router dev menu.
 *
 * The Skia import is a guarded require() rather than a top-level
 * import on purpose: expo-router registers every route at bundle time,
 * so a top-level import would crash a dev client that predates the
 * native rebuild at app START, not when visiting this screen. With the
 * guard, an old client renders a "rebuild needed" note instead.
 */
export default function SkiaSmokeScreen() {
  let skia: typeof import("@shopify/react-native-skia") | null = null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    skia = require("@shopify/react-native-skia");
  } catch {
    skia = null;
  }

  if (skia === null) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Skia smoke" }} />
        <Text style={styles.heading}>Skia native module missing</Text>
        <Text style={styles.body}>
          This dev client predates the D3 native rebuild. Run a fresh
          development build (eas build --profile development) and
          reinstall it, then revisit this screen.
        </Text>
      </View>
    );
  }

  const { Canvas, Circle, Rect, LinearGradient, vg, Group } = {
    Canvas: skia.Canvas,
    Circle: skia.Circle,
    Rect: skia.Rect,
    LinearGradient: skia.LinearGradient,
    vg: skia.vec,
    Group: skia.Group,
  };

  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: "Skia smoke" }} />
      <Text style={styles.heading}>Skia canvas smoke</Text>
      <Text style={styles.body}>
        A sky gradient and a sun circle drawn by Skia. If you can see
        them, the native module is alive and D4 spike work can start.
      </Text>
      <Canvas style={styles.canvas}>
        <Group>
          <Rect x={0} y={0} width={340} height={220}>
            <LinearGradient
              start={vg(0, 0)}
              end={vg(0, 220)}
              colors={["#7fb2e5", "#eaf3d2"]}
            />
          </Rect>
          <Circle cx={260} cy={60} r={28} color="#f5d76e" />
          <Circle cx={90} cy={190} r={70} color="#5d9448" />
          <Circle cx={180} cy={205} r={55} color="#487a38" />
        </Group>
      </Canvas>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.7, marginBottom: 16 },
  canvas: { width: 340, height: 220, borderRadius: 8 },
});
