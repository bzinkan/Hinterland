import FontAwesome from "@expo/vector-icons/FontAwesome";
import { FlashList } from "@shopify/flash-list";
import { router, type Href } from "expo-router";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  AppState,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  useWindowDimensions,
} from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import type { DexListItem } from "@/src/api/dex";
import type { ObservationListItem } from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import { useAuthSession } from "@/src/auth/session";
import { clearBearerToken } from "@/src/auth/token";
import { childSafeError } from "@/src/observation/childSafeErrors";
import {
  childPhotoPresentation,
  DEFAULT_JOURNAL_MODE,
  findCountLabel,
  isUrlUsable,
  journalColumnCount,
  journalCaption,
  queueStatusMessage,
  representativePhotoId,
  speciesDisplayName,
  speciesSubtitle,
  waitingQueueItems,
  type JournalMode,
  visibleJournalItems,
} from "@/src/observation/journalLogic";
import type { QueuedObservation } from "@/src/observation/queueTypes";
import { useMyDex } from "@/src/observation/useMyDex";
import { useMyObservations } from "@/src/observation/useMyObservations";
import { useObservationQueue } from "@/src/observation/useObservationQueue";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";

export default function FieldJournalScreen() {
  const session = useAuthSession();
  const { width, fontScale } = useWindowDimensions();
  const columnCount = journalColumnCount(width, fontScale);
  const ownerUserId =
    session.status === "authenticated" ? session.user.id : null;
  const [mode, setMode] = useState<JournalMode>(DEFAULT_JOURNAL_MODE);
  const observations = useMyObservations();
  const dex = useMyDex();
  const {
    items: queueRows,
    loading: queueLoading,
    reload: reloadQueue,
  } = useObservationQueue(ownerUserId);

  const photoItems = visibleJournalItems(
    observations.data?.pages.flatMap((p) => p.items) ?? [],
  );
  const speciesItems = dex.data?.pages.flatMap((p) => p.items) ?? [];
  const queuedItems = useMemo(
    () => waitingQueueItems(queueRows, photoItems),
    [photoItems, queueRows],
  );

  const onRefresh = useCallback(() => {
    if (!ownerUserId) return;
    void observations.refetch();
    void dex.refetch();
    void reloadQueue();
    void queryClient.refetchQueries({
      queryKey: ["photo-url", ownerUserId],
      type: "active",
    });
  }, [dex, observations, ownerUserId, reloadQueue]);

  useEffect(() => {
    if (!ownerUserId) return;
    const timer = setInterval(onRefresh, 30_000);
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") onRefresh();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [onRefresh, ownerUserId]);

  const changeMode = useCallback((nextMode: JournalMode) => {
    setMode(nextMode);
    AccessibilityInfo.announceForAccessibility(
      `${nextMode === "photos" ? "Photos" : "Species"} selected`,
    );
  }, []);

  if (session.status === "initializing") {
    return (
      <DesktopContainer>
        <View style={styles.center}><ActivityIndicator /></View>
      </DesktopContainer>
    );
  }

  if (session.status === "anonymous") {
    return (
      <DesktopContainer>
        <View style={styles.center}>
          <Text style={styles.heading}>Sign in to open your Field Journal</Text>
          <Text style={styles.body}>Each account keeps its finds and photos separate.</Text>
          <Pressable
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => router.push("/sign-in")}
          >
            <Text style={styles.buttonText}>Sign in</Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  const header = (
    <JournalHeader
      mode={mode}
      onModeChange={changeMode}
      photoCount={formatLoadedCount(photoItems.length, observations.hasNextPage)}
      speciesCount={formatLoadedCount(speciesItems.length, dex.hasNextPage)}
      queuedItems={mode === "photos" ? queuedItems : []}
      queueLoading={mode === "photos" && queueLoading}
      singleColumn={columnCount === 1}
    />
  );

  const renderSpecies = useCallback(
    ({ item }: { item: DexListItem }) => <SpeciesCard item={item} />,
    [],
  );
  const renderObservation = useCallback(
    ({ item }: { item: ObservationListItem }) => <JournalCard item={item} />,
    [],
  );

  if (mode === "species") {
    return (
      <DesktopContainer>
        <FlashList
          key={`species-${columnCount}`}
          testID="field-journal-screen"
          data={speciesItems}
          keyExtractor={(item) => item.id}
          numColumns={columnCount}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={dex.isRefetching || observations.isRefetching}
              onRefresh={onRefresh}
            />
          }
          ListHeaderComponent={header}
          ListEmptyComponent={
            <JournalBodyState
              pending={dex.isPending}
              error={dex.error}
              emptyTitle="No species yet"
              emptyBody="Choose a catalog organism when saving a photo to add it here."
              onRetry={onRefresh}
            />
          }
          renderItem={renderSpecies}
          ListFooterComponent={
            dex.hasNextPage ? (
              <LoadMoreButton
                loading={dex.isFetchingNextPage}
                onPress={() => void dex.fetchNextPage()}
              />
            ) : null
          }
        />
      </DesktopContainer>
    );
  }

  return (
    <DesktopContainer>
      <FlashList
        key={`photos-${columnCount}`}
        testID="field-journal-screen"
        data={photoItems}
        keyExtractor={(item) => item.id}
        numColumns={columnCount}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={observations.isRefetching || dex.isRefetching}
            onRefresh={onRefresh}
          />
        }
        ListHeaderComponent={header}
        ListEmptyComponent={
          <JournalBodyState
            pending={observations.isPending}
            error={observations.error}
            emptyTitle="Your Field Journal is empty"
            emptyBody="Tap the Observe tab to log your first discovery."
            onRetry={onRefresh}
          />
        }
        renderItem={renderObservation}
        ListFooterComponent={
          observations.hasNextPage ? (
            <LoadMoreButton
              loading={observations.isFetchingNextPage}
              onPress={() => void observations.fetchNextPage()}
            />
          ) : null
        }
      />
    </DesktopContainer>
  );
}

function JournalHeader({
  mode,
  onModeChange,
  photoCount,
  speciesCount,
  queuedItems,
  queueLoading,
  singleColumn,
}: {
  mode: JournalMode;
  onModeChange: (mode: JournalMode) => void;
  photoCount: string;
  speciesCount: string;
  queuedItems: QueuedObservation[];
  queueLoading: boolean;
  singleColumn: boolean;
}) {
  return (
    <View style={styles.header}>
      <Text accessibilityRole="header" style={styles.title}>Field Journal</Text>
      <View style={styles.statsRow}>
        <Stat label="Photos" value={photoCount} />
        <Stat label="Species" value={speciesCount} />
      </View>
      <View style={styles.segment}>
        <SegmentButton
          active={mode === "photos"}
          label="Photos"
          onPress={() => onModeChange("photos")}
        />
        <SegmentButton
          active={mode === "species"}
          label="Species"
          onPress={() => onModeChange("species")}
        />
      </View>
      {queueLoading || queuedItems.length > 0 ? (
        <WaitingToSyncSection
          loading={queueLoading}
          items={queuedItems}
          singleColumn={singleColumn}
        />
      ) : null}
    </View>
  );
}

function WaitingToSyncSection({
  loading,
  items,
  singleColumn,
}: {
  loading: boolean;
  items: QueuedObservation[];
  singleColumn: boolean;
}) {
  return (
    <View style={styles.queueSection} testID="waiting-to-sync-section">
      <Text accessibilityRole="header" style={styles.queueHeading}>
        Waiting to sync
      </Text>
      {loading && items.length === 0 ? <ActivityIndicator /> : null}
      <View style={styles.queueGrid}>
        {items.map((item) => (
          <QueuedJournalCard
            key={item.submissionKey}
            item={item}
            singleColumn={singleColumn}
          />
        ))}
      </View>
    </View>
  );
}

function QueuedJournalCard({
  item,
  singleColumn,
}: {
  item: QueuedObservation;
  singleColumn: boolean;
}) {
  const caption = journalCaption(item.identification.speciesName);
  const status = queueStatusMessage(item);
  return (
    <View
      accessible
      accessibilityRole="summary"
      accessibilityLabel={`${caption}. ${status}`}
      style={[
        styles.card,
        styles.queueCard,
        singleColumn && styles.queueCardWide,
      ]}
    >
      <View style={styles.thumbPlaceholder}>
        <FontAwesome name="cloud-upload" size={24} color="#fff" />
        <Text style={styles.placeholderText}>{status}</Text>
      </View>
      <Text style={styles.cardTitle}>{caption}</Text>
      <Text style={styles.cardMeta}>
        {formatDate(new Date(item.observedAt))}
        {item.placeName ? ` - ${item.placeName}` : ""}
      </Text>
      {item.lastRequestId ? (
        <Text style={styles.supportCode}>
          Adult support code: {item.lastRequestId}
        </Text>
      ) : null}
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function SegmentButton({
  active,
  label,
  onPress,
}: {
  active: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={`${label} Field Journal tab`}
      accessibilityHint={`Shows saved ${label.toLowerCase()}`}
      accessibilityState={{ selected: active }}
      style={[styles.segmentButton, active && styles.segmentButtonActive]}
      onPress={onPress}
    >
      <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{label}</Text>
    </Pressable>
  );
}

function JournalBodyState({
  pending,
  error,
  emptyTitle,
  emptyBody,
  onRetry,
}: {
  pending: boolean;
  error: Error | null;
  emptyTitle: string;
  emptyBody: string;
  onRetry: () => void;
}) {
  if (pending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  if (error) {
    const safe = childSafeError(error);

    return (
      <View style={styles.center}>
        <Text style={styles.heading}>Couldn't open your Field Journal</Text>
        <Text style={styles.body}>{safe.message}</Text>
        {safe.supportCode ? (
          <Text style={styles.supportCode}>
            Adult support code: {safe.supportCode}
          </Text>
        ) : null}
        {safe.requiresAdultHandoff ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Ask an adult to sign in again"
            accessibilityHint="Clears the expired session and opens kid handoff"
            style={[styles.button, styles.buttonGhost]}
            onPress={() => {
              void clearBearerToken().finally(() => router.replace("/kid-handoff"));
            }}
          >
            <Text style={styles.buttonText}>Ask an adult</Text>
          </Pressable>
        ) : (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Retry Field Journal"
            style={[styles.button, styles.buttonGhost]}
            onPress={onRetry}
          >
            <Text style={styles.buttonText}>Retry</Text>
          </Pressable>
        )}
      </View>
    );
  }

  return (
    <View style={styles.center}>
      <Text style={styles.heading}>{emptyTitle}</Text>
      <Text style={styles.body}>{emptyBody}</Text>
    </View>
  );
}

const JournalCard = memo(function JournalCard({ item }: { item: ObservationListItem }) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const presentation = childPhotoPresentation(item.child_presentation_status);
  const ts = new Date(item.observed_at);
  const caption = journalCaption(item.species_name);

  useEffect(() => {
    if (presentation.mode === "image") return;
    queryClient.removeQueries({
      queryKey: ["photo-url", ownerUserId ?? "anonymous", item.photo_id],
      exact: true,
    });
  }, [item.photo_id, ownerUserId, presentation.mode]);

  return (
    <Pressable
      testID="field-journal-observation-card"
      accessibilityRole="button"
      accessibilityLabel={`${caption}. ${formatDate(ts)}${item.place_name ? `. ${item.place_name}` : ""}${presentation.message ? `. ${presentation.message}` : ""}`}
      accessibilityHint="Opens this Field Journal entry"
      style={styles.card}
      onPress={() => router.push(observationHref(item.id))}
    >
      {presentation.mode === "image" ? (
        <JournalThumb photoId={item.photo_id} description={`Photo of ${caption}`} />
      ) : (
        <UnavailableThumb
          status={presentation.status}
          message={presentation.message}
          testID={
            presentation.status === "pilot_private"
              ? "field-journal-private-status"
              : undefined
          }
        />
      )}
      <Text style={styles.cardTitle}>{caption}</Text>
      <Text style={styles.cardMeta}>
        {formatDate(ts)}
        {item.place_name ? ` - ${item.place_name}` : ""}
      </Text>
    </Pressable>
  );
});

const SpeciesCard = memo(function SpeciesCard({ item }: { item: DexListItem }) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const photoId = representativePhotoId(item);
  const previousPhotoId = useRef(photoId);
  const firstSeen = new Date(item.first_seen_at);
  const name = speciesDisplayName(item);

  useEffect(() => {
    const previous = previousPhotoId.current;
    previousPhotoId.current = photoId;
    if (!previous || previous === photoId) return;
    queryClient.removeQueries({
      queryKey: ["photo-url", ownerUserId ?? "anonymous", previous],
      exact: true,
    });
  }, [ownerUserId, photoId]);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${name}. ${findCountLabel(item.observation_count)}. First seen ${formatDate(firstSeen)}`}
      accessibilityHint="Opens the first accepted Field Journal entry for this species"
      style={styles.card}
      onPress={() => router.push(observationHref(item.first_observation_id))}
    >
      {photoId ? (
        <JournalThumb photoId={photoId} description={`Representative photo of ${name}`} />
      ) : (
        <UnavailableThumb status="failed" message="No approved photo is available yet." />
      )}
      <Text style={styles.cardTitle}>{name}</Text>
      <Text style={styles.cardMeta}>
        {speciesSubtitle(item)}
      </Text>
      <Text style={styles.cardMeta}>
        {findCountLabel(item.observation_count)} - first seen {formatDate(firstSeen)}
      </Text>
    </Pressable>
  );
});

function JournalThumb({
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
  const [loadRetried, setLoadRetried] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  if (urlQuery.isError || loadFailed) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Retry ${description}`}
        accessibilityHint="Requests a new private photo link"
        style={styles.thumbPlaceholder}
        onPress={(event) => {
          event.stopPropagation();
          setLoadFailed(false);
          setLoadRetried(false);
          void urlQuery.refetch();
        }}
      >
        <FontAwesome name="refresh" size={24} color="#fff" />
        <Text style={styles.placeholderText}>Tap to retry</Text>
      </Pressable>
    );
  }

  if (urlQuery.isPending || !isUrlUsable(urlQuery.data.expires_at)) {
    return (
      <View style={styles.thumbPlaceholder}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <View style={styles.thumbWrap}>
      <Image
        testID="field-journal-photo-image"
        accessible
        accessibilityLabel={description}
        source={{ uri: urlQuery.data.url }}
        style={styles.thumb}
        resizeMode="cover"
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
    </View>
  );
}

