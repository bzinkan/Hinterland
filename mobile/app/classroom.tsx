import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, Stack } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View as RNView,
} from "react-native";
import QRCode from "react-native-qrcode-svg";

import DesktopContainer from "@/components/DesktopContainer";
import { Text, View } from "@/components/Themed";
import { useColorScheme } from "@/components/useColorScheme";
import { ApiError } from "@/src/api/client";
import {
  type AgeBand,
  type CreateKidResponse,
  type Group,
  createGroup,
  createKid,
  listGroupMembers,
  listGroups,
  type RosterMember,
} from "@/src/api/groups";
import { useAuthSession } from "@/src/auth/session";
import { ImperativeRequestSupersededError } from "@/src/auth/requestBoundary";

const AGE_BANDS: AgeBand[] = ["9-10", "11-12", "13+"];

export default function ClassroomScreen() {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const groupsQuery = useQuery({
    queryKey: ["groups", ownerUserId ?? "anonymous"],
    queryFn: listGroups,
    enabled: ownerUserId != null,
  });
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  if (groupsQuery.isPending) {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Classroom" }} />
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      </DesktopContainer>
    );
  }

  if (groupsQuery.isError) {
    const err = groupsQuery.error;
    const isUnauthed =
      err instanceof ApiError && (err.status === 401 || err.status === 403);
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Classroom" }} />
        <View style={styles.center}>
          <Text style={styles.heading}>
            {isUnauthed ? "Sign in required" : "Couldn't load groups"}
          </Text>
          <Text style={styles.body}>
            {isUnauthed
              ? "The classroom view is for parent and teacher accounts."
              : err.message}
          </Text>
          <Pressable
            accessibilityRole="button"
            style={[styles.button, styles.buttonGhost]}
            onPress={() => router.back()}
          >
            <Text style={[styles.buttonText, styles.buttonGhostText]}>Back</Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  const groups = groupsQuery.data.items;
  const activeGroupId = selectedGroupId ?? groups[0]?.id ?? null;
  const activeGroup = groups.find((g) => g.id === activeGroupId) ?? null;

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Classroom" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Classroom</Text>
        <Text style={styles.subtitle}>
          Manage the kids in your groups and hand off accounts to their device.
        </Text>

        <GroupPicker
          groups={groups}
          activeGroupId={activeGroupId}
          onSelect={setSelectedGroupId}
        />

        {activeGroup ? (
          <GroupDetail group={activeGroup} />
        ) : (
          <NoGroupYet onCreated={(g) => setSelectedGroupId(g.id)} />
        )}
      </ScrollView>
    </DesktopContainer>
  );
}

