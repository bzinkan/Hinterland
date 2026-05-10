import { useCallback } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import type { ObservationListItem } from "@/src/api/observations";
import { useMyObservations } from "@/src/observation/useMyObservations";

export default function HomeScreen() {
  const query = useMyObservations();

  const items = query.data?.pages.flatMap((p) => p.items) ?? [];

  const onRefresh = useCallback(() => {
    void query.refetch();
  }, [query]);

  if (query.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  if (query.isError) {
    const err = query.error;
    const isUnauthed = err instanceof ApiError && err.status === 401;
    return (
      <View style={styles.center}>
        <Text style={styles.heading}>
          {isUnauthed ? "Not signed in" : "Couldn't load observations"}
        </Text>
        <Text style={styles.body}>
          {isUnauthed
            ? "Open Settings and paste a Firebase ID token, then come back."
            : err.message}
        </Text>
        <Pressable style={[styles.button, styles.buttonGhost]} onPress={onRefresh}>
          <Text style={styles.buttonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  if (items.length === 0) {
    return (
      <View style={styles.center}>
        <Text style={styles.heading}>No observations yet</Text>
        <Text style={styles.body}>
          Tap the Observe tab to capture your first photo.
        </Text>
      </View>
    );
  }

  return (
    <FlatList
      data={items}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={query.isRefetching} onRefresh={onRefresh} />
      }
      renderItem={({ item }) => <ObservationRow item={item} />}
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
  );
}

function ObservationRow({ item }: { item: ObservationListItem }) {
  const ts = new Date(item.created_at);
  return (
    <View style={styles.row}>
      <Text style={styles.species}>{item.species_name ?? "Unknown species"}</Text>
      <Text style={styles.meta}>
        {ts.toLocaleString()} · {item.photo_status}
      </Text>
      <Text style={styles.meta}>
        {item.latitude.toFixed(4)}, {item.longitude.toFixed(4)}
        {item.geohash4 ? ` · ${item.geohash4}` : ""}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  list: {
    padding: 16,
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
  row: {
    paddingVertical: 12,
    borderBottomColor: "rgba(255,255,255,0.1)",
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  species: {
    fontSize: 16,
    fontWeight: "500",
  },
  meta: {
    fontSize: 12,
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
    marginTop: 16,
    marginHorizontal: 16,
  },
});
