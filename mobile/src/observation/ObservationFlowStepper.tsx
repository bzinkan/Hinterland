import { StyleSheet } from "react-native";

import { Text, View } from "@/components/Themed";
import {
  OBSERVATION_FLOW_STEPS,
  type ObservationFlowStep,
  flowStepState,
} from "@/src/observation/presentation";

type Props = {
  current: ObservationFlowStep;
};

export function ObservationFlowStepper({ current }: Props) {
  return (
    <View style={styles.container}>
      {OBSERVATION_FLOW_STEPS.map((step) => {
        const state = flowStepState(current, step.key);
        return (
          <View key={step.key} style={styles.step}>
            <View
              style={[
                styles.dot,
                state === "complete" && styles.dotComplete,
                state === "active" && styles.dotActive,
              ]}
            />
            <Text
              style={[
                styles.label,
                state === "active" && styles.labelActive,
                state === "upcoming" && styles.labelUpcoming,
              ]}
              numberOfLines={1}
            >
              {step.label}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
    paddingVertical: 6,
    backgroundColor: "transparent",
  },
  step: {
    flex: 1,
    alignItems: "center",
    backgroundColor: "transparent",
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.32)",
    backgroundColor: "transparent",
  },
  dotComplete: {
    backgroundColor: "#66a182",
    borderColor: "#66a182",
  },
  dotActive: {
    backgroundColor: "#f0b44c",
    borderColor: "#f0b44c",
  },
  label: {
    marginTop: 5,
    fontSize: 11,
    fontWeight: "700",
    color: "#fff",
  },
  labelActive: {
    color: "#f0b44c",
  },
  labelUpcoming: {
    opacity: 0.46,
  },
});
