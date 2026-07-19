import { useQuery } from "@tanstack/react-query";
import { router } from "expo-router";
import * as Updates from "expo-updates";
import { useEffect, useState } from "react";
import {
  Alert,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
} from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { getMe, requestAccountDeletion } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import {
  clearBearerToken,
  getBearerToken,
  setBearerToken,
} from "@/src/auth/token";
import { env } from "@/src/config/env";
import { useAuthSession } from "@/src/auth/session";
import {
  ImperativeRequestSupersededError,
  runImperativeRequest,
} from "@/src/auth/requestBoundary";
import {
  listQueuedObservations,
  purgeOwnerObservationQueue,
} from "@/src/observation/observationQueue";

export default function SettingsScreen() {
  const session = useAuthSession();
  const ownerUserId =
    session.status === "authenticated" ? session.user.id : null;
  const [tokenSaved, setTokenSaved] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const devTokenShortcutEnabled =
    env.appEnv === "development" || env.appEnv === "preview";

  const me = useQuery({
    queryKey: ["me", ownerUserId ?? "anonymous"],
    queryFn: ({ signal }) => getMe(signal),
    enabled: ownerUserId != null,
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
      await clearBearerToken();
      setTokenSaved(false);
      void me.refetch();
    } catch (err: unknown) {
      Alert.alert("Sign out failed", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function requestSignOut(): Promise<void> {
    if (Platform.OS === "web" || !ownerUserId) {
      await handleSignOut();
      return;
    }
    const pending = (await listQueuedObservations(ownerUserId)).filter(
      (item) => item.stage !== "complete",
    );
    if (pending.length === 0) {
      await handleSignOut();
      return;
    }
    Alert.alert(
      "Observations are waiting",
      `${pending.length} saved ${pending.length === 1 ? "observation" : "observations"} will stay on this device, locked to this account.`,
      [
        { text: "Stay signed in", style: "cancel" },
        { text: "Sign out", onPress: () => void handleSignOut() },
      ],
    );
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

  function handleRequestDeletion() {
    Alert.alert(
      "Request account deletion",
      "This signs this account out and flags it for deletion follow-up.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Request deletion",
          style: "destructive",
          onPress: () => {
            void submitDeletionRequest();
          },
        },
      ],
    );
  }

  async function submitDeletionRequest() {
    setBusy(true);
    try {
      await runImperativeRequest((signal) => requestAccountDeletion(signal));
      if (ownerUserId && Platform.OS !== "web") {
        await purgeOwnerObservationQueue(ownerUserId);
      }
      await clearBearerToken();
      setTokenSaved(false);
      void me.refetch();
      Alert.alert("Deletion requested", "This account has been signed out.");
    } catch (err: unknown) {
      if (err instanceof ImperativeRequestSupersededError) return;
      Alert.alert(
        "Deletion request failed",
        err instanceof Error ? err.message : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  const signedInUser = me.data ??
    (session.status === "authenticated" ? session.user : null);

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
        {session.status === "initializing" ? (
          <Text style={styles.value}>checking…</Text>
        ) : signedInUser ? (
          <>
            <Text style={styles.value}>signed in as {signedInUser.display_name}</Text>
            <Text style={styles.help}>role: {signedInUser.role}</Text>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: busy, busy }}
              style={[styles.button, styles.buttonGhost, busy && styles.buttonDisabled]}
              onPress={() => void requestSignOut()}
              disabled={busy}
            >
              <Text style={[styles.buttonText, styles.buttonGhostText]}>Sign out</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: busy, busy }}
              style={[styles.button, styles.buttonDanger, busy && styles.buttonDisabled]}
              onPress={handleRequestDeletion}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Request account deletion</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.value}>not signed in</Text>
            <Pressable
              accessibilityRole="button"
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
        <Text style={styles.value}>build channel: {env.updatesChannel}</Text>
        <Text style={styles.value} testID="settings-updates-channel">
          updates channel: {Updates.channel ?? "not configured"}
        </Text>
        <Text style={styles.value} testID="settings-updates-enabled">
          updates enabled: {Updates.isEnabled ? "yes" : "no"}
        </Text>
        <Text style={styles.value} testID="settings-updates-source">
          updates source: {Updates.isEmbeddedLaunch ? "embedded" : "remote"}
        </Text>
        <Text style={styles.value}>updates runtime: {Updates.runtimeVersion ?? "none"}</Text>

        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        <Text style={styles.label}>Adult tools</Text>
        <Pressable
          accessibilityRole="button"
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.push("/groups")}
        >
          <Text style={[styles.buttonText, styles.buttonGhostText]}>Open groups</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.push("/review-queue")}
        >
          <Text style={[styles.buttonText, styles.buttonGhostText]}>Open review queue</Text>
        </Pressable>
        <Text style={styles.help}>
          Availability depends on the adult account's permissions. Kid
          accounts get a "not available" message.
        </Text>

        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        {devTokenShortcutEnabled && (
          <>
            <Text style={styles.label}>Auth (dev shortcut)</Text>
            <Text style={styles.help}>
              Paste a development bearer token to skip the sign-in flow.
              Regular users should use the sign-in screen above.
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
              accessibilityLabel="Development bearer token"
              autoComplete="off"
              spellCheck={false}
              style={styles.input}
              value={draft}
              onChangeText={setDraft}
              placeholder="paste bearer token here"
              placeholderTextColor="#6b7280"
              autoCapitalize="none"
              autoCorrect={false}
              multiline
            />
            <View style={styles.row}>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{
                  disabled: busy || draft.trim().length === 0,
                  busy,
                }}
                style={[
                  styles.button,
                  styles.buttonPrimary,
                  (busy || draft.trim().length === 0) && styles.buttonDisabled,
                ]}
                onPress={handleSaveDevToken}
                disabled={busy || draft.trim().length === 0}
              >
                <Text style={styles.buttonText}>Save token</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{
                  disabled: busy || tokenSaved !== true,
                  busy,
                }}
                style={[
                  styles.button,
                  styles.buttonGhost,
                  (busy || tokenSaved !== true) && styles.buttonDisabled,
                ]}
                onPress={handleClearDevToken}
                disabled={busy || tokenSaved !== true}
              >
                <Text style={[styles.buttonText, styles.buttonGhostText]}>Clear</Text>
              </Pressable>
            </View>
          </>
        )}
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
    color: "#1f2937",
    backgroundColor: "#fff",
  },
  row: {
    flexDirection: "row",
    gap: 8,
    marginTop: 12,
  },
  button: {
    minHeight: 44,
    minWidth: 44,
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
    backgroundColor: "#fff",
  },
  buttonDanger: {
    backgroundColor: "#991b1b",
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  buttonText: {
    fontSize: 14,
    color: "#fff",
  },
  buttonGhostText: {
    color: "#1f2937",
  },
});
