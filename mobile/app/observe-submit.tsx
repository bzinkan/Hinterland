import { router, Stack } from "expo-router";
import * as Location from "expo-location";
import { useEffect, useRef, useState } from "react";
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
  type PhotoPresignResponse,
  createObservation,
  presignPhoto,
} from "@/src/api/observations";
import { identifyPhoto } from "@/src/api/photos";
import { queryClient } from "@/src/api/queryClient";
import {
  UploadHttpError,
  legacyPutHeaders,
  putPhotoToSignedUrl,
} from "@/src/api/upload";
import {
  selectExpeditionRewards,
  selectSanctuaryRewards,
} from "@/src/expeditions/logic";
import { type DraftPhoto, useDraftStore } from "@/src/observation/draftStore";
import { SanctuaryRevealModal } from "@/src/sanctuary/SanctuaryRevealModal";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; step: "presign" | "put" | "identify" | "create" }
  | {
      kind: "picking";
      photoId: string;
      suggestions: CvSuggestion[];
      cvUnavailable: boolean;
      noMatches: boolean;
    }
  | { kind: "done"; observationId: string }
  | { kind: "error"; message: string };

type IdentificationChoice = {
  taxon_id?: number | null;
  species_name?: string | null;
};

/**
 * Server-side progress that survives a failed leg. The phase machine above
 * is what the kid sees; this is what lets "Try again" resume from the
 * failed leg instead of re-running presign + create -- which would attach
 * a second observation to a fresh photo row and orphan the first one.
 */
type SubmitProgress = {
  presigned: PhotoPresignResponse | null;
  uploaded: boolean;
  identify: {
    suggestions: CvSuggestion[];
    cvUnavailable: boolean;
    noMatches: boolean;
  } | null;
  pendingCreate: IdentificationChoice | null;
};

