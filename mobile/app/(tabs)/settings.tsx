import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import { signOut } from "firebase/auth";
import { useEffect, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, TextInput } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { getMe } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import { getFirebaseAuth } from "@/src/auth/firebase";
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

  const me = useQuery({
    queryKey: ["me"],
    queryFn: getMe,
    retry: (count, err) => {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        return false;
      }
      return count < 2;
    },
  });

  useEffect(() => {
    void getBearerToken().then((token) => setTokenSaved(token != null));
  }, [me.dataUpdatedAt]);

  async function handleSignOut() {
    setBusy(true);
    try {
      await signOut(getFirebaseAuth());
      // onIdTokenChanged listener clears the bearer token; mirror locally.
      await clearBearerToken();
      setTokenSaved(false);
      void me.refetch();
    } catch (err: unknown) {
      Alert.alert("Sign out failed", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveDevToken() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await setBearerToken(trimmed);
      setTokenSaved(true);
      setDraft("");
      void me.refetch();
      Alert.alert("Token saved", "Future API calls will include it.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClearDevToken() {
    setBusy(true);
    try {
      await clearBearerToken();
      setTokenSaved(false);
      void me.refetch();
    } finally {
      setBusy(false);
    }
  }

  const signedIn = me.data != null;

  return (
    <DesktopContainer>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Settings</Text>
        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        <Text style={styles.label}>Account</Text>
        {me.isPending ? (
          <Text style={styles.value}>checking…</Text>
        ) : signedIn ? (
          <>
            <Text style={styles.value}>signed in as {me.data.display_name}</Text>
            <Text style={styles.help}>role: {me.data.role}</Text>
            <Pressable
              style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
              onPress={handleSignOut}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Sign out</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.value}>not signed in</Text>
            <Pressable
              style={[styles.button, styles.buttonPrimary]}
              onPress={() => router.push("/sign-in")}
            >
              <Text style={styles.buttonText}>Sign in or create account</Text>
            </Pressable>
          </>
        )}

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

        <Text style={styles.label}>Auth (dev shortcut)</Text>
        <Text style={styles.help}>
          Paste a Firebase ID token to skip the sign-in flow. Useful for
          smoke-testing with seeded users -- regular users should use the
          sign-in screen above.
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
            onPress={handleSaveDevToken}
            disabled={busy || draft.trim().length === 0}
          >
            <Text style={styles.buttonText}>Save token</Text>
          </Pressable>
          <Pressable
            style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
            onPress={handleClearDevToken}
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
    marginTop: 8,
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
