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
  type ObservationReward,
  createObservation,
  identifyObservation,
  patchObservation,
  presignPhoto,
} from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import { putPhotoToSignedUrl } from "@/src/api/upload";
import {
  selectExpeditionRewards,
  selectSanctuaryRewards,
} from "@/src/expeditions/logic";
import { type DraftPhoto, useDraftStore } from "@/src/observation/draftStore";
import { SanctuaryRevealModal } from "@/src/sanctuary/SanctuaryRevealModal";

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
  // Snapshot of the draft photo, taken when the upload kicks off. The
  // pick paths call ``clearDraft()`` right before transitioning to
  // ``done``, which nulls the store photo in the same re-render -- but
  // the done UI (thumbnail, success line, celebration card, reveal
  // modal) still needs the photo, so it renders from this snapshot.
  const [submittedPhoto, setSubmittedPhoto] = useState<DraftPhoto | null>(null);

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
  // Dispatcher rewards land on the createObservation response AND on
  // patchObservation responses when the patch sets/changes taxon_id (the
  // second dispatch is what advances taxon-based expedition steps).
  // Sanctuary rewards drive the reveal modal once we transition to
  // ``done``; expedition rewards drive the inline celebration card.
  const [sanctuaryRewards, setSanctuaryRewards] = useState<ObservationReward[]>([]);
  const [expeditionRewards, setExpeditionRewards] = useState<ObservationReward[]>([]);
  const [revealVisible, setRevealVisible] = useState(false);

  // Fold dispatcher rewards from a create/patch response into local state.
  // Accumulates -- the create and the eventual PATCH can each dispatch.
  function collectRewards(rewards: ObservationReward[] | undefined) {
    const sanctuary = selectSanctuaryRewards(rewards);
    if (sanctuary.length > 0) {
      setSanctuaryRewards((prev) => [...prev, ...sanctuary]);
    }
    const expedition = selectExpeditionRewards(rewards);
    if (expedition.length > 0) {
      setExpeditionRewards((prev) => [...prev, ...expedition]);
    }
  }

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

  // Show the Sanctuary reveal once the submit reaches ``done`` AND the
  // dispatcher returned at least one world_unlock / world_evolution.
  // ``revealVisible`` guards re-show on re-render. The kid dismisses
  // explicitly via "See Sanctuary" or "Done"; never auto-dismissed.
  useEffect(() => {
    if (phase.kind === "done" && sanctuaryRewards.length > 0 && !revealVisible) {
      setRevealVisible(true);
    }
  }, [phase.kind, sanctuaryRewards.length, revealVisible]);

  function handleSeeSanctuary() {
    // Fire-and-forget cache invalidate so the Sanctuary tab fetches fresh
    // state on first visit. We do NOT await the refetch -- the navigation
    // is the user's intent; the data lands when it lands.
    void queryClient.invalidateQueries({ queryKey: ["sanctuary", "me"] });
    void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    setRevealVisible(false);
    router.replace("/sanctuary");
  }

  function handleDone() {
    void queryClient.invalidateQueries({ queryKey: ["sanctuary", "me"] });
    void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    setRevealVisible(false);
    // Same destination the existing post-submit flow would land on if no
    // reveal fired -- the submit screen stays mounted (kid can read the
    // success line) and ``router.back()`` is wired to a back button below.
    router.back();
  }

  // Prefer the live draft; fall back to the snapshot once the draft is
  // cleared. Bail only when neither exists (deep link / stale screen).
  const displayPhoto = photo ?? submittedPhoto;
  if (!displayPhoto) {
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
      const obs = await patchObservation(obsId, {
        taxon_id: s.taxon_id,
        // Server auto-fills species_name from species_cache when only
        // taxon_id is sent (PR #40).
        place_name: placeName,
      });
      collectRewards(obs.rewards);
      clearDraft();
      // Invalidate here (not just in the modal handlers) -- most kids
      // never see the Sanctuary reveal, and the expedition step counts
      // on the tab are stale the moment a step completes.
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
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
      const obs = await patchObservation(obsId, {
        species_name: trimmed,
        place_name: placeName,
      });
      collectRewards(obs.rewards);
      clearDraft();
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
      setPhase({ kind: "done", observationId: obsId });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  async function pickSkip() {
    if (phase.kind !== "picking") return;
    const obsId = phase.observationId;
    if (!placeName) {
      // Nothing to PATCH; skip straight to done. No response to harvest
      // rewards from, but the create may have advanced an expedition.
      clearDraft();
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
      setPhase({ kind: "done", observationId: obsId });
      return;
    }
    setPhase({ kind: "patching", observationId: obsId });
    try {
      const obs = await patchObservation(obsId, { place_name: placeName });
      collectRewards(obs.rewards);
      clearDraft();
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
      setPhase({ kind: "done", observationId: obsId });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: "Submit" }} />
      <Image
        source={{ uri: displayPhoto.localUri }}
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
      {/* Expedition celebration -- an inline card, not a modal, so it
          stays visible after the Sanctuary reveal closes (and renders
          when no reveal fires at all). Title/detail come straight from
          the dispatcher; the client never fabricates progress. */}
      {phase.kind === "done" && expeditionRewards.length > 0 && (
        <View style={styles.expeditionCard}>
          {expeditionRewards.map((r, i) => (
            <View
              key={`${r.type}-${i}`}
              style={[styles.expeditionReward, i > 0 && styles.expeditionRewardGap]}
            >
              <Text style={styles.expeditionRewardTitle}>{r.title}</Text>
              {r.detail ? (
                <Text style={styles.expeditionRewardDetail}>{r.detail}</Text>
              ) : null}
            </View>
          ))}
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
                // Snapshot the photo so the done UI survives clearDraft().
                setSubmittedPhoto(displayPhoto);

                setPhase({ kind: "uploading", step: "presign" });
                const presigned = await presignPhoto();

                setPhase({ kind: "uploading", step: "put" });
                await putPhotoToSignedUrl(
                  presigned.upload_url,
                  displayPhoto.localUri,
                );

                setPhase({ kind: "uploading", step: "create" });
                const obs = await createObservation({
                  photo_id: presigned.photo_id,
                  latitude: coords.lat,
                  longitude: coords.lng,
                });

                // Stash dispatcher rewards so the reveal modal and the
                // expedition celebration can render when we transition to
                // ``done``. ``rewards`` is optional on the wire (empty
                // list when the dispatcher emitted nothing); the helpers
                // handle the missing-field case.
                collectRewards(obs.rewards);

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
      <SanctuaryRevealModal
        visible={revealVisible}
        reward={sanctuaryRewards[0] ?? null}
        extraRewardCount={Math.max(0, sanctuaryRewards.length - 1)}
        onSeeSanctuary={handleSeeSanctuary}
        onDone={handleDone}
      />
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
  expeditionCard: {
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginTop: 12,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
  },
  expeditionReward: {
    // Transparent so the themed View doesn't paint over the card.
    backgroundColor: "transparent",
  },
  expeditionRewardGap: {
    marginTop: 10,
  },
  expeditionRewardTitle: {
    fontSize: 15,
    fontWeight: "600",
  },
  expeditionRewardDetail: {
    fontSize: 13,
    opacity: 0.7,
    marginTop: 2,
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
