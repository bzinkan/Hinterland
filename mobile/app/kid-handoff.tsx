import { useQueryClient } from "@tanstack/react-query";
import { CameraView, useCameraPermissions } from "expo-camera";
import { router, Stack } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { kidExchange } from "@/src/api/auth";
import { setBearerToken } from "@/src/auth/token";

type HandoffPayload = {
  v: 1;
  kind: "hinterland.kid-handoff.v1";
  handoff_token: string;
};

export default function KidHandoffScreen() {
  const [permission, requestPermission] = useCameraPermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  async function handleBarcodeScanned(event: { data: string }) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const payload = parseHandoff(event.data);
      const result = await kidExchange(payload.handoff_token);
      await setBearerToken(result.session_token);
      await queryClient.invalidateQueries();
      router.replace("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (!permission) {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Kid sign in" }} />
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      </DesktopContainer>
    );
  }

  if (!permission.granted) {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Kid sign in" }} />
        <View style={styles.container}>
          <Text style={styles.title}>Scan your QR code</Text>
          <Text style={styles.subtitle}>
            Ask your adult to show the kid handoff QR from Classroom.
          </Text>
          {error && <Text style={styles.error}>{error}</Text>}
          <Pressable style={[styles.button, styles.buttonPrimary]} onPress={requestPermission}>
            <Text style={styles.buttonText}>Allow camera</Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Kid sign in" }} />
      <View style={styles.container}>
        <Text style={styles.title}>Scan your QR code</Text>
        <Text style={styles.subtitle}>
          Hold the camera over the code from your adult's screen.
        </Text>
        <View style={styles.cameraWrap}>
          <CameraView
            style={styles.camera}
            facing="back"
            barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
            onBarcodeScanned={busy ? undefined : handleBarcodeScanned}
          />
          {busy && (
            <View style={styles.busyOverlay}>
              <ActivityIndicator color="#fff" />
              <Text style={styles.busyText}>Signing in...</Text>
            </View>
          )}
        </View>
        {error && <Text style={styles.error}>{error}</Text>}
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={() => router.back()}>
          <Text style={styles.buttonText}>Back</Text>
        </Pressable>
      </View>
    </DesktopContainer>
  );
}

function parseHandoff(raw: string): HandoffPayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (exc) {
    throw new Error("That QR code is not a Hinterland kid sign-in code.", { cause: exc });
  }
  if (
    typeof parsed !== "object" ||
    parsed == null ||
    (parsed as { v?: unknown }).v !== 1 ||
    (parsed as { kind?: unknown }).kind !== "hinterland.kid-handoff.v1" ||
    typeof (parsed as { handoff_token?: unknown }).handoff_token !== "string"
  ) {
    throw new Error("That QR code is not a Hinterland kid sign-in code.");
  }
  return parsed as HandoffPayload;
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 22, fontWeight: "600", marginBottom: 6 },
  subtitle: { fontSize: 13, opacity: 0.7, marginBottom: 20 },
  cameraWrap: {
    width: "100%",
    aspectRatio: 1,
    overflow: "hidden",
    borderRadius: 8,
    backgroundColor: "#111",
  },
  camera: { flex: 1 },
  busyOverlay: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(0,0,0,0.55)",
  },
  busyText: { color: "#fff", marginTop: 8, fontSize: 13 },
  error: { color: "#f87171", marginTop: 12, fontSize: 13 },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    marginTop: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonText: { fontSize: 14, color: "#fff", fontWeight: "500" },
});