function GroupPicker({
  groups,
  activeGroupId,
  onSelect,
}: {
  groups: Group[];
  activeGroupId: string | null;
  onSelect: (id: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const queryClient = useQueryClient();
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );

  const create = useMutation({
    mutationFn: createGroup,
    onSuccess: (g) => {
      void queryClient.invalidateQueries({
        queryKey: ["groups", ownerUserId ?? "anonymous"],
      });
      onSelect(g.id);
      setDraft("");
      setCreating(false);
    },
    onError: (err) => {
      if (!(err instanceof ImperativeRequestSupersededError)) {
        Alert.alert("Couldn't create group", apiErrorMessage(err));
      }
    },
  });

  if (groups.length === 0) return null;

  return (
    <RNView style={styles.section}>
      <Text style={styles.sectionLabel}>Group</Text>
      <RNView style={styles.tabRow}>
        {groups.map((g) => {
          const active = g.id === activeGroupId;
          return (
            <Pressable
              key={g.id}
              testID={`classroom-group-tab-${g.id}`}
              accessibilityRole="button"
              accessibilityLabel={`Open group ${g.name}`}
              accessibilityState={{ selected: active }}
              style={[styles.tab, active && styles.tabActive]}
              onPress={() => onSelect(g.id)}
            >
              <Text style={[styles.tabText, active && styles.tabTextActive]}>{g.name}</Text>
            </Pressable>
          );
        })}
        <Pressable
          testID="classroom-new-group-button"
          accessibilityRole="button"
          accessibilityState={{ expanded: creating }}
          style={[styles.tab, styles.tabGhost]}
          onPress={() => setCreating((v) => !v)}
        >
          <Text style={styles.tabText}>+ New</Text>
        </Pressable>
      </RNView>
      {creating && (
        <RNView style={styles.row}>
          <TextInput
            accessibilityLabel="Group name"
            autoComplete="off"
            style={[styles.input, { flex: 1 }]}
            value={draft}
            onChangeText={setDraft}
            placeholder="Group name (e.g. Mr. Smith's 5th grade)"
            placeholderTextColor="#6b7280"
          />
          <Pressable
            testID="classroom-create-group-button"
            accessibilityRole="button"
            accessibilityState={{
              disabled: create.isPending || draft.trim().length === 0,
              busy: create.isPending,
            }}
            style={[
              styles.button,
              styles.buttonPrimary,
              (create.isPending || draft.trim().length === 0) && styles.buttonDisabled,
            ]}
            disabled={create.isPending || draft.trim().length === 0}
            onPress={() => create.mutate(draft.trim())}
          >
            <Text style={styles.buttonText}>
              {create.isPending ? "Creating…" : "Create"}
            </Text>
          </Pressable>
        </RNView>
      )}
    </RNView>
  );
}

function NoGroupYet({ onCreated }: { onCreated: (g: Group) => void }) {
  const [draft, setDraft] = useState("");
  const queryClient = useQueryClient();
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const create = useMutation({
    mutationFn: createGroup,
    onSuccess: (g) => {
      void queryClient.invalidateQueries({
        queryKey: ["groups", ownerUserId ?? "anonymous"],
      });
      onCreated(g);
      setDraft("");
    },
    onError: (err) => {
      if (!(err instanceof ImperativeRequestSupersededError)) {
        Alert.alert("Couldn't create group", apiErrorMessage(err));
      }
    },
  });

  return (
    <RNView style={styles.section}>
      <Text style={styles.heading}>Create your first group</Text>
      <Text style={styles.body}>
        A group holds your kids and their observations. You can have a group
        per family, per class, or per club.
      </Text>
      <TextInput
        accessibilityLabel="Group name"
        autoComplete="off"
        style={styles.input}
        value={draft}
        onChangeText={setDraft}
        placeholder="Group name"
        placeholderTextColor="#6b7280"
      />
      <Pressable
        testID="classroom-create-first-group-button"
        accessibilityRole="button"
        accessibilityState={{
          disabled: create.isPending || draft.trim().length === 0,
          busy: create.isPending,
        }}
        style={[
          styles.button,
          styles.buttonPrimary,
          (create.isPending || draft.trim().length === 0) && styles.buttonDisabled,
        ]}
        disabled={create.isPending || draft.trim().length === 0}
        onPress={() => create.mutate(draft.trim())}
      >
        <Text style={styles.buttonText}>
          {create.isPending ? "Creating…" : "Create group"}
        </Text>
      </Pressable>
    </RNView>
  );
}

function GroupDetail({ group }: { group: Group }) {
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );
  const roster = useQuery({
    queryKey: ["group-members", ownerUserId ?? "anonymous", group.id],
    queryFn: () => listGroupMembers(group.id),
    enabled: ownerUserId != null,
  });
  const [showAdd, setShowAdd] = useState(false);
  const [handoff, setHandoff] = useState<CreateKidResponse | null>(null);

  return (
    <RNView style={styles.section}>
      <RNView style={styles.row}>
        <RNView style={{ flex: 1 }}>
          <Text style={styles.heading}>{group.name}</Text>
          <Text style={styles.help}>
            Join code: <Text style={styles.code}>{group.join_code}</Text> ·
            give this to a co-parent or co-teacher
          </Text>
        </RNView>
        <Pressable
          testID="classroom-add-kid-button"
          accessibilityRole="button"
          accessibilityLabel={`Add a kid to ${group.name}`}
          style={[styles.button, styles.buttonPrimary]}
          onPress={() => setShowAdd(true)}
        >
          <Text style={styles.buttonText}>Add kid</Text>
        </Pressable>
      </RNView>

      {roster.isPending ? (
        <ActivityIndicator style={{ marginTop: 16 }} />
      ) : roster.isError ? (
        <Text style={styles.body}>Couldn't load roster: {roster.error.message}</Text>
      ) : (
        <FlatList
          data={roster.data.items}
          keyExtractor={(m) => m.membership_id}
          ListEmptyComponent={
            <Text style={styles.body}>
              No members yet. Tap "Add kid" to provision the first account.
            </Text>
          }
          renderItem={({ item }) => <RosterRow member={item} />}
          scrollEnabled={false}
        />
      )}

      <AddKidModal
        visible={showAdd}
        groupId={group.id}
        onClose={() => setShowAdd(false)}
        onCreated={(resp) => {
          setShowAdd(false);
          setHandoff(resp);
        }}
      />

      <HandoffModal
        handoff={handoff}
        onClose={() => setHandoff(null)}
      />
    </RNView>
  );
}

