import { router, Stack } from "expo-router";
import { FirebaseError } from "firebase/app";
import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
} from "firebase/auth";
import { useState } from "react";
import { ActivityIndicator, Platform, Pressable, StyleSheet, TextInput } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { parentSignup } from "@/src/api/auth";
import { getFirebaseAuth } from "@/src/auth/firebase";
import { signIn as msalSignIn } from "@/src/auth/msal";

type Mode = "sign-in" | "sign-up";
const IS_WEB = Platform.OS === "web";

export default function SignInScreen() {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) return;
    if (mode === "sign-up" && !displayName.trim()) {
      setError("Please enter a display name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const auth = getFirebaseAuth();
      if (mode === "sign-up") {
        await createUserWithEmailAndPassword(auth, trimmedEmail, password);
        // onIdTokenChanged in firebase.ts has now written the bearer
        // token to storage, so this authenticated call will succeed.
        await parentSignup(displayName.trim());
      } else {
        await signInWithEmailAndPassword(auth, trimmedEmail, password);
      }
      router.replace("/");
    } catch (err: unknown) {
      setError(humanizeAuthError(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleMsalSignIn() {
    setBusy(true);
    setError(null);
    try {
      await msalSignIn();
      // loginRedirect navigates away; the rest of the flow is handled
      // by ensureTokenSync()'s handleRedirectPromise on return.
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
          <Text style={styles.title}>Welcome to Dragonfly</Text>
          <Text style={styles.subtitle}>
            Dragonfly parent and teacher accounts sign in with Microsoft
            Entra. Kids get a join code from you and never enter an email.
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
      <Stack.Screen
        options={{ title: mode === "sign-in" ? "Sign in" : "Create account" }}
      />
      <View style={styles.container}>
        <Text style={styles.title}>
          {mode === "sign-in" ? "Welcome back" : "Create a parent account"}
        </Text>
        <Text style={styles.subtitle}>
          Dragonfly accounts are for parents and teachers. Kids get a join
          code from you and never enter an email.
        </Text>

        {mode === "sign-up" && (
          <>
            <Text style={styles.label}>Display name</Text>
            <TextInput
              style={styles.input}
              value={displayName}
              onChangeText={setDisplayName}
              placeholder="Mr. Smith"
              placeholderTextColor="#999"
              autoCapitalize="words"
              autoCorrect={false}
            />
          </>
        )}

        <Text style={styles.label}>Email</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          placeholderTextColor="#999"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
        />

        <Text style={styles.label}>Password</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          placeholder="••••••••"
          placeholderTextColor="#999"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          textContentType={mode === "sign-up" ? "newPassword" : "password"}
        />

        {error && <Text style={styles.error}>{error}</Text>}

        <Pressable
          style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
          disabled={busy || !email.trim() || !password}
          onPress={handleSubmit}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>
              {mode === "sign-in" ? "Sign in" : "Create account"}
            </Text>
          )}
        </Pressable>

        <Pressable
          style={styles.linkButton}
          onPress={() => {
            setMode(mode === "sign-in" ? "sign-up" : "sign-in");
            setError(null);
          }}
        >
          <Text style={styles.linkText}>
            {mode === "sign-in"
              ? "New here? Create an account."
              : "Already have an account? Sign in."}
          </Text>
        </Pressable>
      </View>
    </DesktopContainer>
  );
}

function humanizeAuthError(err: unknown): string {
  if (err instanceof FirebaseError) {
    switch (err.code) {
      case "auth/invalid-credential":
      case "auth/wrong-password":
      case "auth/user-not-found":
        return "Wrong email or password.";
      case "auth/email-already-in-use":
        return "That email is already registered. Try signing in instead.";
      case "auth/weak-password":
        return "Password must be at least 6 characters.";
      case "auth/invalid-email":
        return "That doesn't look like a valid email.";
      case "auth/network-request-failed":
        return "Network error. Check your connection and try again.";
      default:
        return err.message;
    }
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24 },
  title: { fontSize: 22, fontWeight: "600", marginBottom: 6 },
  subtitle: { fontSize: 13, opacity: 0.7, marginBottom: 20 },
  label: { fontSize: 13, fontWeight: "600", opacity: 0.7, marginTop: 12 },
  input: {
    width: "100%",
    minHeight: 44,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 10,
    marginTop: 6,
    fontSize: 14,
    color: "#fff",
  },
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
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff", fontWeight: "500" },
  linkButton: { marginTop: 16, alignItems: "center" },
  linkText: { fontSize: 13, color: "#2f6feb" },
});
