import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import {
  type ExpeditionSummary,
  type ProgressItem,
  listAvailableExpeditions,
  listMyExpeditions,
  startExpedition,
} from "@/src/api/expeditions";
import { filterByEnvironment, splitProgress } from "@/src/expeditions/logic";

// "other" has no chip on purpose -- filterByEnvironment treats it as
// matching every environment, so those expeditions show under any chip.
const ENVIRONMENT_CHIPS: { label: string; value: string | null }[] = [
  { label: "All", value: null },
  { label: "Yard", value: "yard" },
  { label: "Park", value: "park" },
  { label: "Street", value: "street" },
  { label: "School", value: "school" },
];

export default function ExpeditionsScreen() {
  const queryClient = useQueryClient();
  const [env, setEnv] = useState<string | null>(null);

  const available = useQuery({
    queryKey: ["expeditions", "available"],
    queryFn: listAvailableExpeditions,
  });
  const mine = useQuery({
    queryKey: ["expeditions", "me"],
    queryFn: listMyExpeditions,
  });

  const start = useMutation({
    mutationFn: startExpedition,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? `${err.status}: ${err.message}` : String(err);
      Alert.alert("Couldn't start", message);
    },
  });

  if (available.isPending || mine.isPending) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  if (available.isError) {
    const err = available.error;
    const isUnauthed = err instanceof ApiError && err.status === 401;
    return (
      <View style={styles.center}>
        <Text style={styles.heading}>
          {isUnauthed ? "Not signed in" : "Couldn't load expeditions"}
        </Text>
        <Text style={styles.body}>
          {isUnauthed
            ? "Open Settings and sign in, then come back."
            : err.message}
        </Text>
        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => void available.refetch()}
        >
          <Text style={styles.buttonText}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const { inProgress, completed } = splitProgress(mine.data?.items ?? []);
  const items = filterByEnvironment(available.data?.items ?? [], env);

  return (
    <FlatList
      data={items}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <View style={styles.section}>
          {inProgress.length > 0 && <InProgressList items={inProgress} />}
          <Text style={styles.sectionLabel}>Where are you?</Text>
          <View style={styles.chipRow}>
            {ENVIRONMENT_CHIPS.map((chip) => (
              <Pressable
                key={chip.label}
                style={[
                  styles.chip,
                  chip.value === env ? styles.chipSelected : styles.chipGhost,
                ]}
                onPress={() => setEnv(chip.value)}
              >
                <Text
                  style={[
                    styles.chipText,
                    chip.value === env && styles.chipTextSelected,
                  ]}
                >
                  {chip.label}
                </Text>
              </Pressable>
            ))}
          </View>
          <Text style={styles.sectionLabel}>Available</Text>
        </View>
      }
      ListFooterComponent={
        completed.length === 0 ? null : <TrophyList items={completed} />
      }
      ListEmptyComponent={
        env !== null ? (
          <View style={styles.empty}>
            <Text style={styles.heading}>Nothing for this spot</Text>
            <Text style={styles.body}>
              No expeditions match this place right now — tap All to see
              every expedition.
            </Text>
          </View>
        ) : (
          <View style={styles.empty}>
            <Text style={styles.heading}>No expeditions available</Text>
            <Text style={styles.body}>
              Either you're working on all of them already, or none have been
              published yet.
            </Text>
          </View>
        )
      }
      refreshControl={
        <RefreshControl
          refreshing={available.isRefetching || mine.isRefetching}
          onRefresh={() => {
            void available.refetch();
            void mine.refetch();
          }}
        />
      }
      renderItem={({ item }) => (
        <ExpeditionCard
          item={item}
          onStart={() => start.mutate(item.id)}
          starting={start.isPending && start.variables === item.id}
        />
      )}
    />
  );
}