function UnavailableThumb({
  status,
  message,
  testID,
}: {
  status: string;
  message: string | null;
  testID?: string;
}) {
  return (
    <View
      testID={testID}
      accessible
      accessibilityRole="text"
      accessibilityLabel={message ?? "Photo unavailable"}
      style={styles.thumbPlaceholder}
    >
      <FontAwesome
        name={status === "adult_review" ? "search" : "lock"}
        size={24}
        color="#fff"
      />
      <Text style={styles.placeholderText}>
        {message ?? "Photo unavailable"}
      </Text>
    </View>
  );
}

function LoadMoreButton({
  loading,
  onPress,
}: {
  loading: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={loading ? "Loading more entries" : "Load more entries"}
      accessibilityState={{ disabled: loading, busy: loading }}
      style={[styles.button, styles.buttonGhost, styles.loadMore]}
      disabled={loading}
      onPress={onPress}
    >
      <Text style={styles.buttonText}>{loading ? "Loading..." : "Load more"}</Text>
    </Pressable>
  );
}

function formatLoadedCount(count: number, hasMore: boolean): string {
  return `${count}${hasMore ? "+" : ""}`;
}

function formatDate(date: Date): string {
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return date.toLocaleDateString();
}

function observationHref(id: string): Href {
  return {
    pathname: "/observation/[id]",
    params: { id },
  } as unknown as Href;
}

