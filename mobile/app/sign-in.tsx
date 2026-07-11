import { router, Stack } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  TextInput,
} from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { parentSignup } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import {
  clearPendingParentConsentProof,
  readPendingParentConsentProof,
  type ParentConsentProof,
} from "@/src/auth/consentProof";
import {
  getSignedInAdultProfile,
  refreshCurrentAdultSession,
  signIn as msalSignIn,
} from "@/src/auth/msal";
import { useAuthSession } from "@/src/auth/session";

const IS_WEB = Platform.OS === "web";
const PARENTS_WEB_URL = "https://parents.thehinterlandguide.app";

export default function SignInScreen() {
  if (IS_WEB) return <ParentWebSignIn />;

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Sign in" }} />
      <View style={styles.container}>
        <Text style={styles.title}>Sign in</Text>
        <Text style={styles.subtitle}>
          Parent and teacher setup happens on the parents web app. Kids use
          the QR code shown by their adult.
        </Text>
        <Pressable
          style={[styles.button, styles.buttonPrimary]}
          onPress={() => router.push("/kid-handoff")}
        >
          <Text style={styles.buttonText}>Scan kid QR</Text>
        </Pressable>

        <Pressable
          style={[styles.button, styles.buttonGhost]}
          onPress={() => {
            void Linking.openURL(PARENTS_WEB_URL);
          }}
        >
          <Text style={styles.buttonGhostText}>Open parent setup</Text>
        </Pressable>
      </View>
    </DesktopContainer>
  );
}

type AccountState =
  | { kind: "loading" }
  | { kind: "signed_out" }
  | { kind: "ready" }
  | { kind: "error" };

type SetupError = {
  message: string;
  needsCurrentConsent: boolean;
  supportCode: string | null;
};

