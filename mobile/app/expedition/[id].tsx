import { router, Stack, useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
} from "react-native";

import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import { type StepProgress, listMyExpeditions } from "@/src/api/expeditions";
import { nextIncompleteStep } from "@/src/expeditions/logic";

export default function ExpeditionDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();

  // Same query key as the Expeditions tab, so navigating here from the
  // tab is a cache hit -- no second fetch for data we just rendered.
  const mine = useQuery({
    queryKey: ["expeditions", "me"],
    queryFn: listMyExpeditions,
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
        <Text style={styles.heading}>
          {isUnauthed ? "Not signed in" : "Couldn't load expedition"}
        </Text>
        <Text style={styles.body}>
          {isUnauthed
            ? "Open Settings and sign in, then come back."
            : err.message}
        </Text>
        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => void mine.refetch()}
        >
          <Text style={styles.buttonText}>Retry</Text>
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
          We couldn't find that expedition. Head back and pick another one.
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

  const isComplete = item.completed_at != null;
  // Guard against a backend that predates the step-detail field --
  // an older /me payload simply renders the header without steps.
  const steps = item.steps ?? [];
  // No "Up next" highlight once the expedition is complete -- the banner
  // + outro take over that slot.
  const upNext = isComplete ? null : nextIncompleteStep(steps);

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
      <Text style={styles.title}>{item.title}</Text>
      {item.subtitle && <Text style={styles.subtitle}>{item.subtitle}</Text>}
      <Text style={styles.intro}>{item.intro}</Text>

      {isComplete && (
        <View style={styles.completeBanner}>
          <Text style={styles.completeTitle}>Expedition complete!</Text>
          <Text style={styles.completeOutro}>{item.outro}</Text>
        </View>
      )}

      <Text style={styles.progressLine}>
        {item.completed_step_count} / {item.total_step_count} steps
      </Text>

      {steps.map((step) => (
        <StepRow
          key={step.id}
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
    </ScrollView>
  );
}

function StepRow({
  step,
  state,
}: {
  step: StepProgress;
  state: "done" | "next" | "later";
}) {
  return (
    <View style={[styles.stepRow, state === "next" && styles.stepRowNext]}>
      <Text
        style={[
          styles.stepMark,
          state === "done" ? styles.stepMarkDone : styles.stepMarkOpen,
        ]}
      >
        {state === "done" ? "✓" : "○"}
      </Text>
      <View style={styles.stepBody}>
        {state === "next" && <Text style={styles.upNextLabel}>Up next</Text>}
        <Text
          style={[styles.stepText, state === "done" && styles.stepTextDone]}
        >
          {step.description}
        </Text>
        {/* Hint only on the active step -- later steps stay unspoiled. */}
        {state === "next" && step.hint ? (
          <Text style={styles.stepHint}>{step.hint}</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  heading: { fontSize: 18, fontWeight: "600", marginBottom: 8 },
  body: { fontSize: 14, opacity: 0.7, textAlign: "center", marginBottom: 16 },
  title: { fontSize: 20, fontWeight: "600" },
  subtitle: { fontSize: 14, opacity: 0.8, marginTop: 2 },
  intro: { fontSize: 14, lineHeight: 20, marginTop: 12 },
  completeBanner: {
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginTop: 16,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
    borderColor: "#2f6feb",
    borderWidth: 1,
  },
  completeTitle: { fontSize: 16, fontWeight: "600", color: "#2f6feb" },
  completeOutro: { fontSize: 14, lineHeight: 20, marginTop: 6 },
  progressLine: {
    fontSize: 13,
    fontWeight: "600",
    opacity: 0.7,
    marginTop: 16,
    marginBottom: 8,
  },
  stepRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 8,
    borderRadius: 6,
    backgroundColor: "#1a1a1a",
  },
  stepRowNext: {
    borderColor: "#2f6feb",
    borderWidth: 1,
  },
  stepMark: { fontSize: 16, width: 24 },
  stepMarkDone: { color: "#22c55e" },
  stepMarkOpen: { opacity: 0.5 },
  // Transparent so the themed View doesn't paint over the row card.
  stepBody: { flex: 1, backgroundColor: "transparent" },
  upNextLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: "#2f6feb",
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 2,
  },
  stepText: { fontSize: 15 },
  stepTextDone: { opacity: 0.5 },
  stepHint: { fontSize: 13, opacity: 0.7, marginTop: 4 },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: "center",
  },
  buttonGhost: { borderColor: "#888", borderWidth: StyleSheet.hairlineWidth },
  buttonText: { fontSize: 14, color: "#fff" },
});
