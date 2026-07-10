import FontAwesome from "@expo/vector-icons/FontAwesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, Stack, useLocalSearchParams } from "expo-router";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import {
  type StepProgress,
  focusExpedition,
  listMyExpeditions,
  restartExpedition,
} from "@/src/api/expeditions";
import { nextObjective, progressLabel } from "@/src/expeditions/logic";
import { useAuthSession } from "@/src/auth/session";
import { ImperativeRequestSupersededError } from "@/src/auth/requestBoundary";

export default function ExpeditionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const queryClient = useQueryClient();
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );

  const mine = useQuery({
    queryKey: ["expeditions", ownerUserId ?? "anonymous", "me"],
    queryFn: ({ signal }) => listMyExpeditions(signal),
    enabled: ownerUserId != null,
  });

  const focus = useMutation({
    mutationFn: focusExpedition,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    },
    onError: (err) => {
      if (err instanceof ImperativeRequestSupersededError) return;
      const message =
        err instanceof ApiError ? `${err.status}: ${err.message}` : String(err);
      Alert.alert("Couldn't focus quest", message);
    },
  });

  const restart = useMutation({
    mutationFn: restartExpedition,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["expeditions"] });
    },
    onError: (err) => {
      if (err instanceof ImperativeRequestSupersededError) return;
      const message =
        err instanceof ApiError ? `${err.status}: ${err.message}` : String(err);
      Alert.alert("Couldn't start over", message);
    },
  });

  if (mine.isPending) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Expedition" }} />
        <ActivityIndicator />
      </View>
    );
  }

  if (mine.isError) {
    const err = mine.error;
    const isUnauthed = err instanceof ApiError && err.status === 401;
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Expedition" }} />
        <FontAwesome name="map-signs" size={28} color="#5fbf8f" />
        <Text style={styles.heading}>
          {isUnauthed ? "Quest unavailable" : "Couldn't load expedition"}
        </Text>
        <Text style={styles.body}>
          {isUnauthed
            ? "This dev build needs a session before the mission briefing can load."
            : err instanceof Error
              ? err.message
              : String(err)}
        </Text>
        <Pressable
          style={[styles.button, styles.buttonGhostLight]}
          onPress={() => void mine.refetch()}
        >
          <Text style={styles.buttonTextDark}>Retry</Text>
        </Pressable>
      </View>
    );
  }

  const item = (mine.data?.items ?? []).find((p) => p.expedition_id === id);

  if (!item) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: "Expedition" }} />
        <Text style={styles.heading}>Expedition not found</Text>
        <Text style={styles.body}>
          Head back to the quest board and pick another mission.
        </Text>
        <Pressable
          style={[styles.button, styles.buttonGhostLight]}
          onPress={() => router.back()}
        >
          <Text style={styles.buttonTextDark}>Back</Text>
        </Pressable>
      </View>
    );
  }

  const isComplete = item.completed_at != null;
  const isActive = mine.data?.active_expedition_id === item.expedition_id;
  const upNext = nextObjective(item);
  const steps = item.steps ?? [];

  async function openCamera() {
    if (!item || isComplete) return;
    if (!isActive) {
      try {
        await focus.mutateAsync(item.expedition_id);
      } catch {
        return;
      }
    }
    router.push("/observe");
  }

  return (
    <ScrollView
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={mine.isRefetching}
          onRefresh={() => void mine.refetch()}
        />
      }
    >
      <Stack.Screen options={{ title: "Expedition" }} />
      <View style={styles.hero}>
        <View style={styles.heroTop}>
          <FontAwesome
            name={isComplete ? "trophy" : isActive ? "flag" : "map-signs"}
            size={18}
            color={isComplete ? "#f6c453" : "#8bdcb6"}
          />
          <Text style={styles.heroKicker}>
            {isComplete ? "Trophy" : isActive ? "Active quest" : "Mission briefing"}
          </Text>
        </View>
        <Text style={styles.title}>{item.title}</Text>
        {item.subtitle && <Text style={styles.subtitle}>{item.subtitle}</Text>}
        <View style={styles.metaRow}>
          {item.difficulty_label ? (
            <Text style={styles.metaPill}>{item.difficulty_label}</Text>
          ) : null}
          <Text style={styles.metaPill}>{themeLabel(item.theme)}</Text>
        </View>
        {item.learning_goal ? (
          <Text style={styles.learningGoal}>{item.learning_goal}</Text>
        ) : null}
        <Text style={styles.intro}>{item.intro}</Text>
      </View>

      {isComplete ? (
        <View style={styles.completePanel}>
          <Text style={styles.completeTitle}>Expedition complete</Text>
          <Text style={styles.completeOutro}>{item.outro}</Text>
        </View>
      ) : (
        <View style={styles.objectivePanel}>
          <Text style={styles.objectiveKicker}>Current objective</Text>
          <Text style={styles.objectiveText}>
            {upNext?.description ?? "All steps complete"}
          </Text>
          {upNext?.hint ? <Text style={styles.hint}>{upNext.hint}</Text> : null}
          <Pressable
            style={[
              styles.button,
              styles.buttonPrimary,
              focus.isPending && styles.buttonDisabled,
            ]}
            disabled={focus.isPending}
            onPress={() => void openCamera()}
          >
            <Text style={styles.buttonText}>
              {focus.isPending ? "Focusing..." : "Open camera"}
            </Text>
          </Pressable>
        </View>
      )}

      <Text style={styles.progressLine}>{progressLabel(item)}</Text>
      <View style={styles.path}>
        {steps.map((step, index) => (
          <StepNode
            key={step.id}
            number={index + 1}
            step={step}
            state={
              step.completed_at != null
                ? "done"
                : step.id === upNext?.id
                  ? "next"
                  : "later"
            }
          />
        ))}
      </View>

      {!isComplete && (
          <Pressable
            style={[
              styles.button,
              styles.buttonGhostLight,
              styles.restartButton,
              restart.isPending && styles.buttonDisabled,
            ]}
          disabled={restart.isPending}
          onPress={() =>
            Alert.alert(
              "Start over?",
              "Your step progress on this expedition will reset.",
              [
                { text: "Cancel", style: "cancel" },
                {
                  text: "Start over",
                  style: "destructive",
                  onPress: () => restart.mutate(item.expedition_id),
                },
              ],
            )
          }
        >
          <Text style={styles.buttonTextDark}>
            {restart.isPending ? "Starting over..." : "Start over"}
          </Text>
        </Pressable>
      )}
    </ScrollView>
  );
}

