import FontAwesome from "@expo/vector-icons/FontAwesome";
import * as Location from "expo-location";
import { router, Stack } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import { reverseGeocode } from "@/src/api/geocode";
import { listMyExpeditions } from "@/src/api/expeditions";
import type { ObservationReward } from "@/src/api/observations";
import { abandonPhotoReservation } from "@/src/api/photos";
import { queryClient } from "@/src/api/queryClient";
import { searchTaxa, type TaxonCatalogItem } from "@/src/api/taxa";
import { useAuthSession } from "@/src/auth/session";
import {
  ImperativeRequestSupersededError,
  runImperativeRequest,
} from "@/src/auth/requestBoundary";
import {
  activeProgress,
  expeditionRewardTarget,
  nextObjective,
} from "@/src/expeditions/logic";
import { ObservationFlowStepper } from "@/src/observation/ObservationFlowStepper";
import { useDraftStore } from "@/src/observation/draftStore";
import { mergeTaxonResults, searchCoreTaxa } from "@/src/observation/coreTaxa";
import { searchInstalledTaxa } from "@/src/observation/taxonomyPacks";
import { encodeGeohash4 } from "@/src/expeditions/geohash";
import {
  acknowledgeQueueCompletion,
  discardQueuedObservation,
  finalizeObservationDraft,
  removeQueuedObservation,
} from "@/src/observation/observationQueue";
import {
  kidSafeQueueMessage,
  syncQueuedObservation,
} from "@/src/observation/queueSync";
import type {
  ObservationIdentification,
  ObservationLocationSource,
  QueuedObservation,
} from "@/src/observation/queueTypes";
import { rewardLabel, type ObservationFlowStep } from "@/src/observation/presentation";
import { useObservationQueue } from "@/src/observation/useObservationQueue";
import { mayRenderLocalPhoto } from "@/src/observation/queuePolicy";
import { observationWorkIsCurrent } from "@/src/observation/workGuard";
import { SanctuaryRevealModal } from "@/src/sanctuary/SanctuaryRevealModal";

type LocationState = {
  geohash4: string | null;
  source: ObservationLocationSource;
  busy: boolean;
  message: string | null;
};