const styles = StyleSheet.create({
  list: {
    padding: 12,
    paddingBottom: 28,
  },
  header: {
    paddingTop: 8,
    paddingBottom: 16,
  },
  title: {
    fontSize: 26,
    fontWeight: "700",
  },
  statsRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 12,
  },
  stat: {
    flex: 1,
    borderColor: "rgba(255,255,255,0.16)",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
  },
  statValue: {
    fontSize: 18,
    fontWeight: "700",
  },
  statLabel: {
    fontSize: 12,
    opacity: 0.62,
    marginTop: 2,
  },
  segment: {
    flexDirection: "row",
    borderColor: "rgba(255,255,255,0.16)",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    padding: 3,
    marginTop: 12,
  },
  segmentButton: {
    flex: 1,
    minHeight: 44,
    borderRadius: 6,
    alignItems: "center",
    justifyContent: "center",
  },
  segmentButtonActive: {
    backgroundColor: "#2f6feb",
  },
  segmentText: {
    fontSize: 14,
    fontWeight: "600",
    opacity: 0.72,
  },
  segmentTextActive: {
    opacity: 1,
    color: "#fff",
  },
  gridRow: {
    gap: 12,
  },
  center: {
    minHeight: 260,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: {
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 8,
    textAlign: "center",
  },
  body: {
    fontSize: 14,
    opacity: 0.7,
    textAlign: "center",
    marginBottom: 16,
  },
  card: {
    flex: 1,
    maxWidth: "100%",
    minWidth: 0,
    marginBottom: 16,
  },
  queueSection: {
    marginTop: 18,
    gap: 10,
  },
  queueHeading: {
    fontSize: 18,
    fontWeight: "700",
  },
  queueGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  queueCard: {
    flexBasis: "46%",
  },
  queueCardWide: {
    flexBasis: "100%",
  },
  thumbWrap: {
    position: "relative",
  },
  thumb: {
    width: "100%",
    aspectRatio: 1,
    borderRadius: 8,
    backgroundColor: "#1a1a1a",
  },
  thumbPlaceholder: {
    width: "100%",
    aspectRatio: 1,
    borderRadius: 8,
    backgroundColor: "#1a1a1a",
    alignItems: "center",
    justifyContent: "center",
    padding: 12,
    gap: 8,
  },
  placeholderText: {
    fontSize: 12,
    opacity: 0.72,
    textAlign: "center",
  },
  cardTitle: {
    fontSize: 14,
    fontWeight: "600",
    marginTop: 7,
  },
  cardMeta: {
    fontSize: 11,
    opacity: 0.62,
    marginTop: 2,
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
  },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
  loadMore: {
    marginTop: 4,
    marginHorizontal: 16,
  },
  supportCode: {
    fontSize: 11,
    opacity: 0.62,
    marginTop: 8,
    marginBottom: 8,
  },
});
