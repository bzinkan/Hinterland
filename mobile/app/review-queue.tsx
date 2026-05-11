import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, Stack } from "expo-router";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import {
  approveReview,
  getPhotoUrl,
  listReviewQueue,
  rejectReview,
  type ReviewQueueItem,
} from "@/src/api/reviewQueue";

export default function ReviewQueueScreen() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["review-queue", "pending"],
    queryFn: listReviewQueue,
  });

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
  }

  const approve = useMutation({
    mutationFn: approveReview,
    onSuccess: () => invalidate(),
    onError: (err) => Alert.alert("Approve failed", apiErrorMessage(err)),
  });
  const reject = useMutation({
    mutationFn: rejectReview,
    onSuccess: () => invalidate(),
    onError: (err) => Alert.alert("Reject failed", apiErrorMessage(err)),
  });

  if (query.isPending) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Review Queue" }} />
        <ActivityIndicator />
      </View>
    );
  }

  if (query.isError) {
    const err = query.error;
    const isUnauthed = err instanceof ApiError && (err.status === 401 || err.status === 403);
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Review Queue" }} />
        <Text style={styles.heading}>
          {isUnauthed ? "Adults only" : "Couldn't load queue"}
        </Text>
        <Text style={styles.body}>
          {isUnauthed
            ? "The review queue is for parent and teacher accounts."
            : err.message}
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

  const items = query.data?.items ?? [];

  return (
    <FlatList
      data={items}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <Stack.Screen options={{ title: `Review Queue (${items.length})` }} />
      }
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.heading}>Nothing pending</Text>
          <Text style={styles.body}>
            All quarantined photos in your groups have been resolved.
          </Text>
        </View>
      }
      refreshControl={
        <RefreshControl
          refreshing={query.isRefetching}
          onRefresh={() => void query.refetch()}
        />
      }
      renderItem={({ item }) => (
        <ReviewCard
          item={item}
          onApprove={() => approve.mutate(item.id)}
          onReject={() => reject.mutate(item.id)}
          busy={
            (approve.isPending && approve.variables === item.id) ||
            (reject.isPending && reject.variables === item.id)
          }
        />
      )}
    />
  );
}

function ReviewCard({
  item,
  onApprove,
  onReject,
  busy,
}: {
  item: ReviewQueueItem;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  const url = useQuery({
    queryKey: ["photo-url", item.photo_id],
    queryFn: () => getPhotoUrl(item.photo_id),
    // Signed URLs expire in 5 min; refetch a bit before that.
    staleTime: 4 * 60 * 1000,
  });

  return (
    <View style={styles.card}>
      {url.data ? (
        <Image
          source={{ uri: url.data.url }}
          style={styles.thumb}
          resizeMode="cover"
        />
      ) : (
        <View style={[styles.thumb, styles.thumbPlaceholder]}>
          {url.isPending ? (
            <ActivityIndicator />
          ) : (
            <Text style={styles.thumbError}>image unavailable</Text>
          )}
        </View>
      )}
      <Text style={styles.cardMeta}>
        {new Date(item.created_at).toLocaleString()}
      </Text>
      {item.reason && (
        <Text style={styles.cardReason} numberOfLines={2}>
          flagged: {item.reason}
        </Text>
      )}
      <View style={styles.actions}>
        <Pressable
          style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
          disabled={busy}
          onPress={onReject}
        >
          <Text style={styles.buttonText}>Reject</Text>
        </Pressable>
        <Pressable
          style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
          disabled={busy}
          onPress={onApprove}
        >
          <Text style={styles.buttonText}>Approve</Text>
        </Pressable>
      </View>
    </View>
  );
}

function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

const styles = StyleSheet.create({
  list: { padding: 16 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  empty: { alignItems: "center", padding: 24 },
  heading: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.7, textAlign: "center", marginBottom: 16 },
  card: {
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
    marginBottom: 12,
  },
  thumb: {
    width: "100%",
    height: 220,
    borderRadius: 6,
    marginBottom: 8,
    backgroundColor: "#000",
  },
  thumbPlaceholder: { alignItems: "center", justifyContent: "center" },
  thumbError: { fontSize: 12, opacity: 0.6 },
  cardMeta: { fontSize: 12, opacity: 0.6 },
  cardReason: { fontSize: 12, opacity: 0.7, marginTop: 4 },
  actions: { flexDirection: "row", gap: 12, marginTop: 12 },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    flex: 1,
    alignItems: "center",
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: { borderColor: "#888", borderWidth: StyleSheet.hairlineWidth },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff" },
});
