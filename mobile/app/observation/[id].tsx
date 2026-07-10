import { router, Stack, useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
  AppState,
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
} from "react-native";

import { Text, View } from "@/components/Themed";
import {
  type CvSuggestion,
  type IdentificationUpdate,
  type Observation,
  type ObservationListItem,
  type ObservationListResponse,
  identifyObservation,
  updateObservationIdentification,
} from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import { searchTaxa, type TaxonCatalogItem } from "@/src/api/taxa";
import { useAuthSession } from "@/src/auth/session";
import {
  ImperativeRequestSupersededError,
} from "@/src/auth/requestBoundary";
import {
  childPhotoPresentation,
  childRecordIsVisible,
  journalCaption,
  isUrlUsable,
} from "@/src/observation/journalLogic";
import {
  childSafeError,
  isEntryUnavailableError,
} from "@/src/observation/childSafeErrors";
import {
  conservationLabel,
  factsAreEmpty,
  worldwideLine,
} from "@/src/observation/speciesFactsLogic";
import { useObservationDetail } from "@/src/observation/useObservationDetail";
import { useObservationCapabilities } from "@/src/observation/useObservationCapabilities";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";
import { useSpeciesFacts } from "@/src/observation/useSpeciesFacts";
import { mergeTaxonResults, searchCoreTaxa } from "@/src/observation/coreTaxa";
import { searchInstalledTaxa } from "@/src/observation/taxonomyPacks";
import {
  emptyIdentificationPresentation,
  identificationResponseMatchesScope,
  identificationScopeKey,
} from "@/src/observation/identificationPresentation";
import {
  ScopedRequestBoundary,
  ScopedRequestSupersededError,
} from "@/src/observation/scopedRequestBoundary";

/**
 * Full-size view of one observation, opened from the Field Journal.
 *
 * Field Journal navigation usually has the ["observations","me"] cache ready,
 * so the cached item renders immediately. Deep links and app restarts fetch
 * GET /v1/observations/{id} so saved entries still open reliably.
 */