function RosterRow({ member }: { member: RosterMember }) {
  const colorScheme = useColorScheme();
  const subtitle = member.role === "kid" ? `kid · age ${member.age_band ?? "?"}` : member.role;
  return (
    <RNView
      testID={`classroom-roster-row-${member.membership_id}`}
      style={[
        styles.rosterRow,
        colorScheme === "dark" ? styles.rosterRowDark : styles.rosterRowLight,
      ]}
    >
      <RNView style={{ flex: 1 }}>
        <Text style={styles.rosterName}>{member.display_name}</Text>
        <Text style={styles.rosterMeta}>{subtitle}</Text>
      </RNView>
      <Text style={styles.rosterMeta}>
        {member.observation_count} obs · {member.dex_count} dex
      </Text>
    </RNView>
  );
}

function AddKidModal({
  visible,
  groupId,
  onClose,
  onCreated,
}: {
  visible: boolean;
  groupId: string;
  onClose: () => void;
  onCreated: (resp: CreateKidResponse) => void;
}) {
  const [name, setName] = useState("");
  const [ageBand, setAgeBand] = useState<AgeBand>("9-10");
  const queryClient = useQueryClient();
  const ownerUserId = useAuthSession((state) =>
    state.status === "authenticated" ? state.user.id : null,
  );

  const create = useMutation({
    mutationFn: () => createKid(groupId, name.trim(), ageBand),
    onSuccess: (resp) => {
      void queryClient.invalidateQueries({
        queryKey: ["group-members", ownerUserId ?? "anonymous", groupId],
      });
      setName("");
      setAgeBand("9-10");
      onCreated(resp);
    },
    onError: (err) => {
      if (!(err instanceof ImperativeRequestSupersededError)) {
        Alert.alert("Couldn't create kid", apiErrorMessage(err));
      }
    },
  });

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <RNView style={styles.modalScrim}>
        <View style={styles.modalCard}>
          <Text style={styles.heading}>Add a kid</Text>
          <Text style={styles.help}>
            Creates the account and shows a handoff QR for the kid's device.
          </Text>

          <Text style={styles.sectionLabel}>Display name</Text>
          <TextInput
            testID="classroom-kid-display-name"
            accessibilityLabel="Kid display name"
            autoComplete="off"
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="First name or nickname"
            placeholderTextColor="#6b7280"
            autoCapitalize="words"
            autoCorrect={false}
          />

          <Text style={styles.sectionLabel}>Age band</Text>
          <RNView
            accessibilityRole="radiogroup"
            accessibilityLabel="Age band selection"
            style={styles.tabRow}
          >
            {AGE_BANDS.map((band) => {
              const active = band === ageBand;
              return (
                <Pressable
                  key={band}
                  testID={`classroom-age-band-${band}`}
                  accessibilityRole="radio"
                  accessibilityLabel={`Age band ${band}`}
                  accessibilityState={{ checked: active }}
                  style={[styles.tab, active && styles.tabActive]}
                  onPress={() => setAgeBand(band)}
                >
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>{band}</Text>
                </Pressable>
              );
            })}
          </RNView>

          <RNView style={styles.row}>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: create.isPending }}
              style={[
                styles.button,
                styles.buttonGhost,
                create.isPending && styles.buttonDisabled,
                { flex: 1 },
              ]}
              onPress={onClose}
              disabled={create.isPending}
            >
              <Text style={[styles.buttonText, styles.buttonGhostText]}>Cancel</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{
                disabled: create.isPending || !name.trim(),
                busy: create.isPending,
              }}
              style={[
                styles.button,
                styles.buttonPrimary,
                { flex: 1 },
                (create.isPending || !name.trim()) && styles.buttonDisabled,
              ]}
              disabled={create.isPending || !name.trim()}
              onPress={() => create.mutate()}
            >
              <Text style={styles.buttonText}>
                {create.isPending ? "Creating…" : "Create"}
              </Text>
            </Pressable>
          </RNView>
        </View>
      </RNView>
    </Modal>
  );
}

