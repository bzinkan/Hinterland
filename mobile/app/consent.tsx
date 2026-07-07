import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, TextInput } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import { recordConsent } from "@/src/api/consent";

type Phase =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "done"; recordedAt: string; policyVersion: string }
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
  const [email, setEmail] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const submittable =
    phase.kind === "idle" && agreed && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);

  async function submit() {
    if (!submittable) return;
    setPhase({ kind: "submitting" });
    try {
      const result = await recordConsent(email.trim());
      setPhase({
        kind: "done",
        recordedAt: result.recorded_at,
        policyVersion: result.policy_version,
      });
    } catch (err) {
      setPhase({
        kind: "error",
        message:
          err instanceof ApiError ? `${err.status}: ${err.message}` : String(err),
      });
    }
  }

  return (
    <DesktopContainer>
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Parental Consent</Text>
        <Text style={styles.body}>
          Hinterland is a citizen-science field app for kids ages 9-12.
          Before your kid can use it, COPPA requires us to record your
          consent as a parent or guardian.
        </Text>

        <View
          style={styles.separator}
          lightColor="#eee"
          darkColor="rgba(255,255,255,0.1)"
        />

        <Text style={styles.sectionLabel}>What we collect from your kid</Text>
        <Text style={styles.body}>
          - Photos of plants and animals they observe outdoors
          {"\n"}- The location each photo was taken (rounded to ~city block)
          {"\n"}- The species ID they pick (or one our automatic suggester
          recommends from iNaturalist)
        </Text>
        <Text style={styles.sectionLabel}>What we don't collect</Text>
        <Text style={styles.body}>
          - No email or phone number from your kid
          {"\n"}- No browser cookies, advertising IDs, or trackers
          {"\n"}- No microphone, contacts, or calendar access
          {"\n"}- No third-party analytics
        </Text>
        <Text style={styles.sectionLabel}>Where the data goes</Text>
        <Text style={styles.body}>
          Photos and species IDs become public scientific observations under
          our iNaturalist project account. Locations are obfuscated for
          threatened species per iNaturalist's policy. You can view, export,
          or delete your kid's data at any time from the app's Settings.
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
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="parent@example.com"
          placeholderTextColor="#999"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          editable={phase.kind === "idle" || phase.kind === "error"}
        />

        <Pressable
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
            <Text style={styles.muted}>
              Next step: open the parents web app with this same email, create
              a kid account, then scan the kid QR code in the native app.
            </Text>
          </View>
        )}
        {phase.kind === "error" && (
          <Text style={styles.error}>● {phase.message}</Text>
        )}

        <Pressable
          style={[
            styles.button,
            styles.buttonPrimary,
            !submittable && styles.buttonDisabled,
          ]}
          disabled={!submittable}
          onPress={() => void submit()}
        >
          <Text style={styles.buttonText}>
            {phase.kind === "done" ? "Submitted ✓" : "Record consent"}
          </Text>
        </Pressable>
      </ScrollView>
    </DesktopContainer>
  );
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
    color: "#fff",
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