export function ParentWebSignIn() {
  const session = useAuthSession();
  const isCanonicalAdult =
    session.status === "authenticated" && session.user.role !== "kid";
  const [accountState, setAccountState] = useState<AccountState>({
    kind: "loading",
  });
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [setupError, setSetupError] = useState<SetupError | null>(null);
  const [pendingProof, setPendingProof] = useState<ParentConsentProof | null>(() =>
    readPendingParentConsentProof(),
  );
  const existingAdultLinkInFlight = useRef(false);

  useEffect(() => {
    if (!isCanonicalAdult) return;
    if (!pendingProof) {
      router.replace("/classroom");
      return;
    }
    if (existingAdultLinkInFlight.current) return;
    existingAdultLinkInFlight.current = true;
    setResolving(true);
    setSetupError(null);
    void parentSignup(session.user.display_name, pendingProof).then(
      () => {
        try {
          clearPendingParentConsentProof(pendingProof);
          setPendingProof(null);
          router.replace("/classroom");
        } catch (error) {
          existingAdultLinkInFlight.current = false;
          setResolving(false);
          setSetupError(safeParentSetupError(error));
        }
      },
      (error: unknown) => {
        existingAdultLinkInFlight.current = false;
        setResolving(false);
        setSetupError(safeParentSetupError(error));
      },
    );
  }, [isCanonicalAdult, pendingProof, session]);

  useEffect(() => {
    if (isCanonicalAdult) return;
    let active = true;
    void getSignedInAdultProfile().then(
      (profile) => {
        if (!active) return;
        if (!profile) {
          setAccountState({ kind: "signed_out" });
          return;
        }
        setDisplayName(profile.suggestedDisplayName);
        setAccountState({ kind: "ready" });
      },
      () => {
        if (active) setAccountState({ kind: "error" });
      },
    );
    return () => {
      active = false;
    };
  }, [isCanonicalAdult]);

  async function handleMsalSignIn() {
    if (!readPendingParentConsentProof()) {
      setSetupError(missingParentConsentProofError());
      return;
    }
    setSubmitting(true);
    setSetupError(null);
    try {
      await msalSignIn();
      // loginRedirect navigates away; ensureTokenSync() handles the return.
    } catch {
      setSetupError(safeParentSetupError(null));
      setSubmitting(false);
    }
  }

  async function finishParentSetup() {
    const normalizedName = displayName.trim().replace(/\s+/g, " ");
    if (!normalizedName || Array.from(normalizedName).length > 80) return;
    setSubmitting(true);
    setSetupError(null);
    try {
      const proof = readPendingParentConsentProof();
      if (!proof) {
        setSetupError(missingParentConsentProofError());
        return;
      }
      await parentSignup(normalizedName, proof);
      clearPendingParentConsentProof(proof);
      setPendingProof(null);
      setResolving(true);
      await refreshCurrentAdultSession();
      // The token-change signal above makes AuthSessionCoordinator rerun
      // canonical /v1/me. Its authenticated state drives the redirect.
    } catch (error) {
      setResolving(false);
      setSetupError(safeParentSetupError(error));
    } finally {
      setSubmitting(false);
    }
  }

  const normalizedLength = Array.from(
    displayName.trim().replace(/\s+/g, " "),
  ).length;
  const nameIsValid = normalizedLength > 0 && normalizedLength <= 80;
  const waitingForIdentity = session.status === "initializing" || resolving;

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Parent sign in" }} />
      <View style={styles.container}>
        <Text style={styles.title}>Welcome to Hinterland</Text>
        <Text style={styles.subtitle}>
          Adults sign in with Microsoft. Kids use a QR code from their adult
          and never enter an email.
        </Text>

        {setupError && (
          <View style={styles.errorPanel} accessibilityRole="alert">
            <Text style={styles.error}>{setupError.message}</Text>
            {setupError.supportCode && (
              <Text style={styles.supportCode}>
                Adult support code: {setupError.supportCode}
              </Text>
            )}
            {setupError.needsCurrentConsent && (
              <Pressable
                accessibilityRole="link"
                accessibilityLabel="Review current pilot consent"
                onPress={() => router.push("/consent")}
                style={[styles.button, styles.buttonGhost]}
              >
                <Text style={styles.buttonGhostText}>
                  Review current pilot consent
                </Text>
              </Pressable>
            )}
          </View>
        )}

        {isCanonicalAdult ? (
          pendingProof ? (
            <Pressable
              testID="retry-existing-parent-consent-link"
              accessibilityRole="button"
              style={[
                styles.button,
                styles.buttonPrimary,
                resolving && styles.buttonDisabled,
              ]}
              disabled={resolving}
              onPress={() => {
                if (existingAdultLinkInFlight.current) return;
                existingAdultLinkInFlight.current = true;
                setSetupError(null);
                setResolving(true);
                void parentSignup(session.user.display_name, pendingProof).then(
                  () => {
                    try {
                      clearPendingParentConsentProof(pendingProof);
                      setPendingProof(null);
                      router.replace("/classroom");
                    } catch (error) {
                      existingAdultLinkInFlight.current = false;
                      setResolving(false);
                      setSetupError(safeParentSetupError(error));
                    }
                  },
                  (error: unknown) => {
                    existingAdultLinkInFlight.current = false;
                    setResolving(false);
                    setSetupError(safeParentSetupError(error));
                  },
                );
              }}
            >
              {resolving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.buttonText}>Retry consent link</Text>
              )}
            </Pressable>
          ) : (
            <Text style={styles.subtitle}>Opening your classroom…</Text>
          )
        ) : waitingForIdentity || accountState.kind === "loading" ? (
          <View style={styles.progressRow}>
            <ActivityIndicator />
            <Text style={styles.subtitle}>Finishing secure sign-in…</Text>
          </View>
        ) : accountState.kind === "ready" && pendingProof ? (
          <>
            <Text style={styles.label}>Adult display name</Text>
            <Text style={styles.subtitle}>
              Confirm the name adults in your group will recognize. We use
              your Microsoft account name only as an editable suggestion.
            </Text>
            <TextInput
              testID="parent-display-name"
              accessibilityLabel="Adult display name"
              accessibilityHint="Edit the name adults in your group will see"
              autoCapitalize="words"
              autoCorrect={false}
              maxLength={80}
              value={displayName}
              onChangeText={setDisplayName}
              style={styles.input}
            />
            <Pressable
              testID="finish-parent-setup"
              accessibilityRole="button"
              style={[
                styles.button,
                styles.buttonPrimary,
                (submitting || !nameIsValid) && styles.buttonDisabled,
              ]}
              disabled={submitting || !nameIsValid}
              onPress={() => void finishParentSetup()}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.buttonText}>Finish parent setup</Text>
              )}
            </Pressable>
          </>
        ) : pendingProof ? (
          <>
            <Pressable
              testID="parent-msal-sign-in"
              accessibilityRole="button"
              style={[
                styles.button,
                styles.buttonPrimary,
                submitting && styles.buttonDisabled,
              ]}
              disabled={submitting}
              onPress={() => void handleMsalSignIn()}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.buttonText}>Continue with Microsoft</Text>
              )}
            </Pressable>
            <Text style={styles.subtitle}>
              New here? Pick "Sign up now" on the Microsoft sign-in page.
            </Text>
          </>
        ) : (
          <>
            <Text style={styles.subtitle}>
              Review and record the current pilot consent in this browser tab
              before continuing with Microsoft.
            </Text>
            <Pressable
              accessibilityRole="link"
              accessibilityLabel="Review current pilot consent"
              onPress={() => router.push("/consent")}
              style={[styles.button, styles.buttonGhost]}
            >
              <Text style={styles.buttonGhostText}>Review current pilot consent</Text>
            </Pressable>
          </>
        )}
      </View>
    </DesktopContainer>
  );
}

