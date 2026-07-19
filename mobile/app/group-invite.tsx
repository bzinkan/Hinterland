import { useMutation, useQueryClient } from "@tanstack/react-query";
import { router, Stack } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet } from "react-native";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { ApiError } from "@/src/api/client";
import { redeemAdultInvitation } from "@/src/api/groups";
import { signIn as msalSignIn } from "@/src/auth/msal";
import { rememberParentReturnPath } from "@/src/auth/parentReturnPath";
import { useAuthSession } from "@/src/auth/session";
import {
  captureInvitationTokenFromFragment,
  clearPendingInvitationToken,
  readPendingInvitationToken,
} from "@/src/groups/invitationToken";

type InviteState = "loading" | "available" | "unavailable";

export default function GroupInviteScreen() {
  const session = useAuthSession();
  const queryClient = useQueryClient();
  const [inviteState, setInviteState] = useState<InviteState>("loading");
  const [signInPending, setSignInPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [supportCode, setSupportCode] = useState<string | null>(null);

  useEffect(() => {
    const token = captureInvitationTokenFromFragment();
    setInviteState(token ? "available" : "unavailable");
  }, []);

  useEffect(() => {
    if (session.status === "authenticated" && session.user.role === "kid") {
      clearPendingInvitationToken();
      setInviteState("unavailable");
    }
  }, [session]);

  const redeem = useMutation({
    mutationFn: (token: string) => redeemAdultInvitation(token),
    gcTime: 0,
    onSuccess: async () => {
      clearPendingInvitationToken();
      setInviteState("unavailable");
      if (session.status === "authenticated") {
        await queryClient.invalidateQueries({
          queryKey: ["groups", session.user.id],
        });
      }
      redeem.reset();
      router.replace("/groups");
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 404 || error.status === 410)) {
        clearPendingInvitationToken();
        setInviteState("unavailable");
        setMessage("This invitation is expired, revoked, or no longer available.");
      } else if (error instanceof ApiError && error.status === 409) {
        setMessage(
          "This invitation cannot be used by this account. Review parental consent or ask the group owner for a new invitation.",
        );
      } else if (error instanceof ApiError && error.status === 401) {
        rememberParentReturnPath("/group-invite");
        setMessage("Your sign-in expired. Sign in again before joining the group.");
      } else {
        setMessage("We couldn't join the group. The invitation is still safe to retry.");
      }
      setSupportCode(error instanceof ApiError ? error.body?.error.request_id ?? null : null);
    },
  });

  function joinAsCurrentParent() {
    const token = readPendingInvitationToken();
    if (!token) {
      setInviteState("unavailable");
      setMessage("This invitation is no longer available in this browser tab.");
      return;
    }
    setMessage(null);
    setSupportCode(null);
    redeem.mutate(token);
  }

  async function chooseParentAccount() {
    rememberParentReturnPath("/group-invite");
    setSignInPending(true);
    setMessage(null);
    try {
      await msalSignIn();
    } catch {
      setMessage("Microsoft sign-in could not start. It is safe to try again.");
      setSignInPending(false);
    }
  }

  if (inviteState === "loading" || session.status === "initializing") {
    return (
      <InviteShell>
        <ActivityIndicator accessibilityLabel="Loading group invitation" />
      </InviteShell>
    );
  }

  if (inviteState === "unavailable" || (session.status === "authenticated" && session.user.role === "kid")) {
    return (
      <InviteShell>
        <Text style={styles.heading}>Invitation unavailable</Text>
        <Text style={styles.body}>
          {session.status === "authenticated" && session.user.role === "kid"
            ? "Group invitations are for parents and guardians."
            : message ?? "Ask the group owner for a new invitation link."}
        </Text>
        <Pressable
          testID="groups-invite-unavailable-open-groups"
          accessibilityRole="button"
          style={[styles.button, styles.buttonGhost]}
          onPress={() => router.replace("/groups")}
        >
          <Text style={styles.buttonGhostText}>Open groups</Text>
        </Pressable>
      </InviteShell>
    );
  }

  if (session.status === "anonymous") {
    return (
      <InviteShell>
        <Text style={styles.heading}>Join a group</Text>
        <Text style={styles.body}>
          Sign in as the parent or guardian who should join. You will confirm the invitation after sign-in.
        </Text>
        <Pressable
          testID="groups-invite-sign-in"
          accessibilityRole="button"
          style={[styles.button, styles.buttonPrimary]}
          onPress={() => {
            rememberParentReturnPath("/group-invite");
            router.push("/sign-in");
          }}
        >
          <Text style={styles.buttonText}>Sign in to join</Text>
        </Pressable>
      </InviteShell>
    );
  }

  return (
    <InviteShell>
      <Text style={styles.heading}>Join a group</Text>
      <Text style={styles.body}>
        Continue as {session.user.display_name}? Joining lets you add and manage only your own children.
      </Text>
      {message ? (
        <View accessibilityRole="alert" style={styles.errorPanel}>
          <Text style={styles.error}>{message}</Text>
          {supportCode ? <Text style={styles.support}>Adult support code: {supportCode}</Text> : null}
        </View>
      ) : null}
      <Pressable
        testID="groups-invite-confirm"
        accessibilityRole="button"
        accessibilityState={{ disabled: redeem.isPending, busy: redeem.isPending }}
        disabled={redeem.isPending}
        style={[styles.button, styles.buttonPrimary, redeem.isPending && styles.buttonDisabled]}
        onPress={joinAsCurrentParent}
      >
        <Text style={styles.buttonText}>
          {redeem.isPending ? "Joining…" : `Join as ${session.user.display_name}`}
        </Text>
      </Pressable>
      {message && redeem.error instanceof ApiError && redeem.error.status === 409 ? (
        <Pressable
          testID="groups-invite-review-consent"
          accessibilityRole="button"
          style={[styles.button, styles.buttonGhost]}
          onPress={() => {
            rememberParentReturnPath("/group-invite");
            router.push("/consent");
          }}
        >
          <Text style={styles.buttonGhostText}>Review parental consent</Text>
        </Pressable>
      ) : null}
      <Pressable
        testID="groups-invite-switch-account"
        accessibilityRole="button"
        accessibilityState={{ disabled: signInPending, busy: signInPending }}
        disabled={signInPending || redeem.isPending}
        style={[styles.button, styles.buttonGhost, (signInPending || redeem.isPending) && styles.buttonDisabled]}
        onPress={() => void chooseParentAccount()}
      >
        <Text style={styles.buttonGhostText}>
          {signInPending ? "Opening Microsoft…" : "Use a different parent account"}
        </Text>
      </Pressable>
    </InviteShell>
  );
}

function InviteShell({ children }: { children: React.ReactNode }) {
  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Group invitation" }} />
      <View style={styles.container}>{children}</View>
    </DesktopContainer>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24 },
  heading: { fontSize: 22, fontWeight: "600" },
  body: { fontSize: 14, lineHeight: 21, opacity: 0.75, marginTop: 8 },
  errorPanel: { marginTop: 12 },
  error: { fontSize: 14, color: "#b42318" },
  support: { fontSize: 12, opacity: 0.65, marginTop: 4 },
  button: {
    minHeight: 44,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
    alignItems: "center",
    marginTop: 12,
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: { borderWidth: StyleSheet.hairlineWidth, borderColor: "#888", backgroundColor: "#fff" },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { color: "#fff", fontSize: 14 },
  buttonGhostText: { color: "#1f2937", fontSize: 14 },
});
