import { router, Stack, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import {
  type CvSuggestion,
  type ObservationListItem,
  type ObservationListResponse,
  type ObservationReward,
  identifyObservation,
  patchObservation,
} from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import {
  journalCaption,
  isAwaitingModeration,
  isUrlUsable,
  photoDisplayMode,
} from "@/src/observation/journalLogic";
import {
  conservationLabel,
  factsAreEmpty,
  worldwideLine,
} from "@/src/observation/speciesFactsLogic";
import { useObservationDetail } from "@/src/observation/useObservationDetail";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";
import { useSpeciesFacts } from "@/src/observation/useSpeciesFacts";

/**
 * Full-size view of one observation, opened from the Field Journal.
 *
 * Field Journal navigation usually has the ["observations","me"] cache ready,
 * so the cached item renders immediately. Deep links and app restarts fetch
 * GET /v1/observations/{id} so saved entries still open reliably.
 */
function findCachedObservation(id: string): ObservationListItem | null {
  const data = queryClient.getQueryData<{
    pages: ObservationListResponse[];
  }>(["observations", "me"]);
  for (const page of data?.pages ?? []) {
    for (const item of page.items) {
      if (item.id === id) return item;
    }
  }
  return null;
}

function suggestionDisplayName(s: CvSuggestion): string | null {
  return s.common_name ?? s.scientific_name ?? null;
}

export default function ObservationDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const observationId = typeof id === "string" ? id : null;
  const cachedItem =
    observationId !== null ? findCachedObservation(observationId) : null;
  const detailQuery = useObservationDetail(observationId);
  const item = detailQuery.data ?? cachedItem;

  // Set when the kid identifies a Mystery find right here. The cached
  // list item is a non-reactive snapshot, so this local override makes
  // the species + facts appear immediately; the invalidations below
  // bring the list cache in line for everyone else.
  const [identified, setIdentified] = useState<{
    taxonId: number | null;
    speciesName: string | null;
    rewards: ObservationReward[];
  } | null>(null);

  if (!item) {
    const isLoading = observationId !== null && detailQuery.isPending;
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Observation" }} />
        {isLoading ? (
          <ActivityIndicator />
        ) : (
          <>
            <Text style={styles.body}>
              {detailQuery.isError
                ? detailErrorMessage(detailQuery.error)
                : "Couldn't find that entry. Open it from your Field Journal."}
            </Text>
            {detailQuery.isError ? (
              <Pressable
                style={[styles.button, styles.buttonGhost]}
                onPress={() => void detailQuery.refetch()}
              >
                <Text style={styles.buttonText}>Retry</Text>
              </Pressable>
            ) : (
              <Pressable
                style={[styles.button, styles.buttonGhost]}
                onPress={() => router.back()}
              >
                <Text style={styles.buttonText}>Back</Text>
              </Pressable>
            )}
          </>
        )}
      </View>
    );
  }

  const mode = photoDisplayMode(item.photo_status);
  const itemId = item.id;
  const ts = new Date(item.created_at);
  const effectiveTaxonId = identified ? identified.taxonId : item.taxon_id;
  const effectiveSpecies = identified
    ? (identified.speciesName ?? item.species_name)
    : item.species_name;
  const isMysteryFind = effectiveTaxonId === null && effectiveSpecies === null;

  function handleIdentified(
    taxonId: number | null,
    speciesName: string | null,
    rewards: ObservationReward[],
  ) {
    setIdentified({ taxonId, speciesName, rewards });
    // The PATCH-time dispatch may have minted first-find / advanced an
    // expedition -- and since Sanctuary contributions happen at
    // identification, the Sanctuary is exactly what just changed.
    void queryClient.invalidateQueries({ queryKey: ["observations", "me"] });
    void queryClient.invalidateQueries({
      queryKey: ["observations", "detail", itemId],
    });
    if (taxonId !== null) {
      void queryClient.invalidateQueries({ queryKey: ["dex", "me"] });
    }
    void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    void queryClient.invalidateQueries({ queryKey: ["sanctuary", "me"] });
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Stack.Screen options={{ title: journalCaption(effectiveSpecies) }} />

      {mode === "image" ? (
        <DetailPhoto
          photoId={item.photo_id}
          checking={isAwaitingModeration(item.photo_status)}
        />
      ) : (
        <View style={styles.photoPlaceholder}>
          <Text style={styles.placeholderGlyph}>
            {mode === "reviewing" ? "🔍" : "🚫"}
          </Text>
          <Text style={styles.placeholderText}>
            {mode === "reviewing"
              ? "An adult is checking this photo. It'll be back if everything looks good."
              : "This photo was removed after review."}
          </Text>
        </View>
      )}

      <Text style={styles.species}>{journalCaption(effectiveSpecies)}</Text>

      {/* Rewards from an identify-right-here PATCH: dispatcher-authored
          copy, same philosophy as the submit screen's celebration card. */}
      {identified !== null && identified.rewards.length > 0 && (
        <View style={styles.rewardCard}>
          {identified.rewards.map((r, i) => (
            <View
              key={`${r.type}-${i}`}
              style={[styles.rewardRow, i > 0 && styles.rewardRowGap]}
            >
              <Text style={styles.rewardTitle}>{r.title}</Text>
              {r.detail ? (
                <Text style={styles.rewardDetail}>{r.detail}</Text>
              ) : null}
            </View>
          ))}
        </View>
      )}

      {effectiveTaxonId !== null ? (
        <SpeciesFactsCard taxonId={effectiveTaxonId} />
      ) : isMysteryFind ? (
        <IdentifySection
          observationId={item.id}
          onIdentified={handleIdentified}
        />
      ) : null}

      <Text style={styles.label}>When</Text>
      <Text style={styles.value}>{ts.toLocaleString()}</Text>

      {item.place_name ? (
        <>
          <Text style={styles.label}>Where</Text>
          <Text style={styles.value}>{item.place_name}</Text>
        </>
      ) : null}

      <Text style={styles.label}>Location</Text>
      <Text style={styles.value}>
        {item.latitude.toFixed(4)}, {item.longitude.toFixed(4)}
        {item.geohash4 ? ` · ${item.geohash4}` : ""}
      </Text>

      <Pressable
        style={[styles.button, styles.buttonGhost, styles.backButton]}
        onPress={() => router.back()}
      >
        <Text style={styles.buttonText}>Back</Text>
      </Pressable>
    </ScrollView>
  );
}

