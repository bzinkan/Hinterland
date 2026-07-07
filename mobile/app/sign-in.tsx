import { router, Stack } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
} from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { signIn as msalSignIn } from "@/src/auth/msal";

const IS_WEB = Platform.OS === "web";
const PARENTS_WEB_URL = "https://parents.thehinterlandguide.app";

export default function SignInScreen() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleMsalSignIn() {
    setBusy(true);
    setError(null);
    try {
      await msalSignIn();
      // loginRedirect navigates away; ensureTokenSync() handles the return.
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (IS_WEB) {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Sign in" }} />
        <View style={styles.container}>
          <Text style={styles.title}>Welcome to Hinterland</Text>
          <Text style={styles.subtitle}>
            Hinterland parent and teacher accounts sign in with Microsoft
            Entra. Kids get a QR code from their adult and never enter an
            email.
          </Text>

          {error && <Text style={styles.error}>{error}</Text>}

          <Pressable
            style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
            disabled={busy}
            onPress={handleMsalSignIn}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>Continue with Microsoft</Text>
            )}
          </Pressable>

          <Text style={styles.subtitle}>
            New here? Pick "Sign up now" on the Microsoft sign-in page.
          </Text>
        </View>
      </DesktopContainer>
    );
  }

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Sign in" }} />
      <View style={styles.container}>
        <Text style={styles.title}>Sign in</Text>
        <Text style={styles.subtitle}>
          Parent and teacher setup happens on the parents web app. Kids use
          the QR code shown by their adult.
        </Text>

        {error && <Text style={styles.error}>{error}</Text>}

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

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24 },
  title: { fontSize: 22, fontWeight: "600", marginBottom: 6 },
  subtitle: { fontSize: 13, opacity: 0.7, marginBottom: 20 },
  error: { color: "#f87171", marginTop: 12, fontSize: 13 },
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
