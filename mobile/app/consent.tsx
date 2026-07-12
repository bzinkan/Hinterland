import { router } from "expo-router";
import { useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, TextInput } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { parentSignup } from "@/src/api/auth";
import { ApiError } from "@/src/api/client";
import { recordConsent } from "@/src/api/consent";
import {
  clearPendingParentConsentProof,
  generateParentConsentNonce,
  ParentConsentProofUnavailableError,
  readPendingParentConsentProof,
  storePendingParentConsentProof,
  type ParentConsentProof,
} from "@/src/auth/consentProof";
import { useAuthSession } from "@/src/auth/session";

type Phase =
  | { kind: "idle" }
  | { kind: "submitting" }
  | {
      kind: "done";
      receiptId: string;
      recordedAt: string;
      policyVersion: string;
      linkage: "awaiting_sign_in" | "linking" | "linked" | "error";
      message?: string;
      supportCode?: string | null;
    }
  | { kind: "error"; message: string };

/**
 * Public unauthenticated /consent page. Per docs/mobile.md the web build
 * is the parent-facing surface; this is the page a parent visits first
 * to record COPPA consent before signing up.
 *
 * The actual adult account flow lives in the parents web app through
 * Microsoft Entra External Identities. Native kid sign-in uses the QR
 * handoff route after an adult creates the kid account.
 */
export default function ConsentScreen() {
  const session = useAuthSession();
  const [email, setEmail] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  // Reuse this nonce after an ambiguous network response so the server's
  // nonce idempotency returns the original receipt instead of creating one.
  const recordingNonce = useRef<string | null>(null);

  const acceptsSubmission = phase.kind === "idle" || phase.kind === "error";
  const submittable =
    acceptsSubmission && agreed && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);

  async function submit() {
    if (!submittable) return;
    setPhase({ kind: "submitting" });
    try {
      const nonce = recordingNonce.current ?? generateParentConsentNonce();
      recordingNonce.current = nonce;
      const result = await recordConsent(email.trim(), nonce);
      const proof: ParentConsentProof = {
        receiptId: result.id,
        nonce,
        policyVersion: result.policy_version,
      };
      storePendingParentConsentProof(proof);
      recordingNonce.current = null;
      const recorded: Extract<Phase, { kind: "done" }> = {
        kind: "done",
        receiptId: result.id,
        recordedAt: result.recorded_at,
        policyVersion: result.policy_version,
        linkage: "awaiting_sign_in",
      };
      if (session.status === "authenticated" && session.user.role !== "kid") {
        await linkExistingParent(proof, recorded);
      } else {
        setPhase(recorded);
      }
    } catch (err) {
      setPhase({
        kind: "error",
        message: safeConsentRecordingError(err),
      });
    }
  }

  async function linkExistingParent(
    proof: ParentConsentProof,
    recorded: Extract<Phase, { kind: "done" }>,
  ): Promise<void> {
    if (session.status !== "authenticated" || session.user.role === "kid") {
      setPhase({ ...recorded, linkage: "awaiting_sign_in" });
      return;
    }
    setPhase({ ...recorded, linkage: "linking" });
    try {
      await parentSignup(session.user.display_name, proof);
      clearPendingParentConsentProof(proof);
      setPhase({ ...recorded, linkage: "linked" });
    } catch (error) {
      setPhase({
        ...recorded,
        linkage: "error",
        message:
          "Consent was recorded, but we could not link it to your adult account yet. Retry the link; do not record another receipt.",
        supportCode:
          error instanceof ApiError ? (error.body?.error.request_id ?? null) : null,
      });
    }
  }

  async function retryExistingParentLink() {
    if (phase.kind !== "done") return;
    const proof = readPendingParentConsentProof();
    if (!proof) {
      setPhase({
        ...phase,
        linkage: "error",
        message:
          "The temporary consent proof is missing. Return to consent and record a new receipt before continuing.",
      });
      return;
    }
    await linkExistingParent(proof, phase);
  }

  return (
    <DesktopContainer>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Parental Consent</Text>
        <Text style={styles.body}>
          Hinterland is preparing a small, adult-supervised Android Internal
          Testing pilot for kids ages 9-12. Before a kid uses it, we record
          consent from their parent or guardian.
        </Text>

        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        <Text style={styles.sectionLabel}>What we collect from your kid</Text>
        <Text style={styles.body}>
          - Photos of organisms they observe outdoors. Once uploaded and saved,
          server-hosted W1 private-pilot photo bytes are purged after seven
          days. Unsynced work remains only on the original device until it
          syncs or an adult discards it
          {"\n"}- An optional coarse area computed on the device. No location
          is valid, and precise coordinates are not stored
          {"\n"}- The catalog identification, manual label, or Unknown choice
          they save, plus the observed time
        </Text>
        <Text style={styles.sectionLabel}>What we don't collect</Text>
        <Text style={styles.body}>
          - No email or phone number from your kid
          {"\n"}- No browser cookies, advertising IDs, or trackers
          {"\n"}- No microphone, contacts, or calendar access
          {"\n"}- No third-party analytics
        </Text>
        <Text style={styles.sectionLabel}>How this consent is linked</Text>
        <Text style={styles.body}>
          This browser tab keeps a random temporary setup proof through
          Microsoft sign-in. It is not a tracker. The API stores only its
          SHA-256 digest and the tab clears the proof after consent is linked
          to your adult account.
        </Text>
        <Text style={styles.sectionLabel}>Where the data goes</Text>
        <Text style={styles.body}>
          W1 data stays in the isolated Hinterland Azure environment. W1 does
          not send photos to iNaturalist, a photo-identification provider, or
          Azure Content Safety, and it does not publish observations. Photos
          remain unavailable to kids while the W1 private-pilot state is in
          effect.
        </Text>
        <Text style={styles.sectionLabel}>Full policy</Text>
        <Text style={styles.body}>
          The complete privacy policy is at
          {"\n"}https://thehinterlandguide.app/privacy (currently DRAFT pending
          legal review).
        </Text>

        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        <Text style={styles.sectionLabel}>Your email</Text>
        <TextInput
          accessibilityLabel="Parent or guardian email"
          autoComplete="email"
          inputMode="email"
          spellCheck={false}
          textContentType="emailAddress"
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="parent@example.com"
          placeholderTextColor="#6b7280"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          editable={phase.kind === "idle" || phase.kind === "error"}
        />

        <Pressable
          testID="consent-agreement-checkbox"
          accessibilityRole="checkbox"
          accessibilityState={{ checked: agreed }}
          style={styles.checkboxRow}
          onPress={() => setAgreed((x) => !x)}
        >
          <View style={[styles.checkbox, agreed && styles.checkboxChecked]}>
            {agreed && <Text style={styles.checkboxMark}>✓</Text>}
          </View>
          <Text style={styles.checkboxLabel}>
            I am the parent or legal guardian of the kid who will use this
            account, and I consent to Hinterland collecting the data described
            above.
          </Text>
        </Pressable>

        {phase.kind === "submitting" && <Text style={styles.muted}>Recording…</Text>}
        {phase.kind === "done" && (
          <View style={styles.success}>
            <Text style={styles.successHeading}>● Consent recorded</Text>
            <Text style={styles.muted}>
              Saved at {new Date(phase.recordedAt).toLocaleString()} ·
              policy version {phase.policyVersion}.
            </Text>
            <Text style={styles.muted}>Receipt: {phase.receiptId}</Text>
            {phase.linkage === "awaiting_sign_in" ? (
              <Text style={styles.muted}>
                Next step: continue with Microsoft in this browser tab. After
                sign-in, create a kid account, then scan the kid QR code in the
                native app.
              </Text>
            ) : phase.linkage === "linking" ? (
              <Text style={styles.muted}>Linking consent to your adult account…</Text>
            ) : phase.linkage === "linked" ? (
              <Text style={styles.muted}>Consent is linked to your adult account.</Text>
            ) : (
              <>
                <Text testID="consent-link-error" style={styles.error}>
                  ● {phase.message}
                </Text>
                {phase.supportCode ? (
                  <Text style={styles.muted}>
                    Adult support code: {phase.supportCode}
                  </Text>
                ) : null}
              </>
            )}
          </View>
        )}
        {phase.kind === "error" && (
          <Text testID="consent-error" style={styles.error}>
            ● {phase.message}
          </Text>
        )}

        {phase.kind === "done" ? (
          <Pressable
            testID="consent-next-button"
            accessibilityRole="button"
            style={[
              styles.button,
              styles.buttonPrimary,
              phase.linkage === "linking" && styles.buttonDisabled,
            ]}
            disabled={phase.linkage === "linking"}
            onPress={() => {
              if (phase.linkage === "error") {
                void retryExistingParentLink();
              } else if (phase.linkage === "linked") {
                router.replace("/classroom");
              } else {
                router.push("/sign-in");
              }
            }}
          >
            <Text style={styles.buttonText}>
              {phase.linkage === "error"
                ? "Retry consent link"
                : phase.linkage === "linked"
                  ? "Open classroom"
                  : "Continue with Microsoft"}
            </Text>
          </Pressable>
        ) : (
          <Pressable
            testID="consent-submit-button"
            accessibilityRole="button"
            style={[
              styles.button,
              styles.buttonPrimary,
              !submittable && styles.buttonDisabled,
            ]}
            disabled={!submittable}
            onPress={() => void submit()}
          >
            <Text style={styles.buttonText}>Record consent</Text>
          </Pressable>
        )}
      </ScrollView>
    </DesktopContainer>
  );
}

