import { router, Stack, useLocalSearchParams } from "expo-router";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import type {
  ObservationListItem,
  ObservationListResponse,
} from "@/src/api/observations";
import { queryClient } from "@/src/api/queryClient";
import {
  galleryCaption,
  isAwaitingModeration,
  photoDisplayMode,
} from "@/src/observation/galleryLogic";
import { usePhotoUrl } from "@/src/observation/usePhotoUrl";

/**
 * Full-size view of one observation, opened from the Home gallery.
 *
 * Data comes straight out of the ["observations","me"] infinite-query
 * cache -- the gallery just rendered this item, so a second fetch would
 * be pure latency. Deep links to ids that aren't cached get a gentle
 * bounce back to Home instead of a spinner that can't resolve.
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

export default function ObservationDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const item = typeof id === "string" ? findCachedObservation(id) : null;

  if (!item) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Observation" }} />
        <Text style={styles.body}>
          Couldn&apos;t find that observation. Open it from your Home list.
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

  const mode = photoDisplayMode(item.photo_status);
  const ts = new Date(item.created_at);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Stack.Screen options={{ title: galleryCaption(item.species_name) }} />

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

      <Text style={styles.species}>{galleryCaption(item.species_name)}</Text>

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

  if (urlQuery.isPending) {
    return (
      <View style={styles.photoPlaceholder}>
        <ActivityIndicator />
      </View>
    );
  }

  if (urlQuery.isError || !urlQuery.data) {
    return (
      <View style={styles.photoPlaceholder}>
        <Text style={styles.placeholderGlyph}>🌿</Text>
        <Text style={styles.placeholderText}>
          Couldn&apos;t load the photo. Check your connection and try again.
        </Text>
      </View>
    );
  }

  return (
    <View>
      <Image
        source={{ uri: urlQuery.data.url }}
        style={styles.photo}
        resizeMode="cover"
      />
      {checking && (
        <Text style={styles.checkingNote}>
          Still being checked -- only you can see it for now.
        </Text>
      )}
    </View>
  );
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
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
  backButton: {
    marginTop: 24,
  },
});