export default function ObserveSubmitScreen() {
  const session = useAuthSession();
  const draft = useDraftStore((state) => state.photo);
  const clearDraft = useDraftStore((state) => state.clear);
  const ownerUserId = session.status === "authenticated" ? session.user.id : null;
  const queue = useObservationQueue(ownerUserId);
  const record = useMemo(
    () => queue.items.find((item) => item.submissionKey === draft?.submissionKey) ?? null,
    [draft?.submissionKey, queue.items],
  );
  const [identification, setIdentification] = useState<ObservationIdentification>({
    source: "unknown",
    taxonId: null,
    speciesName: null,
  });
  const [location, setLocation] = useState<LocationState>({
    geohash4: null,
    source: "none",
    busy: false,
    message: null,
  });
  const [placeName, setPlaceName] = useState<string | null>(null);
  const [ecologyTags, setEcologyTags] = useState<Record<string, string>>({});
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState<TaxonCatalogItem[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [manualText, setManualText] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [revealVisible, setRevealVisible] = useState(false);
  const [visibleRewardCount, setVisibleRewardCount] = useState(0);
  const locationGenerationRef = useRef(0);
  const reverseGeocodeControllerRef = useRef<AbortController | null>(null);
  const selectedGeohashRef = useRef<string | null>(null);
  const cancelLocationWork = useCallback(() => {
    locationGenerationRef.current += 1;
    reverseGeocodeControllerRef.current?.abort();
    reverseGeocodeControllerRef.current = null;
  }, []);
  const chooseNoLocation = useCallback(() => {
    cancelLocationWork();
    selectedGeohashRef.current = null;
    setPlaceName(null);
    setLocation({
      geohash4: null,
      source: "none",
      busy: false,
      message: null,
    });
  }, [cancelLocationWork]);
  const mission = useQuery({
    queryKey: ["expeditions", ownerUserId ?? "anonymous", "me"],
    queryFn: ({ signal }) => listMyExpeditions(signal),
    retry: false,
    enabled: ownerUserId != null,
  });

  const rewards = record?.observation?.rewards ?? [];
  const visibleRewards = rewards.slice(0, visibleRewardCount);
  const sanctuaryRewards = rewards.filter(
    (reward) => reward.type === "world_unlock" || reward.type === "world_evolution",
  );

  useEffect(() => {
    cancelLocationWork();
    selectedGeohashRef.current = null;
    setIdentification({ source: "unknown", taxonId: null, speciesName: null });
    setLocation({ geohash4: null, source: "none", busy: false, message: null });
    setPlaceName(null);
    setEcologyTags({});
    setSearchText("");
    setSearchResults([]);
    setSearchBusy(false);
    setSearchMessage(null);
    setManualText("");
    setShowManual(false);
    setSyncing(false);
    setRevealVisible(false);
    setVisibleRewardCount(0);
  }, [cancelLocationWork, draft?.submissionKey, ownerUserId]);

  useEffect(() => {
    if (!record || record.payloadFrozen) return;
    selectedGeohashRef.current = record.geohash4;
    setIdentification(record.identification);
    setLocation({
      geohash4: record.geohash4,
      source: record.locationSource,
      busy: false,
      message: null,
    });
    setPlaceName(record.placeName);
    setEcologyTags(record.ecologyTags);
  }, [record?.submissionKey, record?.payloadFrozen]);

  useEffect(() => () => cancelLocationWork(), [cancelLocationWork]);

  useEffect(() => {
    if (!record || record.payloadFrozen || searchText.trim().length < 2) {
      setSearchResults([]);
      setSearchBusy(false);
      return;
    }
    const bundledResults = searchCoreTaxa(searchText);
    setSearchResults(bundledResults);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setSearchBusy(true);
      setSearchMessage(null);
      void searchInstalledTaxa(searchText.trim())
        .catch(() => [])
        .then((installedResults) => {
          const localResults = mergeTaxonResults(bundledResults, installedResults);
          if (!controller.signal.aborted) setSearchResults(localResults);
          return searchTaxa(searchText.trim(), controller.signal).then((response) => ({
            localResults,
            response,
          }));
        })
        .then(({ localResults, response }) => {
          if (controller.signal.aborted) return;
          const merged = mergeTaxonResults(localResults, response.items);
          setSearchResults(merged);
          if (merged.length === 0) {
            setSearchMessage("No catalog matches yet. You can save Unknown or type a note.");
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setSearchMessage("Catalog search is unavailable. Your draft is still safe.");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearchBusy(false);
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [record?.payloadFrozen, record?.submissionKey, searchText]);

  useEffect(() => {
    if (record?.stage !== "complete") return;
    if (visibleRewardCount < rewards.length) {
      const timer = setTimeout(
        () => setVisibleRewardCount((count) => Math.min(count + 1, rewards.length)),
        visibleRewardCount === 0 ? 150 : 550,
      );
      return () => clearTimeout(timer);
    }
    if (sanctuaryRewards.length > 0) setRevealVisible(true);
  }, [record?.stage, rewards.length, sanctuaryRewards.length, visibleRewardCount]);

  if (session.status === "initializing" || (ownerUserId && queue.loading)) {
    return <CenteredLoading />;
  }
  if (session.status !== "authenticated") {
    return <MissingDraft message="Sign in again to open this saved observation." />;
  }
  if (!draft || draft.ownerUserId !== session.user.id || !record) {
    return <MissingDraft message="This draft belongs to another account or is no longer on this device." />;
  }

  const editable = record.stage === "ready" && !record.payloadFrozen;
  const activeRecord = record;
  const activeMission = activeProgress(
    mission.data?.items ?? [],
    mission.data?.active_expedition_id,
  );
  const tagPrompt = nextObjective(activeMission)?.tag_prompt ?? null;
  const rewardTarget = expeditionRewardTarget(
    visibleRewards.filter(
      (reward) =>
        reward.type === "expedition_step" ||
        reward.type === "expedition_complete",
    ),
    activeMission?.expedition_id,
  );
  const currentStep: ObservationFlowStep =
    record.stage === "complete"
      ? "saved"
      : record.payloadFrozen || syncing
        ? "upload"
        : "identify";

  async function useCoarseLocation(): Promise<void> {
    cancelLocationWork();
    const generation = locationGenerationRef.current;
    const controller = new AbortController();
    reverseGeocodeControllerRef.current = controller;
    selectedGeohashRef.current = null;
    setPlaceName(null);
    setLocation((current) => ({ ...current, busy: true, message: null }));
    const expected = {
      ownerUserId: activeRecord.ownerUserId,
      submissionKey: activeRecord.submissionKey,
      generation,
    };
    const isCurrent = (geohash4?: string | null) => {
      const currentSession = useAuthSession.getState();
      const currentDraft = useDraftStore.getState().photo;
      return observationWorkIsCurrent(
        geohash4 === undefined ? expected : { ...expected, geohash4 },
        {
          ownerUserId:
            currentSession.status === "authenticated" ? currentSession.user.id : null,
          submissionKey: currentDraft?.submissionKey ?? null,
          generation: locationGenerationRef.current,
          geohash4: selectedGeohashRef.current,
        },
        controller.signal,
      );
    };
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (!isCurrent()) return;
      if (!permission.granted) {
        selectedGeohashRef.current = null;
        setLocation({
          geohash4: null,
          source: "none",
          busy: false,
          message: "Location is off. You can still save without it.",
        });
        return;
      }
      let position = await Location.getLastKnownPositionAsync({ maxAge: 5 * 60_000 });
      if (!isCurrent()) return;
      if (!position) {
        position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        if (!isCurrent()) return;
      }
      const coarse = encodeGeohash4(
        position.coords.latitude,
        position.coords.longitude,
      );
      if (!isCurrent()) return;
      // Do not retain coordinates in component or queue state.
      selectedGeohashRef.current = coarse;
      setLocation({
        geohash4: coarse,
        source: "device_coarse",
        busy: false,
        message: "Only a coarse four-character area will be saved.",
      });
      try {
        const response = await reverseGeocode(coarse, controller.signal);
        if (isCurrent(coarse)) setPlaceName(response.place_name);
      } catch {
        // A place label is optional; the coarse cell remains useful.
      }
    } catch {
      if (!isCurrent()) return;
      selectedGeohashRef.current = null;
      setLocation({
        geohash4: null,
        source: "none",
        busy: false,
        message: "Location could not be read. You can still save without it.",
      });
    } finally {
      if (reverseGeocodeControllerRef.current === controller) {
        reverseGeocodeControllerRef.current = null;
      }
    }
  }

  async function startSync(): Promise<void> {
    if (!editable) return;
    if (tagPrompt && !ecologyTags[tagPrompt.key]) {
      Alert.alert("One more clue", "Choose the visible life stage before saving.");
      return;
    }
    cancelLocationWork();
    setSyncing(true);
    try {
      const updated = await finalizeObservationDraft(activeRecord.ownerUserId, activeRecord.submissionKey, {
        geohash4: location.geohash4,
        locationSource: location.source,
        identification,
        placeName,
        ecologyTags,
      });
      await syncQueuedObservation(updated);
    } finally {
      setSyncing(false);
      await queue.reload();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["observations", activeRecord.ownerUserId] }),
        queryClient.invalidateQueries({ queryKey: ["dex", activeRecord.ownerUserId] }),
        queryClient.invalidateQueries({ queryKey: ["expeditions", activeRecord.ownerUserId] }),
        queryClient.invalidateQueries({ queryKey: ["sanctuary", activeRecord.ownerUserId] }),
      ]);
    }
  }

  function abandonFailedDraft(): void {
    Alert.alert(
      "Discard this upload?",
      "The private uploaded photo and saved draft will be removed.",
      [
        { text: "Keep it", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            void (async () => {
              setSyncing(true);
              try {
                if (activeRecord.photoId) {
                  try {
                    await runImperativeRequest((signal) =>
                      abandonPhotoReservation(activeRecord.photoId!, signal),
                    );
                  } catch (error) {
                    if (!(error instanceof ApiError && error.status === 404)) throw error;
                  }
                }
                await removeQueuedObservation(
                  activeRecord.ownerUserId,
                  activeRecord.submissionKey,
                );
                clearDraft();
                router.replace("/observe");
              } catch (error) {
                if (error instanceof ImperativeRequestSupersededError) return;
                const supportCode =
                  error instanceof ApiError ? error.body?.error.request_id : null;
                Alert.alert(
                  "Could not discard yet",
                  `The draft is still safe on this device.${supportCode ? ` Adult support code: ${supportCode}` : ""}`,
                );
              } finally {
                setSyncing(false);
              }
            })();
          },
        },
      ],
    );
  }

  function discardDraft(): void {
    Alert.alert(
      "Discard this draft?",
      "The photo and unsaved observation will be removed from this device.",
      [
        { text: "Keep it", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            void discardQueuedObservation(activeRecord.ownerUserId, activeRecord.submissionKey).then(() => {
              clearDraft();
              router.replace("/observe");
            });
          },
        },
      ],
    );
  }

  async function finish(
    destination: "/" | "/sanctuary" | `/expedition/${string}`,
  ): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["observations", activeRecord.ownerUserId] }),
      queryClient.invalidateQueries({ queryKey: ["dex", activeRecord.ownerUserId] }),
      queryClient.invalidateQueries({ queryKey: ["expeditions", activeRecord.ownerUserId] }),
      queryClient.invalidateQueries({ queryKey: ["sanctuary", activeRecord.ownerUserId] }),
    ]);
    await acknowledgeQueueCompletion(activeRecord.ownerUserId, activeRecord.submissionKey);
    clearDraft();
    setRevealVisible(false);
    router.replace(destination);
  }

  return (
    <ScrollView
      testID="observation-submit-screen"
      contentContainerStyle={styles.container}
    >
      <Stack.Screen options={{ title: "Save observation" }} />
      <ObservationFlowStepper current={currentStep} />

      <View
        testID={
          syncing ? "observation-sync-in-progress" : `observation-stage-${record.stage}`
        }
        style={styles.photoCard}
      >
        {mayRenderLocalPhoto(record) && !syncing ? (
          <Image
            source={{ uri: record.localUri }}
            style={styles.thumb}
            resizeMode="contain"
          />
        ) : (
          <View style={[styles.thumb, styles.privateThumb]}>
            <FontAwesome name="lock" size={22} color="#f0b44c" />
          </View>
        )}
        <View style={styles.photoCopy}>
          <Text style={styles.eyebrow}>Saved draft</Text>
          <Text style={styles.photoTitle}>{syncing ? "Saving…" : stageTitle(record)}</Text>
          <Text style={styles.meta}>
            {record.width} by {record.height}px · {new Date(record.observedAt).toLocaleString()}
          </Text>
        </View>
      </View>

      {editable && (
        <>
          <View style={styles.panel}>
            <PanelHeader icon="map-marker" title="Coarse place (optional)" />
            <Text style={styles.help}>
              {record.source === "library"
                ? "A library photo never uses your current location unless you choose it here."
                : "Add a broad area for place-based activities, or save without location."}
            </Text>
            <Text style={styles.value}>
              {location.geohash4 ? `Area ${location.geohash4}` : "No location"}
            </Text>
            {placeName ? <Text style={styles.value}>{placeName}</Text> : null}
            {location.message ? <Text style={styles.help}>{location.message}</Text> : null}
            <View style={styles.inlineActions}>
              <Pressable
                testID="observation-coarse-location-button"
                style={[styles.smallButton, styles.buttonGhost]}
                disabled={location.busy}
                onPress={() => void useCoarseLocation()}
              >
                {location.busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Use coarse area</Text>}
              </Pressable>
              <Pressable
                testID="observation-no-location-button"
                style={[styles.smallButton, styles.buttonGhost]}
                onPress={chooseNoLocation}
              >
                <Text style={styles.buttonText}>No location</Text>
              </Pressable>
            </View>
          </View>

          <View style={styles.panel}>
            <PanelHeader icon="search" title="What did you find?" />
            <Text style={styles.help}>
              Pick a canonical catalog organism for Dex credit. Notes and Unknown still save, but do not create a Dex species.
            </Text>
            {tagPrompt ? (
              <View style={styles.tagPrompt}>
                <Text style={styles.tagQuestion}>{tagPrompt.question}</Text>
                <View style={styles.tagOptions}>
                  {tagPrompt.options.map((option) => {
                    const selected = ecologyTags[tagPrompt.key] === option.value;
                    return (
                      <Pressable
                        key={option.value}
                        style={[
                          styles.tagOption,
                          selected && styles.tagOptionSelected,
                        ]}
                        onPress={() =>
                          setEcologyTags((current) => ({
                            ...current,
                            [tagPrompt.key]: option.value,
                          }))
                        }
                      >
                        <Text
                          style={[
                            styles.tagOptionText,
                            selected && styles.tagOptionTextSelected,
                          ]}
                        >
                          {option.label}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            ) : null}
            <TextInput
              style={styles.input}
              value={searchText}
              onChangeText={setSearchText}
              placeholder="Search the organism catalog"
              placeholderTextColor="#9aa39d"
              autoCapitalize="words"
            />
            {searchBusy ? <ActivityIndicator color="#f0b44c" /> : null}
            {searchMessage ? <Text style={styles.help}>{searchMessage}</Text> : null}
            {searchResults.map((taxon) => (
              <Pressable
                key={taxon.taxon_id}
                style={styles.suggestion}
                onPress={() => {
                  setIdentification({
                    source: "catalog",
                    taxonId: taxon.taxon_id,
                    speciesName: taxonDisplayName(taxon),
                  });
                  setSearchText(taxonDisplayName(taxon));
                  setSearchResults([]);
                }}
              >
                <View style={styles.suggestionCopy}>
                  <Text style={styles.suggestionName}>{taxonDisplayName(taxon)}</Text>
                  {taxon.scientific_name ? <Text style={styles.meta}>{taxon.scientific_name}</Text> : null}
                </View>
                <FontAwesome name="check-circle" size={16} color="#66a182" />
              </Pressable>
            ))}

            {identification.source !== "unknown" ? (
              <View style={styles.selectedIdentification}>
                <FontAwesome name="check" size={14} color="#86efac" />
                <Text style={styles.value}>{identification.speciesName}</Text>
              </View>
            ) : null}

            {!showManual ? (
              <Pressable style={[styles.suggestion, styles.suggestionGhost]} onPress={() => setShowManual(true)}>
                <Text style={styles.suggestionName}>Type a note instead</Text>
                <FontAwesome name="pencil" size={15} color="#f0b44c" />
              </Pressable>
            ) : (
              <View style={styles.manualBox}>
                <TextInput
                  style={styles.input}
                  value={manualText}
                  onChangeText={setManualText}
                  maxLength={200}
                  placeholder="Your organism note"
                  placeholderTextColor="#9aa39d"
                />
                <Pressable
                  style={[styles.smallButton, styles.buttonPrimary, !manualText.trim() && styles.buttonDisabled]}
                  disabled={!manualText.trim()}
                  onPress={() => setIdentification({ source: "manual_text", taxonId: null, speciesName: manualText.trim() })}
                >
                  <Text style={styles.buttonText}>Use note</Text>
                </Pressable>
              </View>
            )}
            <Pressable
              testID="observation-unknown-button"
              style={[styles.suggestion, styles.suggestionGhost]}
              onPress={() => setIdentification({ source: "unknown", taxonId: null, speciesName: null })}
            >
              <Text style={styles.suggestionName}>Save as Unknown</Text>
              {identification.source === "unknown" ? <FontAwesome name="check" size={15} color="#86efac" /> : null}
            </Pressable>
          </View>
        </>
      )}

      {record.payloadFrozen && record.stage !== "complete" ? (
        <View testID={`observation-stage-${record.stage}`} style={styles.panel}>
          <PanelHeader icon="cloud-upload" title={record.stage === "needs_attention" ? "Needs attention" : "Safely queued"} />
          {syncing ? <ActivityIndicator color="#f0b44c" /> : null}
          <Text style={record.stage === "needs_attention" ? styles.error : styles.help}>
            {kidSafeQueueMessage(record)}
          </Text>
          {record.nextAttemptAt ? (
            <Text style={styles.meta}>Next foreground try: {new Date(record.nextAttemptAt).toLocaleTimeString()}</Text>
          ) : null}
          {record.lastRequestId ? <Text style={styles.supportCode}>Adult support code: {record.lastRequestId}</Text> : null}
        </View>
      ) : null}

      {record.stage === "complete" ? (
        <View testID="observation-stage-complete" style={styles.panel}>
          <PanelHeader icon="check" title="Observation saved" />
          {record.observation?.dispatch_status === "partial" || record.observation?.dispatch_status === "pending" ? (
            <Text style={styles.help}>Your find is saved. A few rewards are still catching up.</Text>
          ) : null}
          <RewardList rewards={visibleRewards} />
          {rewardTarget.primary ? (
            <View style={styles.expeditionCard}>
              <Text style={styles.expeditionRewardKicker}>Quest progress</Text>
              <Text style={styles.expeditionRewardTitle}>
                {rewardTarget.primary.title}
              </Text>
              {rewardTarget.primary.detail ? (
                <Text style={styles.meta}>{rewardTarget.primary.detail}</Text>
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}

      <View style={styles.actions}>
        {editable ? (
          <Pressable style={[styles.button, styles.buttonGhost]} onPress={discardDraft}>
            <Text style={styles.buttonText}>Discard</Text>
          </Pressable>
        ) : record.stage !== "complete" ? (
          <Pressable style={[styles.button, styles.buttonGhost]} onPress={() => router.replace("/observe")}>
            <Text style={styles.buttonText}>Back</Text>
          </Pressable>
        ) : null}
        {editable ? (
          <Pressable
            testID="observation-save-button"
            style={[styles.button, styles.buttonPrimary, syncing && styles.buttonDisabled]}
            disabled={syncing}
            onPress={() => void startSync()}
          >
            {syncing ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Save observation</Text>}
          </Pressable>
        ) : record.stage === "complete" ? (
          <>
            {rewardTarget.expeditionId ? (
              <Pressable
                style={[styles.button, styles.buttonGhost]}
                onPress={() =>
                  void finish(`/expedition/${rewardTarget.expeditionId}`)
                }
              >
                <Text style={styles.buttonText}>View Expedition</Text>
              </Pressable>
            ) : null}
            <Pressable
              testID="observation-done-button"
              style={[styles.button, styles.buttonPrimary]}
              onPress={() => void finish("/")}
            >
              <Text style={styles.buttonText}>Done</Text>
            </Pressable>
          </>
        ) : null}
      </View>
      {record.stage === "needs_attention" && !syncing ? (
        <Pressable style={styles.discardLink} onPress={abandonFailedDraft}>
          <Text style={styles.discardLinkText}>Discard this private upload</Text>
        </Pressable>
      ) : null}

      <SanctuaryRevealModal
        visible={revealVisible}
        reward={sanctuaryRewards[0] ?? null}
        extraRewardCount={Math.max(0, sanctuaryRewards.length - 1)}
        onSeeSanctuary={() => void finish("/sanctuary")}
        onDone={() => void finish("/")}
      />
    </ScrollView>
  );
}

function CenteredLoading() {
  return (
    <View style={styles.center}>
      <ActivityIndicator color="#f0b44c" />
    </View>
  );
}

function MissingDraft({ message }: { message: string }) {
  return (
    <View style={styles.center}>
      <Stack.Screen options={{ title: "Save observation" }} />
      <Text style={styles.help}>{message}</Text>
      <Pressable style={[styles.button, styles.buttonGhost, styles.centerButton]} onPress={() => router.replace("/observe")}>
        <Text style={styles.buttonText}>Back to Observe</Text>
      </Pressable>
    </View>
  );
}

function PanelHeader({ icon, title }: { icon: string; title: string }) {
  return (
    <View style={styles.panelHeader}>
      <View style={styles.panelIcon}>
        <FontAwesome name={icon as never} size={14} color="#f0b44c" />
      </View>
      <Text style={styles.panelTitle}>{title}</Text>
    </View>
  );
}

function RewardList({ rewards }: { rewards: ObservationReward[] }) {
  if (rewards.length === 0) {
    return <Text style={styles.help}>Your observation is in the field log.</Text>;
  }
  return (
    <View style={styles.rewardList}>
      {rewards.map((reward, index) => (
        <View key={`${reward.type}-${index}`} style={styles.rewardCard}>
          <Text style={styles.rewardBadge}>{rewardLabel(reward.type)}</Text>
          <Text style={styles.rewardTitle}>{reward.title}</Text>
          <Text style={styles.meta}>{reward.detail}</Text>
        </View>
      ))}
    </View>
  );
}

function stageTitle(record: QueuedObservation): string {
  switch (record.stage) {
    case "ready":
      return "Ready to finish";
    case "presigned":
      return "Upload reserved";
    case "uploaded":
      return "Photo uploaded";
    case "complete":
      return "Saved";
    case "needs_attention":
      return "Needs attention";
    case "abandoned":
      return "Discarding";
  }
}

function taxonDisplayName(taxon: TaxonCatalogItem): string {
  return taxon.common_name ?? taxon.scientific_name ?? "Unknown taxon";
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, padding: 18, paddingBottom: 36, backgroundColor: "#07130f" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, backgroundColor: "#07130f" },
  centerButton: { flex: 0, minWidth: 160, marginTop: 12 },
  photoCard: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 12, padding: 10, borderRadius: 8, backgroundColor: "#111916", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)" },
  thumb: { width: 96, height: 96, borderRadius: 7, backgroundColor: "#18241f" },
  privateThumb: { alignItems: "center", justifyContent: "center" },
  photoCopy: { flex: 1, backgroundColor: "transparent" },
  eyebrow: { fontSize: 11, fontWeight: "800", color: "#66a182", textTransform: "uppercase" },
  photoTitle: { fontSize: 20, fontWeight: "900", marginTop: 4, color: "#fff" },
  panel: { marginTop: 14, padding: 14, borderRadius: 8, backgroundColor: "#111916", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)" },
  panelHeader: { flexDirection: "row", alignItems: "center", gap: 9, marginBottom: 8, backgroundColor: "transparent" },
  panelIcon: { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(240,180,76,0.14)" },
  panelTitle: { fontSize: 16, fontWeight: "900", color: "#fff" },
  value: { flexShrink: 1, fontSize: 14, fontWeight: "700", color: "#fff" },
  help: { fontSize: 13, lineHeight: 18, opacity: 0.72, marginTop: 4, marginBottom: 8, color: "#fff", textAlign: "left" },
  meta: { fontSize: 12, lineHeight: 17, opacity: 0.68, marginTop: 3, color: "#fff" },
  supportCode: { fontFamily: "SpaceMono", fontSize: 10, color: "#f0b44c", marginTop: 8 },
  error: { fontSize: 14, lineHeight: 20, color: "#f87171", marginTop: 4 },
  input: { width: "100%", minHeight: 44, borderColor: "rgba(255,255,255,0.3)", borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, marginVertical: 8, fontSize: 14, color: "#fff" },
  suggestion: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 10, paddingVertical: 12, paddingHorizontal: 12, marginTop: 8, borderRadius: 8, backgroundColor: "#18241f" },
  suggestionGhost: { backgroundColor: "transparent", borderColor: "rgba(255,255,255,0.24)", borderWidth: 1 },
  suggestionCopy: { flex: 1, backgroundColor: "transparent" },
  suggestionName: { flexShrink: 1, fontSize: 15, fontWeight: "800", color: "#fff" },
  selectedIdentification: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10, padding: 10, borderRadius: 8, backgroundColor: "rgba(102,161,130,0.16)" },
  manualBox: { marginTop: 8, backgroundColor: "transparent" },
  tagPrompt: { marginTop: 10, marginBottom: 8, padding: 12, borderRadius: 8, backgroundColor: "#eef7ed", borderColor: "#8bbf86", borderWidth: StyleSheet.hairlineWidth },
  tagQuestion: { color: "#14351f", fontSize: 14, fontWeight: "700", marginBottom: 8 },
  tagOptions: { flexDirection: "row", flexWrap: "wrap", gap: 8, backgroundColor: "transparent" },
  tagOption: { paddingHorizontal: 10, paddingVertical: 7, borderRadius: 16, backgroundColor: "#fff", borderColor: "#9ca3af", borderWidth: StyleSheet.hairlineWidth },
  tagOptionSelected: { backgroundColor: "#267344", borderColor: "#267344" },
  tagOptionText: { color: "#14351f", fontSize: 12, fontWeight: "700" },
  tagOptionTextSelected: { color: "#fff" },
  inlineActions: { flexDirection: "row", gap: 8, marginTop: 10, backgroundColor: "transparent" },
  actions: { flexDirection: "row", gap: 10, marginTop: 18, backgroundColor: "transparent" },
  button: { minHeight: 46, paddingHorizontal: 16, paddingVertical: 11, borderRadius: 8, flex: 1, alignItems: "center", justifyContent: "center" },
  smallButton: { minHeight: 42, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 8, flex: 1, alignItems: "center", justifyContent: "center" },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: { borderColor: "rgba(255,255,255,0.32)", borderWidth: 1, backgroundColor: "transparent" },
  buttonDisabled: { opacity: 0.42 },
  buttonText: { fontSize: 14, color: "#fff", fontWeight: "900" },
  rewardList: { gap: 8, marginTop: 12, backgroundColor: "transparent" },
  rewardCard: { padding: 12, borderRadius: 8, backgroundColor: "#18241f", borderWidth: 1, borderColor: "rgba(102,161,130,0.28)" },
  rewardBadge: { alignSelf: "flex-start", paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, overflow: "hidden", backgroundColor: "rgba(102,161,130,0.22)", color: "#fff", fontSize: 10, fontWeight: "900" },
  rewardTitle: { fontSize: 16, fontWeight: "900", marginTop: 8, color: "#fff" },
  expeditionCard: { marginTop: 12, padding: 12, borderRadius: 8, backgroundColor: "rgba(47,111,235,0.13)", borderWidth: 1, borderColor: "rgba(96,165,250,0.35)" },
  expeditionRewardKicker: { color: "#93c5fd", fontSize: 11, fontWeight: "900", textTransform: "uppercase" },
  expeditionRewardTitle: { color: "#fff", fontSize: 16, fontWeight: "900", marginTop: 5 },
  discardLink: { alignItems: "center", padding: 12, marginTop: 8 },
  discardLinkText: { color: "#fca5a5", fontSize: 13, fontWeight: "700" },
});