function safeConsentRecordingError(error: unknown): string {
  if (error instanceof ParentConsentProofUnavailableError) {
    return "This browser cannot securely hold the temporary consent proof. Enable session storage and try again.";
  }
  if (error instanceof ApiError && error.status === 409) {
    return "This consent attempt no longer matches the current pilot policy. Refresh this page and review the current consent.";
  }
  return "We could not record consent. It is safe to retry; the same private attempt will be reused.";
}

const styles = StyleSheet.create({
  container: { padding: 24 },
  title: { fontSize: 24, fontWeight: "600", marginBottom: 12 },
  sectionLabel: {
    fontSize: 13,
    fontWeight: "600",
    opacity: 0.8,
    marginTop: 16,
    marginBottom: 4,
  },
  body: { fontSize: 14, lineHeight: 22, opacity: 0.9 },
  muted: { fontSize: 12, opacity: 0.7, marginTop: 8 },
  error: { fontSize: 14, color: "#ef4444", marginTop: 12 },
  success: { marginTop: 12 },
  successHeading: { fontSize: 14, color: "#22c55e" },
  separator: { marginVertical: 16, height: 1, width: "100%" },
  input: {
    width: "100%",
    height: 40,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 8,
    marginTop: 4,
    marginBottom: 12,
    fontSize: 14,
    color: "#1f2937",
    backgroundColor: "#fff",
  },
  checkboxRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    marginTop: 4,
    marginBottom: 16,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "#888",
    borderRadius: 4,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
  },
  checkboxChecked: { backgroundColor: "#2f6feb", borderColor: "#2f6feb" },
  checkboxMark: { color: "#fff", fontSize: 14, fontWeight: "700" },
  checkboxLabel: { flex: 1, fontSize: 14, lineHeight: 20 },
  button: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: "center",
    marginTop: 12,
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff" },
});