export function safeParentSetupError(error: unknown): SetupError {
  if (error instanceof ApiError && error.status === 409) {
    return {
      message:
        "Your parent consent needs to be refreshed before setup can finish. Review the current pilot consent, then try again.",
      needsCurrentConsent: true,
      supportCode: error.body?.error.request_id ?? null,
    };
  }
  if (
    error instanceof ApiError &&
    (error.status === 401 || error.status === 403)
  ) {
    return {
      message: "Your Microsoft sign-in expired. Sign in again to continue parent setup.",
      needsCurrentConsent: false,
      supportCode: error.body?.error.request_id ?? null,
    };
  }
  if (error instanceof ApiError && error.status === 422) {
    return {
      message: "Choose an adult display name between 1 and 80 characters.",
      needsCurrentConsent: false,
      supportCode: error.body?.error.request_id ?? null,
    };
  }
  return {
    message:
      "We couldn't finish parent setup. No child account was created. Please try again.",
    needsCurrentConsent: false,
    supportCode:
      error instanceof ApiError ? (error.body?.error.request_id ?? null) : null,
  };
}

function missingParentConsentProofError(): SetupError {
  return {
    message:
      "Current pilot consent is required in this browser tab before Microsoft sign-in can continue.",
    needsCurrentConsent: true,
    supportCode: null,
  };
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24 },
  title: { fontSize: 22, fontWeight: "600", marginBottom: 6 },
  subtitle: { fontSize: 13, opacity: 0.7, marginBottom: 20 },
  error: { color: "#f87171", marginTop: 12, fontSize: 13 },
  errorPanel: { marginBottom: 12 },
  supportCode: { fontSize: 12, opacity: 0.7, marginTop: 6 },
  label: { fontSize: 13, fontWeight: "600", marginTop: 4, marginBottom: 6 },
  input: {
    minHeight: 44,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 16,
    color: "#1f2937",
    backgroundColor: "#fff",
  },
  progressRow: { gap: 12, alignItems: "flex-start" },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    marginTop: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
  },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff", fontWeight: "500" },
  buttonGhostText: { fontSize: 14, color: "#1f2937", fontWeight: "500" },
});