function HandoffModal({
  handoff,
  onClose,
}: {
  handoff: CreateKidResponse | null;
  onClose: () => void;
}) {
  return (
    <Modal
      visible={handoff != null}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <RNView style={styles.modalScrim}>
        <View style={styles.modalCard}>
          <Text style={styles.heading}>Hand off to {handoff?.display_name}</Text>
          <Text style={styles.help}>
            Open Hinterland on the kid's device and scan this code. The token
            is one-time-use; if the kid doesn't sign in within a few minutes
            you'll need to re-issue from their roster row.
          </Text>
          <RNView style={styles.qrWrap}>
            {handoff && (
              <QRCode
                value={JSON.stringify({
                  v: 1,
                  kind: "hinterland.kid-handoff.v1",
                  handoff_token: handoff.handoff_token,
                })}
                size={240}
                backgroundColor="#fff"
                color="#000"
              />
            )}
          </RNView>
          <Pressable
            accessibilityRole="button"
            style={[styles.button, styles.buttonPrimary]}
            onPress={onClose}
          >
            <Text style={styles.buttonText}>Done</Text>
          </Pressable>
        </View>
      </RNView>
    </Modal>
  );
}

function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

const styles = StyleSheet.create({
  container: { padding: 24 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  title: { fontSize: 22, fontWeight: "600" },
  subtitle: { fontSize: 13, opacity: 0.7, marginTop: 4, marginBottom: 16 },
  section: { marginTop: 16 },
  sectionLabel: { fontSize: 13, fontWeight: "600", opacity: 0.7, marginTop: 12 },
  heading: { fontSize: 16, fontWeight: "600" },
  body: { fontSize: 14, opacity: 0.75, marginTop: 4 },
  help: { fontSize: 12, opacity: 0.6, marginTop: 4, marginBottom: 8 },
  code: { fontFamily: "SpaceMono", fontSize: 13 },
  tabRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  tab: {
    minHeight: 44,
    minWidth: 44,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    backgroundColor: "#fff",
  },
  tabActive: { backgroundColor: "#2f6feb", borderColor: "#2f6feb" },
  tabGhost: { borderStyle: "dashed" },
  tabText: { fontSize: 13, color: "#1f2937", opacity: 0.85 },
  tabTextActive: { color: "#fff", opacity: 1, fontWeight: "600" },
  row: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 8 },
  input: {
    minHeight: 40,
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 6,
    paddingHorizontal: 10,
    fontSize: 14,
    color: "#1f2937",
    backgroundColor: "#fff",
    marginTop: 6,
  },
  button: {
    minHeight: 44,
    minWidth: 44,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 6,
    alignItems: "center",
    marginTop: 8,
  },
  buttonPrimary: { backgroundColor: "#2f6feb" },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    backgroundColor: "#fff",
  },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff" },
  buttonGhostText: { color: "#1f2937" },
  rosterRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  rosterRowLight: { borderBottomColor: "rgba(31,41,55,0.15)" },
  rosterRowDark: { borderBottomColor: "rgba(255,255,255,0.1)" },
  rosterName: { fontSize: 14, fontWeight: "500" },
  rosterMeta: { fontSize: 12, opacity: 0.6, marginTop: 2 },
  modalScrim: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.6)",
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  modalCard: {
    width: "100%",
    maxWidth: 480,
    padding: 20,
    borderRadius: 10,
  },
  qrWrap: {
    backgroundColor: "#fff",
    padding: 16,
    borderRadius: 8,
    alignSelf: "center",
    marginVertical: 16,
  },
});