function InProgressList({ items }: { items: ProgressItem[] }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionLabel}>In progress</Text>
      {items.map((p) => (
        <Pressable
          key={p.expedition_id}
          style={styles.progressRow}
          onPress={() => router.push(`/expedition/${p.expedition_id}`)}
        >
          <View style={styles.progressBody}>
            <Text style={styles.progressTitle}>{p.title}</Text>
            <Text style={styles.progressMeta}>
              {p.completed_step_count} / {p.total_step_count} steps
            </Text>
          </View>
          <Text style={styles.progressChevron}>›</Text>
        </Pressable>
      ))}
      <View
        style={styles.divider}
        lightColor="#eee"
        darkColor="rgba(255,255,255,0.1)"
      />
    </View>
  );
}

function TrophyList({ items }: { items: ProgressItem[] }) {
  return (
    <View style={styles.section}>
      <View
        style={styles.divider}
        lightColor="#eee"
        darkColor="rgba(255,255,255,0.1)"
      />
      <Text style={styles.sectionLabel}>Trophies</Text>
      {items.map((p) => (
        <Pressable
          key={p.expedition_id}
          style={styles.progressRow}
          onPress={() => router.push(`/expedition/${p.expedition_id}`)}
        >
          <Text style={styles.trophyGlyph}>🏆</Text>
          <View style={styles.progressBody}>
            <Text style={styles.progressTitle}>{p.title}</Text>
            <Text style={styles.progressMeta}>
              {p.completed_at
                ? `Completed ${new Date(p.completed_at).toLocaleDateString()}`
                : "Completed"}
            </Text>
          </View>
          <Text style={styles.progressChevron}>›</Text>
        </Pressable>
      ))}
    </View>
  );
}

function ExpeditionCard({
  item,
  onStart,
  starting,
}: {
  item: ExpeditionSummary;
  onStart: () => void;
  starting: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Pressable style={styles.card} onPress={() => setExpanded((x) => !x)}>
      <Text style={styles.cardTitle}>{item.title}</Text>
      {item.subtitle && <Text style={styles.cardSubtitle}>{item.subtitle}</Text>}
      <Text style={styles.cardMeta}>
        {item.duration_minutes} min · {item.environments.join(", ")}
      </Text>
      {expanded && <Text style={styles.cardIntro}>{item.intro}</Text>}
      <Pressable
        style={[styles.button, styles.buttonPrimary, starting && styles.buttonDisabled]}
        disabled={starting}
        onPress={onStart}
      >
        <Text style={styles.buttonText}>{starting ? "Starting…" : "Start expedition"}</Text>
      </Pressable>
    </Pressable>
  );
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
  section: { marginBottom: 8 },
  sectionLabel: {
    fontSize: 13,
    fontWeight: "600",
    opacity: 0.7,
    marginTop: 8,
    marginBottom: 8,
  },
  progressRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
    marginBottom: 8,
  },
  // Transparent so the themed View doesn't paint over the row card.
  progressBody: { flex: 1, backgroundColor: "transparent" },
  progressTitle: { fontSize: 15, fontWeight: "500" },
  progressMeta: { fontSize: 12, opacity: 0.7, marginTop: 2 },
  progressChevron: { fontSize: 22, opacity: 0.4, marginLeft: 8 },
  trophyGlyph: { fontSize: 14, opacity: 0.8, marginRight: 10 },
  divider: { height: 1, marginVertical: 16 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    alignItems: "center",
  },
  chipSelected: { backgroundColor: "#2f6feb" },
  chipGhost: { borderColor: "#888", borderWidth: StyleSheet.hairlineWidth },
  // No color on the base style -- Themed Text supplies a scheme-aware
  // color, so unselected ghost chips stay readable in light mode. The
  // selected chip's blue fill needs white for contrast in both schemes.
  chipText: { fontSize: 13 },
  chipTextSelected: { color: "#fff" },
  card: {
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
    marginBottom: 12,
  },
  cardTitle: { fontSize: 16, fontWeight: "600" },
  cardSubtitle: { fontSize: 13, opacity: 0.8, marginTop: 2 },
  cardMeta: { fontSize: 12, opacity: 0.6, marginTop: 4 },
  cardIntro: { fontSize: 13, marginTop: 8, lineHeight: 18 },
  button: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 6,
    alignItems: "center",
    marginTop: 12,
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: { borderColor: "#888", borderWidth: StyleSheet.hairlineWidth },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff" },
});
