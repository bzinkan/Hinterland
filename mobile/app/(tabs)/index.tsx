import { router } from "expo-router";
import { useCallback } from "react";
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
import type { ObservationListItem } from "@/src/api/observations";
import {
  galleryCaption,
  isAwaitingModeration,
  photoDisplayMode,
} from "@/src/observation/galleryLogic";
import { useMyObservations } from "@/src/observation/useMyObservations";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";

export default function HomeScreen() {
  const query = useMyObservations();

  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  const onRefresh = useCallback(() => {
    void query.refetch();
  }, [query]);

  if (query.isPending) {
    return (
      <DesktopContainer>
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      </DesktopContainer>
    );
  }

  if (query.isError) {
    const err = query.error;
    const isUnauthed = err instanceof ApiError && err.status === 401;
    return (
      <DesktopContainer>
        <View style={styles.center}>
          <Text style={styles.heading}>
            {isUnauthed ? "Not signed in" : "Couldn't load observations"}
          </Text>
          <Text style={styles.body}>
            {isUnauthed
              ? "Open Settings and sign in, then come back."
              : err.message}
          </Text>
          <Pressable style={[styles.button, styles.buttonGhost]} onPress={onRefresh}>
            <Text style={styles.buttonText}>Retry</Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  if (items.length === 0) {
    return (
      <DesktopContainer>
        <View style={styles.center}>
          <Text style={styles.heading}>No observations yet</Text>
          <Text style={styles.body}>
            Tap the Observe tab to capture your first photo.
          </Text>
        </View>
      </DesktopContainer>
    );
  }

  return (
    <DesktopContainer>
      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        numColumns={2}
        columnWrapperStyle={styles.gridRow}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={query.isRefetching} onRefresh={onRefresh} />
        }
        renderItem={({ item }) => <GalleryCard item={item} />}
        ListFooterComponent={
          query.hasNextPage ? (
            <Pressable
              style={[styles.button, styles.buttonGhost, styles.loadMore]}
              disabled={query.isFetchingNextPage}
              onPress={() => void query.fetchNextPage()}
            >
              <Text style={styles.buttonText}>
                {query.isFetchingNextPage ? "Loading…" : "Load more"}
              </Text>
            </Pressable>
          ) : null
        }
      />
    </DesktopContainer>
  );
}

function GalleryCard({ item }: { item: ObservationListItem }) {
  const mode = photoDisplayMode(item.photo_status);
  const ts = new Date(item.created_at);

  return (
    <Pressable
      style={styles.card}
      onPress={() => router.push(`/observation/${item.id}`)}
    >
      {mode === "image" ? (
        <GalleryThumb
          photoId={item.photo_id}
          checking={isAwaitingModeration(item.photo_status)}
        />
      ) : (
        <View style={styles.thumbPlaceholder}>
          <Text style={styles.placeholderGlyph}>
            {mode === "reviewing" ? "🔍" : "🚫"}
          </Text>
          <Text style={styles.placeholderText}>
            {mode === "reviewing"
              ? "An adult is checking this photo"
              : "Photo removed"}
          </Text>
        </View>
      )}
      <Text style={styles.species} numberOfLines={1}>
        {galleryCaption(item.species_name)}
      </Text>
      <Text style={styles.meta} numberOfLines={1}>
        {ts.toLocaleDateString()}
        {item.place_name ? ` · ${item.place_name}` : ""}
      </Text>
    </Pressable>
  );
}

function GalleryThumb({
  photoId,
  checking,
}: {
  photoId: string;
  checking: boolean;
}) {
  const urlQuery = usePhotoUrl(photoId, true);

  if (urlQuery.isPending) {
    return (
      <View style={styles.thumbPlaceholder}>
        <ActivityIndicator />
      </View>
    );
  }

  if (urlQuery.isError || !urlQuery.data) {
    // URL mint failed (offline, blob missing). Placeholder, not an error
    // banner -- pull-to-refresh retries the whole grid.
    return (
      <View style={styles.thumbPlaceholder}>
        <Text style={styles.placeholderGlyph}>🌿</Text>
      </View>
    );
  }

  return (
    <View style={styles.thumbWrap}>
      <Image
        source={{ uri: urlQuery.data.url }}
        style={styles.thumb}
        resizeMode="cover"
      />
      {checking && (
        <View style={styles.checkingBadge}>
          <Text style={styles.checkingBadgeText}>checking…</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  list: {
    padding: 12,
  },
  gridRow: {
    gap: 12,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: {
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 8,
  },
  body: {
    fontSize: 14,
    opacity: 0.7,
    textAlign: "center",
    marginBottom: 16,
  },
  card: {
    flex: 1,
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
  },
  placeholderGlyph: {
    fontSize: 28,
    marginBottom: 6,
  },
  placeholderText: {
    fontSize: 12,
    opacity: 0.7,
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
  species: {
    fontSize: 14,
    fontWeight: "500",
    marginTop: 6,
  },
  meta: {
    fontSize: 11,
    opacity: 0.6,
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