function DetailPhoto({
  photoId,
  checking,
}: {
  photoId: string;
  checking: boolean;
}) {
  const urlQuery = usePhotoUrl(photoId, true);
  // One silent re-mint on image-load failure (moderation may have moved
  // the blob since the URL was minted), then a tappable placeholder.
  const [loadRetried, setLoadRetried] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  if (urlQuery.isError || loadFailed) {
    return (
      <Pressable
        style={styles.photoPlaceholder}
        onPress={() => {
          setLoadFailed(false);
          setLoadRetried(false);
          void urlQuery.refetch();
        }}
      >
        <Text style={styles.placeholderGlyph}>🌿</Text>
        <Text style={styles.placeholderText}>
          Couldn&apos;t load the photo. Tap to try again.
        </Text>
      </Pressable>
    );
  }

  // Pending, or a cache hit whose SAS already expired (this screen often
  // opens off a Field Journal tab that sat past the 5-min TTL) -- wait for the
  // background re-mint instead of handing <Image> a 403.
  if (urlQuery.isPending || !isUrlUsable(urlQuery.data.expires_at)) {
    return (
      <View style={styles.photoPlaceholder}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <View>
      <Image
        source={{ uri: urlQuery.data.url }}
        style={styles.photo}
        resizeMode="cover"
        onError={() => {
          if (!loadRetried) {
            setLoadRetried(true);
            void queryClient.invalidateQueries({
              queryKey: ["photo-url", photoId],
            });
          } else {
            setLoadFailed(true);
          }
        }}
      />
      {checking && (
        <Text style={styles.checkingNote}>
          Still being checked -- only you can see it for now.
        </Text>
      )}
    </View>
  );
}

/**
 * "About this species" -- factual sheet from the backend's cached iNat
 * taxon payload (Wikipedia summary, worldwide sightings, conservation
 * status). Renders nothing on error / degradation / empty facts: the
 * card is a bonus, never a failure state.
 */
function SpeciesFactsCard({ taxonId }: { taxonId: number }) {
  const facts = useSpeciesFacts(taxonId);

  if (facts.isPending) {
    return (
      <View style={styles.factsCard}>
        <ActivityIndicator />
      </View>
    );
  }
  if (
    facts.isError ||
    !facts.data.facts_available ||
    factsAreEmpty(facts.data)
  ) {
    return null;
  }

  const worldwide = worldwideLine(facts.data.observations_worldwide);
  const conservation = conservationLabel(facts.data.conservation_status);

  return (
    <View style={styles.factsCard}>
      <Text style={styles.factsHeading}>About this species</Text>
      {facts.data.scientific_name ? (
        <Text style={styles.factsScientific}>
          {facts.data.scientific_name}
          {facts.data.rank ? ` · ${facts.data.rank}` : ""}
        </Text>
      ) : null}
      {facts.data.summary ? (
        <Text style={styles.factsSummary}>{facts.data.summary}</Text>
      ) : null}
      {worldwide ? <Text style={styles.factRow}>🌍 {worldwide}</Text> : null}
      {conservation ? (
        <Text style={styles.factRow}>
          💚 Conservation status: {conservation}
        </Text>
      ) : null}
      {facts.data.summary ? (
        <Text style={styles.factsAttribution}>
          Facts from Wikipedia via iNaturalist
        </Text>
      ) : null}
    </View>
  );
}

type IdentifyPhase =
  | { kind: "idle" }
  | { kind: "identifying" }
  | { kind: "picking"; suggestions: CvSuggestion[]; cvUnavailable: boolean }
  | { kind: "patching" }
  | { kind: "error"; message: string };

/**
 * Identify a Mystery find right from its detail screen -- the same
 * identify -> pick -> PATCH flow as the submit screen. This is also the
 * recovery path for observations whose species pick failed at submit
 * time (previously stranded forever).
 */
function IdentifySection({
  observationId,
  onIdentified,
}: {
  observationId: string;
  onIdentified: (
    taxonId: number | null,
    speciesName: string | null,
    rewards: ObservationReward[],
  ) => void;
}) {
  const [phase, setPhase] = useState<IdentifyPhase>({ kind: "idle" });
  const [manualSpecies, setManualSpecies] = useState("");
  const [showManualInput, setShowManualInput] = useState(false);

  async function startIdentify() {
    setPhase({ kind: "identifying" });
    try {
      const ident = await identifyObservation(observationId);
      setPhase({
        kind: "picking",
        suggestions: ident.suggestions,
        cvUnavailable: ident.cv_unavailable,
      });
    } catch (err) {
      setPhase({ kind: "error", message: identifyErrorMessage(err) });
    }
  }

  async function pick(
    payload: { taxon_id: number } | { species_name: string },
  ) {
    setPhase({ kind: "patching" });
    try {
      const obs = await patchObservation(observationId, payload);
      onIdentified(obs.taxon_id, obs.species_name, obs.rewards ?? []);
    } catch (err) {
      setPhase({ kind: "error", message: identifyErrorMessage(err) });
    }
  }

  return (
    <View style={styles.factsCard}>
      <Text style={styles.factsHeading}>What is it?</Text>

      {phase.kind === "idle" && (
        <>
          <Text style={styles.identifyHelp}>
            This is still a mystery find. Figure out what you spotted!
          </Text>
          <Pressable
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => void startIdentify()}
          >
            <Text style={styles.buttonText}>Find out</Text>
          </Pressable>
        </>
      )}

      {phase.kind === "identifying" && (
        <View style={styles.identifyRow}>
          <ActivityIndicator />
          <Text style={styles.identifyHelp}>asking iNaturalist…</Text>
        </View>
      )}

      {phase.kind === "patching" && (
        <View style={styles.identifyRow}>
          <ActivityIndicator />
          <Text style={styles.identifyHelp}>saving your pick…</Text>
        </View>
      )}

      {phase.kind === "error" && (
        <>
          <Text style={styles.identifyError}>● {phase.message}</Text>
          <Pressable
            style={[styles.button, styles.buttonGhost]}
            onPress={() => void startIdentify()}
          >
            <Text style={styles.buttonText}>Try again</Text>
          </Pressable>
        </>
      )}

      {phase.kind === "picking" && (
        <>
          {phase.cvUnavailable && (
            <Text style={styles.identifyHelp}>
              Couldn&apos;t reach iNaturalist. Type your own below.
            </Text>
          )}
          {phase.suggestions.map((s) => {
            const displayName = suggestionDisplayName(s);
            const canPick = s.taxon_id !== null || displayName !== null;
            return (
              <Pressable
                key={`${s.source ?? "inat"}-${s.taxon_id ?? displayName ?? s.score}`}
                style={styles.suggestion}
                disabled={!canPick}
                onPress={() => {
                  if (s.taxon_id !== null) {
                    void pick({ taxon_id: s.taxon_id });
                  } else if (displayName !== null) {
                    void pick({ species_name: displayName });
                  }
                }}
              >
                <Text style={styles.suggestionName}>
                  {displayName ?? "Unknown taxon"}
                </Text>
                <Text style={styles.suggestionMeta}>
                  {Math.round(s.score)}%
                </Text>
              </Pressable>
            );
          })}

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
                onPress={() => {
                  const trimmed = manualSpecies.trim();
                  if (trimmed) void pick({ species_name: trimmed });
                }}
              >
                <Text style={styles.buttonText}>Save</Text>
              </Pressable>
            </View>
          )}

          <Pressable
            style={[styles.suggestion, styles.suggestionGhost]}
            onPress={() => setPhase({ kind: "idle" })}
          >
            <Text style={styles.suggestionName}>Not now</Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

function identifyErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

function detailErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 404) {
    return "Couldn't find that entry. Open it from your Field Journal.";
  }
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  content: {
    padding: 16,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  body: {
    fontSize: 14,
    opacity: 0.8,
    marginBottom: 16,
    textAlign: "center",
  },
  photo: {
    width: "100%",
    aspectRatio: 1,
    borderRadius: 8,
    backgroundColor: "#1a1a1a",
  },
  photoPlaceholder: {
    width: "100%",
    aspectRatio: 1,
    borderRadius: 8,
    backgroundColor: "#1a1a1a",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  placeholderGlyph: {
    fontSize: 40,
    marginBottom: 10,
  },
  placeholderText: {
    fontSize: 14,
    opacity: 0.7,
    textAlign: "center",
  },
  checkingNote: {
    fontSize: 12,
    opacity: 0.6,
    marginTop: 6,
  },
  species: {
    fontSize: 20,
    fontWeight: "600",
    marginTop: 14,
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
  button: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: "center",
  },
  buttonPrimary: {
    backgroundColor: "#2f6feb",
    marginTop: 10,
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
  backButton: {
    marginTop: 24,
  },
  rewardCard: {
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginTop: 12,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
  },
  rewardRow: {
    backgroundColor: "transparent",
  },
  rewardRowGap: {
    marginTop: 10,
  },
  rewardTitle: {
    fontSize: 15,
    fontWeight: "600",
  },
  rewardDetail: {
    fontSize: 13,
    opacity: 0.7,
    marginTop: 2,
  },
  factsCard: {
    paddingVertical: 14,
    paddingHorizontal: 14,
    marginTop: 14,
    borderRadius: 8,
    backgroundColor: "#1a1a1a",
  },
  factsHeading: {
    fontSize: 15,
    fontWeight: "600",
  },
  factsScientific: {
    fontSize: 13,
    fontStyle: "italic",
    opacity: 0.7,
    marginTop: 4,
  },
  factsSummary: {
    fontSize: 14,
    lineHeight: 20,
    marginTop: 10,
  },
  factRow: {
    fontSize: 14,
    marginTop: 10,
  },
  factsAttribution: {
    fontSize: 11,
    opacity: 0.5,
    marginTop: 10,
  },
  identifyHelp: {
    fontSize: 13,
    opacity: 0.7,
    marginTop: 6,
  },
  identifyError: {
    fontSize: 14,
    color: "#ef4444",
    marginTop: 6,
    marginBottom: 8,
  },
  identifyRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 10,
    backgroundColor: "transparent",
  },
  suggestion: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginTop: 8,
    borderRadius: 6,
    backgroundColor: "#262626",
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
});