function StepNode({
  step,
  number,
  state,
}: {
  step: StepProgress;
  number: number;
  state: "done" | "next" | "later";
}) {
  return (
    <View style={[styles.stepRow, state === "next" && styles.stepRowNext]}>
      <View
        style={[
          styles.stepMark,
          state === "done"
            ? styles.stepMarkDone
            : state === "next"
              ? styles.stepMarkNext
              : styles.stepMarkLater,
        ]}
      >
        <Text style={styles.stepMarkText}>{state === "done" ? "✓" : number}</Text>
      </View>
      <View style={styles.stepBody}>
        {state === "next" && <Text style={styles.upNextLabel}>Up next</Text>}
        <Text style={[styles.stepText, state === "done" && styles.stepTextDone]}>
          {step.description}
        </Text>
        {state === "next" && step.hint ? (
          <Text style={styles.stepHint}>{step.hint}</Text>
        ) : null}
        {state === "next" && step.tag_prompt ? (
          <Text style={styles.stepPrompt}>
            Tag prompt: {step.tag_prompt.question}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

function themeLabel(theme: string): string {
  switch (theme) {
    case "food_web":
      return "Food web";
    case "pollinators":
      return "Pollinators";
    case "decomposers":
      return "Decomposers";
    case "trees":
      return "Trees";
    case "wetland":
      return "Wetland";
    case "invasive":
      return "Invasive";
    case "urban":
      return "Urban";
    case "seasonal":
      return "Seasonal";
    default:
      return "Warm-up";
  }
}

const styles = StyleSheet.create({
  content: { padding: 16, paddingBottom: 28 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: { fontSize: 18, fontWeight: "800", marginTop: 8, marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.75, textAlign: "center", marginBottom: 16 },
  hero: {
    backgroundColor: "#161a22",
    borderRadius: 8,
    padding: 16,
    marginBottom: 12,
  },
  heroTop: {
    backgroundColor: "transparent",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 8,
  },
  heroKicker: {
    color: "#8bdcb6",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  title: { color: "#fff", fontSize: 24, fontWeight: "800" },
  subtitle: { color: "#dbeafe", fontSize: 14, marginTop: 2 },
  metaRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    backgroundColor: "transparent",
    marginTop: 10,
  },
  metaPill: {
    color: "#dcead8",
    borderColor: "#8bdcb6",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 4,
    fontSize: 11,
    fontWeight: "800",
  },
  learningGoal: {
    color: "#f7e7b5",
    fontSize: 13,
    lineHeight: 18,
    marginTop: 10,
  },
  intro: { color: "#e5e7eb", fontSize: 14, lineHeight: 20, marginTop: 12 },
  objectivePanel: {
    padding: 16,
    borderRadius: 8,
    backgroundColor: "#8bdcb6",
    marginBottom: 14,
  },
  objectiveKicker: {
    color: "#0f172a",
    fontSize: 12,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  objectiveText: {
    color: "#0f172a",
    fontSize: 18,
    lineHeight: 24,
    fontWeight: "800",
    marginTop: 6,
  },
  hint: { color: "#0f172a", fontSize: 13, lineHeight: 18, marginTop: 6 },
  completePanel: {
    padding: 16,
    borderRadius: 8,
    backgroundColor: "#f6c453",
    marginBottom: 14,
  },
  completeTitle: { color: "#241a05", fontSize: 18, fontWeight: "800" },
  completeOutro: { color: "#241a05", fontSize: 14, lineHeight: 20, marginTop: 6 },
  progressLine: {
    fontSize: 13,
    fontWeight: "800",
    opacity: 0.75,
    marginTop: 4,
    marginBottom: 8,
  },
  path: { gap: 8 },
  stepRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 8,
    backgroundColor: "#1a1f2b",
  },
  stepRowNext: {
    borderColor: "#8bdcb6",
    borderWidth: 1,
  },
  stepMark: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 12,
  },
  stepMarkDone: { backgroundColor: "#22c55e" },
  stepMarkNext: { backgroundColor: "#2f6feb" },
  stepMarkLater: { backgroundColor: "#334155" },
  stepMarkText: { color: "#fff", fontSize: 13, fontWeight: "800" },
  stepBody: { flex: 1, backgroundColor: "transparent" },
  upNextLabel: {
    color: "#8bdcb6",
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase",
    marginBottom: 3,
  },
  stepText: { color: "#fff", fontSize: 15, lineHeight: 20 },
  stepTextDone: { opacity: 0.58 },
  stepHint: { color: "#cbd5e1", fontSize: 13, lineHeight: 18, marginTop: 4 },
  stepPrompt: { color: "#f7e7b5", fontSize: 12, lineHeight: 17, marginTop: 5 },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 7,
    alignItems: "center",
  },
  buttonPrimary: { backgroundColor: "#0f172a", marginTop: 14 },
  buttonGhost: { borderColor: "#888", borderWidth: StyleSheet.hairlineWidth },
  buttonGhostLight: {
    backgroundColor: "#fff",
    borderColor: "#64748b",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonDisabled: { opacity: 0.45 },
  buttonText: { fontSize: 14, color: "#fff", fontWeight: "700" },
  buttonTextDark: { fontSize: 14, color: "#0f172a", fontWeight: "700" },
  restartButton: { marginTop: 12 },
});