function presignExpired(presigned: PhotoPresignResponse): boolean {
  // 30s safety margin: a PUT started on the edge of SAS expiry fails.
  return Date.parse(presigned.expires_at) - 30_000 < Date.now();
}

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
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(
    null,
  );
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [manualSpecies, setManualSpecies] = useState("");
  const [showManualInput, setShowManualInput] = useState(false);
  // Geocoded place_name resolves in parallel with /identify; gets folded
  // into whatever PATCH the kid eventually sends.
  const [placeName, setPlaceName] = useState<string | null>(null);
  // Dispatcher rewards land on the final createObservation response, after
  // the kid has picked an iNat suggestion, typed a name, or skipped.
  // Sanctuary rewards drive the reveal modal once we transition to
  // ``done``; expedition rewards drive the inline celebration card.
  const [sanctuaryRewards, setSanctuaryRewards] = useState<ObservationReward[]>(
    [],
  );
  const [expeditionRewards, setExpeditionRewards] = useState<
    ObservationReward[]
  >([]);
  const [revealVisible, setRevealVisible] = useState(false);
  // Not state: progress is only read inside runSubmit/sendPatch, and a
  // re-render mid-pipeline must not reset it.
  const progressRef = useRef<SubmitProgress>({
    presigned: null,
    uploaded: false,
    identify: null,
    pendingCreate: null,
  });

  // Fold dispatcher rewards from the final create response into local state.
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
    if (
      phase.kind === "done" &&
      sanctuaryRewards.length > 0 &&
      !revealVisible
    ) {
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

  // Submit starts the pipeline; Try again resumes it after an error.
  const canSubmit =
    (phase.kind === "idle" || phase.kind === "error") &&
    locStatus === "ready" &&
    coords !== null;

  function finishDone(observationId: string) {
    progressRef.current.pendingCreate = null;
    clearDraft();
    // Invalidate here (not just in the modal handlers) -- most kids
    // never see the Sanctuary reveal, and the expedition step counts
    // on the tab are stale the moment a step completes. The Field Journal
    // caches for 30s; without its invalidation the fresh observation
    // doesn't show up until a pull-to-refresh. Sanctuary contributions
    // happen at final save when a taxon was chosen, so its zone counts
    // changed even when no reveal-worthy reward fired.
    void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    void queryClient.invalidateQueries({ queryKey: ["observations", "me"] });
    void queryClient.invalidateQueries({ queryKey: ["sanctuary", "me"] });
    setPhase({ kind: "done", observationId });
  }

  async function createFinalObservation(choice: IdentificationChoice) {
    if (!coords) return;
    const presigned = progressRef.current.presigned;
    if (!presigned) return;

    // Stashed before the request so Try again can re-send the same final
    // choice if the create response is lost or the network drops.
    progressRef.current.pendingCreate = choice;
    setPhase({ kind: "uploading", step: "create" });
    const obs = await createObservation({
      photo_id: presigned.photo_id,
      latitude: coords.lat,
      longitude: coords.lng,
      taxon_id: choice.taxon_id,
      species_name: choice.species_name,
      place_name: placeName,
    });
    if (obs.taxon_id !== null) {
      void queryClient.invalidateQueries({ queryKey: ["dex", "me"] });
    }
    collectRewards(obs.rewards);
    finishDone(obs.id);
  }

  async function pickSuggestion(s: CvSuggestion) {
    if (phase.kind !== "picking") return;
    try {
      await createFinalObservation(
        s.taxon_id != null
          ? { taxon_id: s.taxon_id }
          : { species_name: suggestionDisplayName(s) },
      );
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  async function pickManual() {
    if (phase.kind !== "picking") return;
    const trimmed = manualSpecies.trim();
    if (!trimmed) return;
    try {
      await createFinalObservation({ species_name: trimmed });
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  async function pickSkip() {
    if (phase.kind !== "picking") return;
    try {
      await createFinalObservation({});
    } catch (err) {
      setPhase({ kind: "error", message: errorMessage(err) });
    }
  }

  /**
   * Run (or resume) the submit pipeline. Completed legs are recorded in
   * progressRef, so a retry picks up at the failed leg: a created
   * observation is never re-created, an uploaded photo is never
   * re-presigned (unless the SAS expired), and a failed species PATCH is
   * re-sent as-is.
   */
  async function runSubmit() {
    if (!coords || !displayPhoto) return;
    const p = progressRef.current;
    try {
      // A final save failed after the kid picked/manual/skipped:
      // re-send the exact same create payload.
      if (p.pendingCreate) {
        await createFinalObservation(p.pendingCreate);
        return;
      }

      // Identify already completed: drop straight back into picking.
      if (p.presigned && p.identify) {
        setPhase({
          kind: "picking",
          photoId: p.presigned.photo_id,
          suggestions: p.identify.suggestions,
          cvUnavailable: p.identify.cvUnavailable,
          noMatches: p.identify.noMatches,
        });
        return;
      }

      // Snapshot the photo so the done UI survives clearDraft().
      setSubmittedPhoto(displayPhoto);

      let presigned = p.presigned;
      // Presign fresh, or re-presign when the SAS sat in an error state
      // past expiry. A superseded pending photo row never gets an
      // observation and is cleaned up with other upload orphans.
      if (!presigned || presignExpired(presigned)) {
        setPhase({ kind: "uploading", step: "presign" });
        presigned = await presignPhoto();
        p.presigned = presigned;
        p.uploaded = false;
      }

      if (!p.uploaded) {
        setPhase({ kind: "uploading", step: "put" });
        try {
          await putPhotoToSignedUrl(
            presigned.upload_url,
            displayPhoto.localUri,
            presigned.required_headers ??
              legacyPutHeaders(presigned.content_type),
          );
        } catch (err) {
          // 403 = storage rejected the SAS (expired server-side, or a
          // slow device clock fooled presignExpired). Forget this
          // presign so the next Try again mints a fresh one instead of
          // looping on the same dead URL.
          if (err instanceof UploadHttpError && err.status === 403) {
            p.presigned = null;
            p.uploaded = false;
          }
          throw err;
        }
        p.uploaded = true;
      }

      // Fire geocode in parallel; result is folded into final create if
      // it resolves before the kid picks/manual/skips. Failure is non-fatal.
      void reverseGeocode(coords.lat, coords.lng)
        .then((r) => setPlaceName(r.place_name))
        .catch(() => {
          /* ignore */
        });

      setPhase({ kind: "uploading", step: "identify" });
      const ident = await identifyPhoto(presigned.photo_id);
      p.identify = {
        suggestions: ident.suggestions,
        cvUnavailable: ident.cv_unavailable,
        noMatches: ident.no_matches,
      };
      setPhase({
        kind: "picking",
        photoId: presigned.photo_id,
        suggestions: ident.suggestions,
        cvUnavailable: ident.cv_unavailable,
        noMatches: ident.no_matches,
      });
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
          Hinterland, then come back.
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
      {/* Expedition celebration -- an inline card, not a modal, so it
          stays visible after the Sanctuary reveal closes (and renders
          when no reveal fires at all). Title/detail come straight from
          the dispatcher; the client never fabricates progress. */}
      {phase.kind === "done" && expeditionRewards.length > 0 && (
        <View style={styles.expeditionCard}>
          {expeditionRewards.map((r, i) => (
            <View
              key={`${r.type}-${i}`}
              style={[
                styles.expeditionReward,
                i > 0 && styles.expeditionRewardGap,
              ]}
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
          {phase.noMatches && (
            <Text style={styles.help}>
              No matches from iNaturalist. Type your own ID or save it as an
              unknown organism.
            </Text>
          )}
          {!phase.cvUnavailable &&
            !phase.noMatches &&
            phase.suggestions.length === 0 && (
              <Text style={styles.help}>
                No suggestions came back. You can still type your own ID or
                skip.
              </Text>
            )}
          {phase.suggestions.map((s) => (
            <Pressable
              key={`${s.source ?? "inat"}-${s.taxon_id ?? s.common_name ?? s.scientific_name}-${s.score}`}
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
          onPress={phase.kind === "done" ? handleDone : () => router.back()}
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
              !canSubmit && styles.buttonDisabled,
            ]}
            disabled={!canSubmit}
            onPress={() => void runSubmit()}
          >
            <Text style={styles.buttonText}>
              {phase.kind === "error" ? "Try again" : "Submit"}
            </Text>
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

function stepLabel(step: "presign" | "put" | "identify" | "create"): string {
  switch (step) {
    case "presign":
      return "Requesting upload URL";
    case "put":
      return "Uploading photo";
    case "identify":
      return "Asking iNaturalist";
    case "create":
      return "Saving observation";
  }
}

function suggestionDisplayName(s: CvSuggestion): string | null {
  return s.common_name ?? s.scientific_name ?? null;
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
