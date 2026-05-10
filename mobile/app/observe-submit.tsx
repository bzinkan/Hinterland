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
import { ApiError } from "@/src/api/client";
import { reverseGeocode } from "@/src/api/geocode";
import {
  type CvSuggestion,
  createObservation,
  identifyObservation,
  patchObservation,
  presignPhoto,
} from "@/src/api/observations";
import { putPhotoToSignedUrl } from "@/src/api/upload";
import { useDraftStore } from "@/src/observation/draftStore";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; step: "presign" | "put" | "create" }
  | { kind: "identifying"; observationId: string }
  | {
      kind: "picking";
      observationId: string;
      suggestions: CvSuggestion[];
      cvUnavailable: boolean;
    }
  | { kind: "patching"; observationId: string }
  | { kind: "done"; observationId: string }
  | { kind: "error"; message: string };

export default function ObserveSubmitScreen() {
  const photo = useDraftStore((s) => s.photo);
  const clearDraft = useDraftStore((s) => s.clear);

  const [locStatus, setLocStatus] = useState<
    "loading" | "ready" | "denied" | "error"
  >("loading");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [manualSpecies, setManualSpecies] = useState("");
  const [showManualInput, setShowManualInput] = useState(false);
  // Geocoded place_name resolves in parallel with /identify; gets folded
  // into whatever PATCH the kid eventually sends.
  const [placeName, setPlaceName] = useState<string | null>(null);

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

  async function pickSuggestion(s: CvSuggestion) {
    if (phase.kind !== "picking") return;
    const obsId = phase.observationId;
    setPhase({ kind: "patching", observationId: obsId });
    try {
      await patchObservation(obsId, {
        taxon_id: s.taxon_id,
        // Server auto-fills species_name from species_cache when only
        // taxon_id is sent (PR #40).
        place_name: placeName,
      });
      clearDraft();
      setPhase({ kind: "done", observationId: obsId });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  async function pickManual() {
    if (phase.kind !== "picking") return;
    const obsId = phase.observationId;
    const trimmed = manualSpecies.trim();
    if (!trimmed) return;
    setPhase({ kind: "patching", observationId: obsId });
    try {
      await patchObservation(obsId, {
        species_name: trimmed,
        place_name: placeName,
      });
      clearDraft();
      setPhase({ kind: "done", observationId: obsId });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  async function pickSkip() {
    if (phase.kind !== "picking") return;
    const obsId = phase.observationId;
    if (!placeName) {
      // Nothing to PATCH; skip straight to done.
      clearDraft();
      setPhase({ kind: "done", observationId: obsId });
      return;
    }
    setPhase({ kind: "patching", observationId: obsId });
    try {
      await patchObservation(obsId, { place_name: placeName });
      clearDraft();
      setPhase({ kind: "done", observationId: obsId });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

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
          {placeName ? `\n${placeName}` : ""}
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

      {phase.kind === "uploading" && (
        <View style={styles.row}>
          <ActivityIndicator />
          <Text style={styles.value}>{stepLabel(phase.step)}…</Text>
        </View>
      )}
      {phase.kind === "identifying" && (
        <View style={styles.row}>
          <ActivityIndicator />
          <Text style={styles.value}>asking iNaturalist…</Text>
        </View>
      )}
      {phase.kind === "patching" && (
        <View style={styles.row}>
          <ActivityIndicator />
          <Text style={styles.value}>saving your pick…</Text>
        </View>
      )}
      {phase.kind === "done" && (
        <Text style={styles.success}>
          ● Submitted! id: {phase.observationId}
        </Text>
      )}
      {phase.kind === "error" && (
        <Text style={styles.error}>● {phase.message}</Text>
      )}

      {phase.kind === "picking" && (
        <View style={styles.picker}>
          <Text style={styles.label}>What is it?</Text>
          {phase.cvUnavailable && (
            <Text style={styles.help}>
              Couldn&apos;t reach iNaturalist. Type your own or skip.
            </Text>
          )}
          {phase.suggestions.map((s) => (
            <Pressable
              key={s.taxon_id}
              style={[styles.suggestion]}
              onPress={() => void pickSuggestion(s)}
            >
              <Text style={styles.suggestionName}>
                {s.common_name ?? s.scientific_name ?? "Unknown taxon"}
              </Text>
              <Text style={styles.suggestionMeta}>{Math.round(s.score)}%</Text>
            </Pressable>
          ))}

          {!showManualInput ? (
            <Pressable
              style={[styles.suggestion, styles.suggestionGhost]}
              onPress={() => setShowManualInput(true)}
            >
              <Text style={styles.suggestionName}>Type my own</Text>
            </Pressable>
          ) : (
            <View>
              <TextInput
                style={styles.input}
                value={manualSpecies}
                onChangeText={setManualSpecies}
                placeholder="e.g. Northern Cardinal"
                placeholderTextColor="#999"
                autoCapitalize="words"
                autoFocus
              />
              <Pressable
                style={[
                  styles.button,
                  styles.buttonPrimary,
                  manualSpecies.trim().length === 0 && styles.buttonDisabled,
                ]}
                disabled={manualSpecies.trim().length === 0}
                onPress={() => void pickManual()}
              >
                <Text style={styles.buttonText}>Save</Text>
              </Pressable>
            </View>
          )}

          <Pressable
            style={[styles.suggestion, styles.suggestionGhost]}
            onPress={() => void pickSkip()}
          >
            <Text style={styles.suggestionName}>Skip for now</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.actions}>
        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.back()}
        >
          <Text style={styles.buttonText}>
            {phase.kind === "done" ? "Done" : "Cancel"}
          </Text>
        </Pressable>
        {phase.kind !== "picking" && phase.kind !== "done" && (
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
                });

                // Fire geocode in parallel; result folded into the
                // eventual PATCH. Failure is non-fatal.
                void reverseGeocode(coords.lat, coords.lng)
                  .then((r) => setPlaceName(r.place_name))
                  .catch(() => {
                    /* ignore */
                  });

                setPhase({ kind: "identifying", observationId: obs.id });
                const ident = await identifyObservation(obs.id);
                setPhase({
                  kind: "picking",
                  observationId: obs.id,
                  suggestions: ident.suggestions,
                  cvUnavailable: ident.cv_unavailable,
                });
              } catch (err) {
                setPhase({ kind: "error", message: errorMessage(err) });
              }
            }}
          >
            <Text style={styles.buttonText}>Submit</Text>
          </Pressable>
        )}
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

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
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
    height: 200,
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
  help: {
    fontSize: 12,
    opacity: 0.6,
    marginTop: 4,
    marginBottom: 8,
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
  picker: {
    marginTop: 16,
  },
  suggestion: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginTop: 8,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
  },
  suggestionGhost: {
    backgroundColor: "transparent",
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  suggestionName: {
    fontSize: 15,
  },
  suggestionMeta: {
    fontSize: 13,
    opacity: 0.6,
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