function findCachedObservation(
  id: string,
  ownerUserId: string | null,
): ObservationListItem | null {
  if (!ownerUserId) return null;
  const data = queryClient.getQueryData<{
    pages: ObservationListResponse[];
  }>(["observations", ownerUserId]);
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
  const session = useAuthSession();
  const ownerUserId =
    session.status === "authenticated" ? session.user.id : null;
  const { id } = useLocalSearchParams<{ id: string }>();
  const observationId = typeof id === "string" ? id : null;
  const cachedItem =
    observationId !== null
      ? findCachedObservation(observationId, ownerUserId)
      : null;
  const detailQuery = useObservationDetail(observationId);
  const refetchDetail = detailQuery.refetch;
  const detailUnavailable = isEntryUnavailableError(detailQuery.error);
  // Rejection can complete between refreshes, jumping straight from a clean
  // response to 404. TanStack retains the previous success data on a refetch
  // error, so explicitly discard it and unmount the decoded image.
  const cachedOrServerItem = detailQuery.data ?? cachedItem;
  const responseItem = detailUnavailable ? null : cachedOrServerItem;
  const item =
    responseItem && childRecordIsVisible(String(responseItem.child_presentation_status))
      ? responseItem
      : null;
  const capabilities = useObservationCapabilities();
  const presentation = childPhotoPresentation(
    responseItem?.child_presentation_status ?? "failed",
  );

  // Set when the kid identifies a Mystery find right here. The cached
  // list item is a non-reactive snapshot, so this local override makes
  // the species + facts appear immediately; the invalidations below
  // bring the list cache in line for everyone else.
  const [identified, setIdentified] = useState<{
    taxonId: number | null;
    speciesName: string | null;
  } | null>(null);
  const activeIdentificationScope = useRef({ ownerUserId, observationId });
  activeIdentificationScope.current = { ownerUserId, observationId };

  useEffect(() => setIdentified(null), [observationId, ownerUserId]);

  useEffect(() => {
    if (!ownerUserId || !observationId) return;
    const refresh = () => {
      void refetchDetail();
      void queryClient.refetchQueries({
        queryKey: ["observations", ownerUserId],
        type: "active",
      });
    };
    const timer = setInterval(refresh, 30_000);
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") refresh();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [observationId, ownerUserId, refetchDetail]);

  useEffect(() => {
    if (!cachedOrServerItem || (presentation.mode === "image" && !detailUnavailable)) return;
    queryClient.removeQueries({
      queryKey: ["photo-url", ownerUserId ?? "anonymous", cachedOrServerItem.photo_id],
      exact: true,
    });
  }, [cachedOrServerItem, detailUnavailable, ownerUserId, presentation.mode]);

  if (session.status !== "authenticated") {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Observation" }} />
        {session.status === "initializing" ? (
          <ActivityIndicator />
        ) : (
          <>
            <Text style={styles.body}>Sign in to open this observation.</Text>
            <Pressable
              style={[styles.button, styles.buttonPrimary]}
              onPress={() => router.replace("/sign-in")}
            >
              <Text style={styles.buttonText}>Sign in</Text>
            </Pressable>
          </>
        )}
      </View>
    );
  }

  if (!item) {
    const isLoading = observationId !== null && detailQuery.isPending;
    const safeError = detailQuery.isError
      ? childSafeError(detailQuery.error)
      : null;
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Observation" }} />
        {isLoading ? (
          <ActivityIndicator />
        ) : (
          <>
            <Text style={styles.body}>
              {safeError
                ? safeError.message
                : "Couldn't find that entry. Open it from your Field Journal."}
            </Text>
            {safeError?.supportCode ? (
              <Text style={styles.supportCode}>
                Adult support code: {safeError.supportCode}
              </Text>
            ) : null}
            {safeError ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Retry Field Journal entry"
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

  const itemId = item.id;
  const ts = new Date(item.observed_at ?? Date.now());
  const effectiveTaxonId = identified ? identified.taxonId : item.taxon_id;
  const effectiveSpecies = identified
    ? (identified.speciesName ?? item.species_name)
    : item.species_name;
  function handleIdentified(observation: Observation) {
    if (
      !identificationResponseMatchesScope(
        observation,
        activeIdentificationScope.current,
      )
    ) {
      return;
    }
    setIdentified({
      taxonId: observation.taxon_id,
      speciesName: observation.species_name,
    });
    void queryClient.invalidateQueries({
      queryKey: ["observations", ownerUserId ?? "anonymous"],
    });
    void queryClient.invalidateQueries({
      queryKey: [
        "observations",
        ownerUserId ?? "anonymous",
        "detail",
        itemId,
      ],
    });
    void queryClient.invalidateQueries({
      queryKey: ["dex", ownerUserId ?? "anonymous"],
    });
    void queryClient.invalidateQueries({
      queryKey: ["expeditions", ownerUserId ?? "anonymous"],
    });
    void queryClient.invalidateQueries({
      queryKey: ["sanctuary", ownerUserId ?? "anonymous"],
    });
  }

  return (
    <ScrollView
      testID="observation-detail-screen"
      style={styles.container}
      contentContainerStyle={styles.content}
    >
      <Stack.Screen options={{ title: journalCaption(effectiveSpecies) }} />

      {presentation.mode === "image" ? (
        <DetailPhoto
          photoId={item.photo_id}
          description={`Photo of ${journalCaption(effectiveSpecies)}`}
        />
      ) : (
        <View
          testID={
            presentation.status === "pilot_private"
              ? "observation-detail-private-status"
              : undefined
          }
          accessible
          accessibilityRole="text"
          accessibilityLabel={presentation.message ?? "Photo unavailable"}
          style={styles.photoPlaceholder}
        >
          <Text style={styles.placeholderGlyph}>🔒</Text>
          <Text style={styles.placeholderText}>{presentation.message}</Text>
        </View>
      )}

      <Text style={styles.species}>{journalCaption(effectiveSpecies)}</Text>

      {effectiveTaxonId !== null ? <SpeciesFactsCard taxonId={effectiveTaxonId} /> : null}

      {detailQuery.data ? (
        <IdentifySection
          key={identificationScopeKey(session.user.id, item.id)}
          ownerUserId={session.user.id}
          observationId={item.id}
          expectedRevision={detailQuery.data.identification_revision}
          currentTaxonId={effectiveTaxonId}
          currentSpeciesName={effectiveSpecies}
          photoHelperEnabled={
            capabilities.photoHelperEnabled && presentation.mode === "image"
          }
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
        {item.geohash4 ? `Coarse area ${item.geohash4}` : "No location saved"}
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
  description,
}: {
  photoId: string;
  description: string;
}) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const urlQuery = usePhotoUrl(photoId, true);
  // One silent re-mint on image-load failure (moderation may have moved
  // the blob since the URL was minted), then a tappable placeholder.
  const [loadRetried, setLoadRetried] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  if (urlQuery.isError || loadFailed) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Retry ${description}`}
        accessibilityHint="Requests a new private photo link"
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

  // Pending, or a cache hit inside the server's 60-second revocation bound --
  // wait for the background re-mint instead of handing <Image> an expiring SAS.
  if (urlQuery.isPending || !isUrlUsable(urlQuery.data.expires_at)) {
    return (
      <View style={styles.photoPlaceholder}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <Image
      testID="observation-detail-photo-image"
      accessible
      accessibilityLabel={description}
      source={{ uri: urlQuery.data.url }}
      style={styles.photo}
      resizeMode="contain"
      onError={() => {
        if (!loadRetried) {
          setLoadRetried(true);
          void queryClient.invalidateQueries({
            queryKey: ["photo-url", ownerUserId ?? "anonymous", photoId],
          });
        } else {
          setLoadFailed(true);
        }
      }}
    />
  );
}

/**
 * "About this species" -- structured catalog facts only. Raw cached upstream
 * prose is intentionally ignored even if an older server accidentally sends
 * it. Renders nothing on error / degradation / empty facts: the
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
      {worldwide ? <Text style={styles.factRow}>🌍 {worldwide}</Text> : null}
      {conservation ? (
        <Text style={styles.factRow}>
          💚 Conservation status: {conservation}
        </Text>
      ) : null}
    </View>
  );
}

function IdentifySection({
  ownerUserId,
  observationId,
  expectedRevision,
  currentTaxonId,
  currentSpeciesName,
  photoHelperEnabled,
  onIdentified,
}: {
  ownerUserId: string;
  observationId: string;
  expectedRevision: number;
  currentTaxonId: number | null;
  currentSpeciesName: string | null;
  photoHelperEnabled: boolean;
  onIdentified: (observation: Observation) => void;
}) {
  const [catalogQuery, setCatalogQuery] = useState("");
  const [catalogResults, setCatalogResults] = useState<TaxonCatalogItem[]>([]);
  const [suggestions, setSuggestions] = useState<CvSuggestion[]>([]);
  const [manualSpecies, setManualSpecies] = useState("");
  const [revision, setRevision] = useState(expectedRevision);
  const [busy, setBusy] = useState<"photo" | "save" | null>(null);
  const [searching, setSearching] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [supportCode, setSupportCode] = useState<string | null>(null);
  const [scopeBoundary] = useState(() => new ScopedRequestBoundary());
  const scopeKey = identificationScopeKey(ownerUserId, observationId);

  useEffect(() => setRevision(expectedRevision), [expectedRevision]);

  useEffect(() => {
    const reset = emptyIdentificationPresentation(expectedRevision);
    setCatalogQuery(reset.catalogQuery);
    setCatalogResults([]);
    setSuggestions([]);
    setManualSpecies(reset.manualSpecies);
    setRevision(reset.revision);
    setBusy(reset.busy);
    setSearching(reset.searching);
    setMessage(reset.message);
    setSupportCode(null);
  }, [scopeKey]);

  useEffect(
    () => () => {
      scopeBoundary.invalidate();
    },
    [scopeBoundary, scopeKey],
  );

  useEffect(() => {
    const trimmed = catalogQuery.trim();
    if (trimmed.length < 2) {
      setCatalogResults([]);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    const bundled = searchCoreTaxa(trimmed);
    setCatalogResults(bundled);
    const timer = setTimeout(() => {
      setSearching(true);
      void searchInstalledTaxa(trimmed)
        .catch(() => [])
        .then((installed) => {
          const local = mergeTaxonResults(bundled, installed);
          if (!controller.signal.aborted) setCatalogResults(local);
          return searchTaxa(trimmed, controller.signal).then((response) =>
            mergeTaxonResults(local, response.items),
          );
        })
        .then((merged) => {
          if (!controller.signal.aborted) setCatalogResults(merged);
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setMessage("Catalog search is unavailable. Try again later or enter an identification.");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [catalogQuery]);

  async function askPhotoHelper() {
    setBusy("photo");
    setMessage(null);
    setSupportCode(null);
    try {
      const ident = await scopeBoundary.run((signal) =>
        identifyObservation(observationId, signal),
      );
      setSuggestions(ident.suggestions);
      setMessage(
        ident.cv_unavailable
          ? "The photo helper is unavailable. Catalog, manual identification, and Unknown still work."
          : ident.suggestions.length === 0
            ? "The photo helper was not sure. Unknown is always okay."
            : "Pick a photo-helper idea only if it looks right.",
      );
    } catch (err) {
      if (
        err instanceof ImperativeRequestSupersededError ||
        err instanceof ScopedRequestSupersededError
      ) return;
      const safe = childSafeError(err);
      setMessage(safe.message);
      setSupportCode(safe.supportCode);
    } finally {
      setBusy(null);
    }
  }

  async function save(payload: Omit<IdentificationUpdate, "expected_revision">) {
    setBusy("save");
    setMessage(null);
    setSupportCode(null);
    try {
      const response = await scopeBoundary.run((signal) =>
        updateObservationIdentification(
          observationId,
          {
            ...payload,
            expected_revision: revision,
          },
          signal,
        ),
      );
      setRevision(response.observation.identification_revision);
      onIdentified(response.observation);
      setMessage("Updated. Your Dex and expedition progress are being checked again.");
    } catch (err) {
      if (
        err instanceof ImperativeRequestSupersededError ||
        err instanceof ScopedRequestSupersededError
      ) return;
      const safe = childSafeError(err);
      setMessage(safe.message);
      setSupportCode(safe.supportCode);
    } finally {
      setBusy(null);
    }
  }

  return (
    <View style={styles.factsCard}>
      <Text style={styles.factsHeading}>Improve identification</Text>
      <Text style={styles.identifyHelp}>
        Current: {currentSpeciesName ?? (currentTaxonId ? `Taxon ${currentTaxonId}` : "Unknown")}
      </Text>

      <TextInput
        accessibilityLabel="Search the organism catalog"
        accessibilityHint="Type at least two letters to find a catalog identification"
        style={styles.input}
        value={catalogQuery}
        onChangeText={setCatalogQuery}
        placeholder="Search the organism catalog"
        placeholderTextColor="#999"
      />
      {searching ? <ActivityIndicator /> : null}
      {catalogResults.map((taxon) => (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Use ${taxonDisplayName(taxon)} as the identification`}
          accessibilityHint="Updates this entry and recalculates progress"
          accessibilityState={{ disabled: busy != null }}
          key={taxon.taxon_id}
          style={styles.suggestion}
          disabled={busy != null}
          onPress={() =>
            void save({ source: "catalog", taxon_id: taxon.taxon_id })
          }
        >
          <Text style={styles.suggestionName}>{taxonDisplayName(taxon)}</Text>
          <Text style={styles.suggestionMeta}>{taxon.scientific_name}</Text>
        </Pressable>
      ))}

      {photoHelperEnabled ? (
        <Pressable
          testID="observation-photo-helper-button"
          accessibilityRole="button"
          accessibilityLabel="Ask the photo helper"
          accessibilityHint="Gets optional suggestions from the approved photo helper"
          accessibilityState={{ disabled: busy != null, busy: busy === "photo" }}
          style={[styles.button, styles.buttonGhost, styles.identifyAction]}
          disabled={busy != null}
          onPress={() => void askPhotoHelper()}
        >
          <Text style={styles.buttonText}>Ask the photo helper</Text>
        </Pressable>
      ) : null}
      {photoHelperEnabled ? suggestions.map((suggestion) => (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Use ${suggestionDisplayName(suggestion) ?? `Taxon ${suggestion.taxon_id}`} from the photo helper`}
          accessibilityState={{ disabled: busy != null }}
          key={`cv-${suggestion.taxon_id}`}
          style={styles.suggestion}
          disabled={busy != null}
          onPress={() =>
            void save({ source: "cv", taxon_id: suggestion.taxon_id })
          }
        >
          <Text style={styles.suggestionName}>
            {suggestionDisplayName(suggestion) ?? `Taxon ${suggestion.taxon_id}`}
          </Text>
          <Text style={styles.suggestionMeta}>{Math.round(suggestion.score)}%</Text>
        </Pressable>
      )) : null}

      <TextInput
        accessibilityLabel="Manual identification correction"
        accessibilityHint="Enter only an organism identification, not a general journal note"
        style={styles.input}
        value={manualSpecies}
        onChangeText={setManualSpecies}
        placeholder="Or enter a short identification"
        placeholderTextColor="#999"
        maxLength={200}
      />
      <View style={styles.identificationActions}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Use manual identification"
          accessibilityHint="Updates this entry with the organism identification you entered"
          accessibilityState={{
            disabled: busy != null || manualSpecies.trim().length === 0,
            busy: busy === "save",
          }}
          style={[styles.button, styles.buttonGhost, styles.identificationButton]}
          disabled={busy != null || manualSpecies.trim().length === 0}
          onPress={() =>
            void save({ source: "manual_text", manual_text: manualSpecies.trim() })
          }
        >
          <Text style={styles.buttonText}>Use identification</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Mark identification Unknown"
          accessibilityHint="Keeps the observation without adding a Dex species"
          accessibilityState={{ disabled: busy != null, busy: busy === "save" }}
          style={[styles.button, styles.buttonGhost, styles.identificationButton]}
          disabled={busy != null}
          onPress={() => void save({ source: "unknown" })}
        >
          <Text style={styles.buttonText}>Use Unknown</Text>
        </Pressable>
      </View>
      {busy === "photo" || busy === "save" ? <ActivityIndicator /> : null}
      {message ? <Text style={styles.identifyHelp}>{message}</Text> : null}
      {supportCode ? (
        <Text style={styles.supportCode}>Adult support code: {supportCode}</Text>
      ) : null}
    </View>
  );
}

function taxonDisplayName(taxon: TaxonCatalogItem): string {
  return taxon.common_name ?? taxon.scientific_name ?? `Taxon ${taxon.taxon_id}`;
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
    minHeight: 44,
    minWidth: 44,
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
  factRow: {
    fontSize: 14,
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
  identifyAction: {
    marginTop: 10,
  },
  identificationActions: {
    flexDirection: "row",
    gap: 8,
    marginTop: 8,
    backgroundColor: "transparent",
  },
  identificationButton: {
    flex: 1,
  },
  suggestion: {
    minHeight: 44,
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
    minHeight: 44,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 8,
    marginTop: 8,
    fontSize: 14,
    color: "#fff",
  },
  supportCode: {
    fontSize: 11,
    opacity: 0.62,
    marginTop: 8,
  },
});
