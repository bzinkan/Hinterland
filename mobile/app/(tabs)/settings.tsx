import { router } from "expo-router";
import { useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, TextInput } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import {
  clearBearerToken,
  getBearerToken,
  setBearerToken,
} from "@/src/auth/token";
import { env } from "@/src/config/env";

export default function SettingsScreen() {
  const [tokenSaved, setTokenSaved] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void getBearerToken().then((token) => setTokenSaved(token != null));
  }, []);

  async function handleSave() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await setBearerToken(trimmed);
      setTokenSaved(true);
      setDraft("");
      Alert.alert("Token saved", "Future API calls will include it.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    setBusy(true);
    try {
      await clearBearerToken();
      setTokenSaved(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <DesktopContainer>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Settings</Text>
      <View
        style={styles.separator}
        lightColor="#eee"
        darkColor="rgba(255,255,255,0.1)"
      />

      <Text style={styles.label}>Build</Text>
      <Text style={styles.value}>env: {env.appEnv}</Text>
      <Text style={styles.value}>API: {env.apiBaseUrl}</Text>
      <Text style={styles.value}>updates channel: {env.updatesChannel}</Text>

      <View
        style={styles.separator}
        lightColor="#eee"
        darkColor="rgba(255,255,255,0.1)"
      />

      <Text style={styles.label}>Adult tools</Text>
      <Pressable
        style={[styles.button, styles.buttonGhost]}
        onPress={() => router.push("/review-queue")}
      >
        <Text style={styles.buttonText}>Open review queue</Text>
      </Pressable>
      <Text style={styles.help}>
        Parents and teachers only. Kid accounts get a "not available"
        message.
      </Text>

      <View
        style={styles.separator}
        lightColor="#eee"
        darkColor="rgba(255,255,255,0.1)"
      />

      <Text style={styles.label}>Auth (dev)</Text>
      <Text style={styles.help}>
        Paste a Firebase ID token to authenticate API calls. Phase 6 only;
        replaced by real sign-in flow later.
      </Text>
      <Text style={styles.value}>
        status:{" "}
        {tokenSaved === null
          ? "loading…"
          : tokenSaved
            ? "● token present"
            : "○ no token"}
      </Text>
      <TextInput
        style={styles.input}
        value={draft}
        onChangeText={setDraft}
        placeholder="paste ID token here"
        placeholderTextColor="#999"
        autoCapitalize="none"
        autoCorrect={false}
        multiline
      />
      <View style={styles.row}>
        <Pressable
          style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
          onPress={handleSave}
          disabled={busy || draft.trim().length === 0}
        >
          <Text style={styles.buttonText}>Save token</Text>
        </Pressable>
        <Pressable
          style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
          onPress={handleClear}
          disabled={busy || tokenSaved !== true}
        >
          <Text style={styles.buttonText}>Clear</Text>
        </Pressable>
        </View>
      </ScrollView>
    </DesktopContainer>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "flex-start",
    justifyContent: "flex-start",
    padding: 24,
  },
  title: {
    fontSize: 22,
    fontWeight: "600",
  },
  separator: {
    marginVertical: 16,
    height: 1,
    width: "100%",
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
  help: {
    fontSize: 12,
    opacity: 0.6,
    marginTop: 4,
    marginBottom: 8,
  },
  input: {
    width: "100%",
    minHeight: 80,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    padding: 8,
    marginTop: 12,
    fontFamily: "SpaceMono",
    fontSize: 12,
    color: "#fff",
  },
  row: {
    flexDirection: "row",
    gap: 8,
    marginTop: 12,
  },
  button: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 6,
  },
  buttonPrimary: {
    backgroundColor: "#2f6feb",
  },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
});
