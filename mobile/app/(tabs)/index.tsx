import FontAwesome from "@expo/vector-icons/FontAwesome";
import { router, type Href } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
} from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import type { DexListItem } from "@/src/api/dex";
import type { ObservationListItem } from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import {
  DEFAULT_JOURNAL_MODE,
  findCountLabel,
  isAwaitingModeration,
  isUrlUsable,
  journalCaption,
  photoDisplayMode,
  speciesDisplayName,
  speciesSubtitle,
  type JournalMode,
} from "@/src/observation/journalLogic";
import { useMyDex } from "@/src/observation/useMyDex";
import { useMyObservations } from "@/src/observation/useMyObservations";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";

export default function FieldJournalScreen() {
  const [mode, setMode] = useState<JournalMode>(DEFAULT_JOURNAL_MODE);
  const observations = useMyObservations();
  const dex = useMyDex();

  const photoItems = observations.data?.pages.flatMap((p) => p.items) ?? [];
  const speciesItems = dex.data?.pages.flatMap((p) => p.items) ?? [];

  const onRefresh = useCallback(() => {
    void observations.refetch();
    void dex.refetch();
    void queryClient.refetchQueries({ queryKey: ["photo-url"], type: "active" });
  }, [dex, observations]);

  const header = (
    <JournalHeader
      mode={mode}
      onModeChange={setMode}
      photoCount={formatLoadedCount(photoItems.length, observations.hasNextPage)}
      speciesCount={formatLoadedCount(speciesItems.length, dex.hasNextPage)}
    />
  );

  if (mode === "species") {
    return (
      <DesktopContainer>
        <FlatList
          data={speciesItems}
          keyExtractor={(item) => item.id}
          numColumns={2}
          columnWrapperStyle={styles.gridRow}
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
              emptyBody="Choose an iNaturalist match when saving a photo to add it here."
              onRetry={onRefresh}
            />
          }
          renderItem={({ item }) => <SpeciesCard item={item} />}
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
      <FlatList
        data={photoItems}
        keyExtractor={(item) => item.id}
        numColumns={2}
        columnWrapperStyle={styles.gridRow}
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
        renderItem={({ item }) => <JournalCard item={item} />}
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
}: {
  mode: JournalMode;
  onModeChange: (mode: JournalMode) => void;
  photoCount: string;
  speciesCount: string;
}) {
  return (
    <View style={styles.header}>
      <Text style={styles.title}>Field Journal</Text>
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
      accessibilityRole="button"
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
    const isUnauthed = error instanceof ApiError && error.status === 401;
    if (isUnauthed) {
      return (
        <View style={styles.center}>
          <Text style={styles.heading}>{emptyTitle}</Text>
          <Text style={styles.body}>{emptyBody}</Text>
        </View>
      );
    }

    return (
      <View style={styles.center}>
        <Text style={styles.heading}>Couldn't open your Field Journal</Text>
        <Text style={styles.body}>{error.message}</Text>
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={onRetry}>
          <Text style={styles.buttonText}>Retry</Text>
        </Pressable>
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

function JournalCard({ item }: { item: ObservationListItem }) {
  const mode = photoDisplayMode(item.photo_status);
  const ts = new Date(item.created_at);

  return (
    <Pressable
      style={styles.card}
      onPress={() => router.push(observationHref(item.id))}
    >
      {mode === "image" ? (
        <JournalThumb
          photoId={item.photo_id}
          checking={isAwaitingModeration(item.photo_status)}
        />
      ) : (
        <UnavailableThumb mode={mode} />
      )}
      <Text style={styles.cardTitle} numberOfLines={1}>
        {journalCaption(item.species_name)}
      </Text>
      <Text style={styles.cardMeta} numberOfLines={1}>
        {formatDate(ts)}
        {item.place_name ? ` - ${item.place_name}` : ""}
      </Text>
    </Pressable>
  );
}

function SpeciesCard({ item }: { item: DexListItem }) {
  const mode = photoDisplayMode(item.first_photo_status);
  const firstSeen = new Date(item.first_seen_at);

  return (
    <Pressable
      style={styles.card}
      onPress={() => router.push(observationHref(item.first_observation_id))}
    >
      {mode === "image" ? (
        <JournalThumb
          photoId={item.first_photo_id}
          checking={isAwaitingModeration(item.first_photo_status)}
        />
      ) : (
        <UnavailableThumb mode={mode} />
      )}
      <Text style={styles.cardTitle} numberOfLines={1}>
        {speciesDisplayName(item)}
      </Text>
      <Text style={styles.cardMeta} numberOfLines={1}>
        {speciesSubtitle(item)}
      </Text>
      <Text style={styles.cardMeta} numberOfLines={1}>
        {findCountLabel(item.observation_count)} - first seen {formatDate(firstSeen)}
      </Text>
    </Pressable>
  );
}

function JournalThumb({
  photoId,
  checking,
}: {
  photoId: string;
  checking: boolean;
}) {
  const urlQuery = usePhotoUrl(photoId, true);
  const [loadRetried, setLoadRetried] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  if (urlQuery.isError || loadFailed) {
    return (
      <Pressable
        style={styles.thumbPlaceholder}
        onPress={() => {
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
        source={{ uri: urlQuery.data.url }}
        style={styles.thumb}
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
        <View style={styles.checkingBadge}>
          <Text style={styles.checkingBadgeText}>checking...</Text>
        </View>
      )}
    </View>
  );
}

function UnavailableThumb({ mode }: { mode: "reviewing" | "removed" }) {
  return (
    <View style={styles.thumbPlaceholder}>
      <FontAwesome
        name={mode === "reviewing" ? "search" : "ban"}
        size={24}
        color="#fff"
      />
      <Text style={styles.placeholderText}>
        {mode === "reviewing" ? "An adult is checking this photo" : "Photo removed"}
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
    minHeight: 36,
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
    maxWidth: "50%",
    marginBottom: 16,
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
  checkingBadge: {
    position: "absolute",
    top: 6,
    right: 6,
    backgroundColor: "rgba(0,0,0,0.65)",
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  checkingBadgeText: {
    fontSize: 10,
    color: "#fff",
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
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: "center",
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
});
