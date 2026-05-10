import { router, Stack } from "expo-router";
import * as Location from "expo-location";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  TextInput,
} from "react-native";

import { Text, View } from "@/components/Themed";
import {
  createObservation,
  presignPhoto,
} from "@/src/api/observations";
import { putPhotoToSignedUrl } from "@/src/api/upload";
import { ApiError } from "@/src/api/client";
import { useDraftStore } from "@/src/observation/draftStore";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; step: "presign" | "put" | "create" }
  | { kind: "success"; observationId: string }
  | { kind: "error"; message: string };

export default function ObserveSubmitScreen() {
  const photo = useDraftStore((s) => s.photo);
  const clearDraft = useDraftStore((s) => s.clear);

  const [locStatus, setLocStatus] = useState<
    "loading" | "ready" | "denied" | "error"
  >("loading");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(
    null,
  );
  const [speciesName, setSpeciesName] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (cancelled) return;
      if (!perm.granted) {
        setLocStatus("denied");
        return;
      }
      try {
        const pos = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (cancelled) return;
        setCoords({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        setLocStatus("ready");
      } catch {
        if (cancelled) return;
        setLocStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!photo) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Submit" }} />
        <Text style={styles.body}>
          No photo in draft. Go back to Observe and capture one.
        </Text>
        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.back()}
        >
          <Text style={styles.buttonText}>Back</Text>
        </Pressable>
      </View>
    );
  }

  const submittable =
    phase.kind === "idle" && locStatus === "ready" && coords !== null;

  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: "Submit" }} />
      <Image
        source={{ uri: photo.localUri }}
        style={styles.thumb}
        resizeMode="cover"
      />

      <Text style={styles.label}>Location</Text>
      {locStatus === "loading" && (
        <View style={styles.row}>
          <ActivityIndicator />
          <Text style={styles.value}>getting your location…</Text>
        </View>
      )}
      {locStatus === "ready" && coords && (
        <Text style={styles.value}>
          {coords.lat.toFixed(4)}, {coords.lng.toFixed(4)}
        </Text>
      )}
      {locStatus === "denied" && (
        <Text style={styles.error}>
          Location permission denied. Open the OS settings, enable location for
          Dragonfly, then come back.
        </Text>
      )}
      {locStatus === "error" && (
        <Text style={styles.error}>
          Couldn&apos;t read location. Try again outdoors / with a clear sky
          view.
        </Text>
      )}

      <Text style={styles.label}>Species (optional)</Text>
      <TextInput
        style={styles.input}
        value={speciesName}
        onChangeText={setSpeciesName}
        placeholder="e.g. Northern Cardinal"
        placeholderTextColor="#999"
        autoCapitalize="words"
      />

      {phase.kind === "uploading" && (
        <View style={styles.row}>
          <ActivityIndicator />
          <Text style={styles.value}>{stepLabel(phase.step)}…</Text>
        </View>
      )}
      {phase.kind === "success" && (
        <Text style={styles.success}>● Submitted! id: {phase.observationId}</Text>
      )}
      {phase.kind === "error" && <Text style={styles.error}>● {phase.message}</Text>}

      <View style={styles.actions}>
        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.back()}
        >
          <Text style={styles.buttonText}>Cancel</Text>
        </Pressable>
        <Pressable
          style={[
            styles.button,
            styles.buttonPrimary,
            !submittable && styles.buttonDisabled,
          ]}
          disabled={!submittable}
          onPress={async () => {
            if (!coords) return;
            try {
              setPhase({ kind: "uploading", step: "presign" });
              const presigned = await presignPhoto();

              setPhase({ kind: "uploading", step: "put" });
              await putPhotoToSignedUrl(presigned.upload_url, photo.localUri);

              setPhase({ kind: "uploading", step: "create" });
              const obs = await createObservation({
                photo_id: presigned.photo_id,
                latitude: coords.lat,
                longitude: coords.lng,
                species_name: speciesName.trim() || null,
              });

              clearDraft();
              setPhase({ kind: "success", observationId: obs.id });
            } catch (err) {
              setPhase({
                kind: "error",
                message:
                  err instanceof ApiError
                    ? `${err.status}: ${err.message}`
                    : err instanceof Error
                      ? err.message
                      : String(err),
              });
            }
          }}
        >
          <Text style={styles.buttonText}>
            {phase.kind === "success" ? "Submitted ✓" : "Submit"}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

function stepLabel(step: "presign" | "put" | "create"): string {
  switch (step) {
    case "presign":
      return "Requesting upload URL";
    case "put":
      return "Uploading photo";
    case "create":
      return "Saving observation";
  }
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 16,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  thumb: {
    width: "100%",
    height: 240,
    borderRadius: 6,
    marginBottom: 12,
  },
  label: {
    fontSize: 13,
    fontWeight: "600",
    opacity: 0.7,
    marginTop: 12,
  },
  value: {
    fontSize: 14,
    marginTop: 4,
  },
  body: {
    fontSize: 14,
    opacity: 0.8,
    marginBottom: 16,
    textAlign: "center",
  },
  error: {
    fontSize: 14,
    color: "#ef4444",
    marginTop: 4,
  },
  success: {
    fontSize: 14,
    color: "#22c55e",
    marginTop: 12,
  },
  input: {
    width: "100%",
    height: 40,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 8,
    marginTop: 8,
    fontSize: 14,
    color: "#fff",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 8,
  },
  actions: {
    flexDirection: "row",
    gap: 12,
    marginTop: 24,
  },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    flex: 1,
    alignItems: "center",
  },
  buttonPrimary: {
    backgroundColor: "#2f6feb",
  },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
});
