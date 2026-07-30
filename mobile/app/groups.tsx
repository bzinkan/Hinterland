import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router, Stack } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
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
  type AdultInvitation,
  type AgeBand,
  type CreateKidResponse,
  type Group,
  type OwnedChildInventoryItem,
  type OwnChildRosterMember,
  archiveGroup,
  createAdultInvitation,
  createGroup,
  createKid,
  listAdultInvitations,
  listGroupMembers,
  listGroups,
  listOwnedChildren,
  placeOwnedChildInGroup,
  removeAdultMember,
  reissueKidHandoff,
  revokeAdultInvitation,
  updateGroup,
} from "@/src/api/groups";
import { useAuthSession } from "@/src/auth/session";
import { ImperativeRequestSupersededError } from "@/src/auth/requestBoundary";
import {
  copyInvitationUrl,
  validateInvitationUrl,
} from "@/src/groups/invitationToken";
import { useGroupCapabilities } from "@/src/groups/useGroupCapabilities";

const AGE_BANDS: AgeBand[] = ["9-10", "11-12", "13+"];

export default function GroupsScreen() {
  const session = useAuthSession();
  const ownerUserId =
    session.status === "authenticated" && session.user.role !== "kid"
      ? session.user.id
      : null;
  const parentUserId =
    session.status === "authenticated" && session.user.role === "parent"
      ? session.user.id
      : null;
  const groupsQuery = useQuery({
    queryKey: ["groups", ownerUserId ?? "anonymous"],
    queryFn: listGroups,
    enabled: ownerUserId != null,
  });
  const ownedChildrenQuery = useQuery({
    queryKey: ["owned-children", parentUserId ?? "anonymous"],
    queryFn: listOwnedChildren,
    enabled: parentUserId != null,
  });
  const { sharedGroupsEnabled } = useGroupCapabilities();
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);

  useEffect(() => {
    setSelectedGroupId(null);
  }, [ownerUserId]);

  useEffect(() => {
    if (
      selectedGroupId &&
      groupsQuery.data &&
      !groupsQuery.data.items.some((group) => group.id === selectedGroupId)
    ) {
      setSelectedGroupId(null);
    }
  }, [groupsQuery.data, selectedGroupId]);

  if (session.status === "initializing") {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Groups" }} />
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      </DesktopContainer>
    );
  }

  if (session.status === "anonymous") {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Groups" }} />
        <View style={styles.center}>
          <Text style={styles.heading}>Sign in to manage groups</Text>
          <Text style={styles.body}>
            Parents can create groups and manage their own children.
          </Text>
          <Pressable
            testID="groups-sign-in-button"
            accessibilityRole="button"
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => router.replace("/sign-in")}
          >
            <Text style={styles.buttonText}>Sign in</Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  if (session.user.role === "kid") {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Groups" }} />
        <View style={styles.center}>
          <Text style={styles.heading}>Groups are managed by adults</Text>
          <Text style={styles.body}>
            Ask your parent or guardian if your group needs to change.
          </Text>
        </View>
      </DesktopContainer>
    );
  }

  if (groupsQuery.isPending) {
    return (
      <DesktopContainer>
        <Stack.Screen options={{ title: "Groups" }} />
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
        <Stack.Screen options={{ title: "Groups" }} />
        <View style={styles.center}>
          <Text style={styles.heading}>
            {isUnauthed ? "Sign in required" : "Couldn't load groups"}
          </Text>
          <Text style={styles.body}>
            {isUnauthed
              ? "Sign in again to manage your groups."
              : "Please try again. Your group data has not changed."}
          </Text>
          <Pressable
            accessibilityRole="button"
            style={[styles.button, styles.buttonGhost]}
            onPress={() =>
              isUnauthed ? router.replace("/sign-in") : void groupsQuery.refetch()
            }
          >
            <Text style={[styles.buttonText, styles.buttonGhostText]}>
              {isUnauthed ? "Sign in" : "Try again"}
            </Text>
          </Pressable>
        </View>
      </DesktopContainer>
    );
  }

  const groups = groupsQuery.data.items;
  const activeGroupId = selectedGroupId ?? groups[0]?.id ?? null;
  const activeGroup = groups.find((g) => g.id === activeGroupId) ?? null;
  const ungroupedChildren =
    ownedChildrenQuery.data?.items.filter((child) => child.active_group_id == null) ?? [];
  const eligibleGroups = groups.filter((group) => group.permissions.can_add_child);

  return (
    <DesktopContainer>
      <Stack.Screen options={{ title: "Groups" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.title}>Groups</Text>
        <Text style={styles.subtitle}>
          Manage your groups and hand off your children's accounts to their devices.
        </Text>

        <GroupPicker
          groups={groups}
          activeGroupId={activeGroupId}
          canCreateGroup={session.user.role === "parent"}
          onSelect={setSelectedGroupId}
        />

        {session.user.role === "parent" && ungroupedChildren.length > 0 ? (
          <UngroupedChildrenSection
            key={session.user.id}
            userId={session.user.id}
            children={ungroupedChildren}
            eligibleGroups={eligibleGroups}
            onPlaced={setSelectedGroupId}
          />
        ) : null}

        {session.user.role === "parent" && ownedChildrenQuery.isError ? (
          <RNView style={styles.section}>
            <Text style={styles.body}>
              Couldn't check whether any of your children need a group. Try refreshing this page.
            </Text>
          </RNView>
        ) : null}

        {activeGroup ? (
          <GroupDetail
            key={`${ownerUserId ?? "anonymous"}:${activeGroup.id}`}
            group={activeGroup}
            sharedGroupsEnabled={sharedGroupsEnabled}
          />
        ) : session.user.role === "parent" ? (
          <NoGroupYet onCreated={(g) => setSelectedGroupId(g.id)} />
        ) : (
          <RNView style={styles.section}>
            <Text style={styles.heading}>No groups available</Text>
            <Text style={styles.body}>This compatibility account cannot create groups.</Text>
          </RNView>
        )}
      </ScrollView>
    </DesktopContainer>
  );
}

function UngroupedChildrenSection({
  userId,
  children,
  eligibleGroups,
  onPlaced,
}: {
  userId: string;
  children: OwnedChildInventoryItem[];
  eligibleGroups: Group[];
  onPlaced: (groupId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedGroups, setSelectedGroups] = useState<Record<string, string>>({});
  const placeChild = useMutation({
    mutationKey: ["place-owned-child", userId],
    gcTime: 0,
    mutationFn: ({ childId, groupId }: { childId: string; groupId: string }) =>
      placeOwnedChildInGroup(groupId, childId),
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["owned-children", userId] }),
        queryClient.invalidateQueries({ queryKey: ["groups", userId] }),
        queryClient.invalidateQueries({
          queryKey: ["group-members", userId, variables.groupId],
        }),
      ]);
      onPlaced(variables.groupId);
    },
    onError: (error) => {
      if (!(error instanceof ImperativeRequestSupersededError)) {
        Alert.alert(
          "Couldn't add child to group",
          safeAdultError(error, "Try again. Your child's group has not changed."),
        );
      }
    },
  });

  return (
    <RNView testID="groups-ungrouped-children" style={[styles.section, styles.ownerPanel]}>
      <Text style={styles.heading}>Children not in a group</Text>
      <Text style={styles.body}>
        Choose one of your groups. After placement, create a new sign-in QR for the child's device.
      </Text>
      {children.map((child) => {
        const selectedGroupId =
          selectedGroups[child.id] ?? eligibleGroups[0]?.id ?? null;
        const busy = placeChild.isPending && placeChild.variables?.childId === child.id;
        return (
          <RNView
            key={child.id}
            testID={`groups-ungrouped-child-${child.id}`}
            style={styles.aggregateCard}
          >
            <Text style={styles.rosterName}>{child.display_name}</Text>
            <Text style={styles.rosterMeta}>child · age {child.age_band ?? "not set"}</Text>
            {eligibleGroups.length > 0 ? (
              <>
                <Text style={styles.sectionLabel}>Place in</Text>
                <RNView style={styles.tabRow}>
                  {eligibleGroups.map((group) => {
                    const selected = group.id === selectedGroupId;
                    return (
                      <Pressable
                        key={group.id}
                        testID={`groups-place-child-group-${child.id}-${group.id}`}
                        accessibilityRole="button"
                        accessibilityState={{ selected, disabled: placeChild.isPending }}
                        disabled={placeChild.isPending}
                        style={[styles.tab, selected && styles.tabActive]}
                        onPress={() =>
                          setSelectedGroups((current) => ({
                            ...current,
                            [child.id]: group.id,
                          }))
                        }
                      >
                        <Text style={[styles.tabText, selected && styles.tabTextActive]}>
                          {group.name}
                        </Text>
                      </Pressable>
                    );
                  })}
                </RNView>
                <Pressable
                  testID={`groups-place-child-${child.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Add ${child.display_name} to the selected group`}
                  accessibilityState={{ disabled: placeChild.isPending, busy }}
                  disabled={placeChild.isPending || selectedGroupId == null}
                  style={[
                    styles.button,
                    styles.buttonPrimary,
                    styles.inlineButton,
                    (placeChild.isPending || selectedGroupId == null) && styles.buttonDisabled,
                  ]}
                  onPress={() => {
                    if (selectedGroupId) {
                      placeChild.mutate({ childId: child.id, groupId: selectedGroupId });
                    }
                  }}
                >
                  <Text style={styles.buttonText}>{busy ? "Adding…" : "Add to group"}</Text>
                </Pressable>
              </>
            ) : (
              <Text style={styles.body}>
                Create a group or accept a parent invitation before placing this child.
              </Text>
            )}
          </RNView>
        );
      })}
    </RNView>
  );
}

function GroupPicker({
  groups,
  activeGroupId,
  canCreateGroup,
  onSelect,
}: {
  groups: Group[];
  activeGroupId: string | null;
  canCreateGroup: boolean;
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
        Alert.alert("Couldn't create group", safeAdultError(err, "Try again."));
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
        {canCreateGroup ? (
          <Pressable
            testID="classroom-new-group-button"
            nativeID="groups-new-group-button"
            accessibilityRole="button"
            accessibilityState={{ expanded: creating }}
            style={[styles.tab, styles.tabGhost]}
            onPress={() => setCreating((v) => !v)}
          >
            <Text style={styles.tabText}>+ New</Text>
          </Pressable>
        ) : null}
      </RNView>
      {canCreateGroup && creating && (
        <RNView style={styles.row}>
          <TextInput
            accessibilityLabel="Group name"
            autoComplete="off"
            style={[styles.input, { flex: 1 }]}
            value={draft}
            onChangeText={setDraft}
            placeholder="Group name (e.g. Saturday Nature Club)"
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
        Alert.alert("Couldn't create group", safeAdultError(err, "Try again."));
      }
    },
  });

  return (
    <RNView style={styles.section}>
      <Text style={styles.heading}>Create your first group</Text>
      <Text style={styles.body}>
        A group keeps children and their observations organized for a family
        or nature club.
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

function GroupDetail({
  group,
  sharedGroupsEnabled,
}: {
  group: Group;
  sharedGroupsEnabled: boolean;
}) {
  const currentUser = useAuthSession((state) =>
    state.status === "authenticated" ? state.user : null,
  );
  const userId = currentUser?.id ?? null;
  const queryClient = useQueryClient();
  const roster = useQuery({
    queryKey: ["group-members", userId ?? "anonymous", group.id],
    queryFn: () => listGroupMembers(group.id),
    enabled: userId != null,
  });
  const [showAdd, setShowAdd] = useState(false);
  const [handoff, setHandoff] = useState<CreateKidResponse | null>(null);
  const [reissueKidId, setReissueKidId] = useState<string | null>(null);
  const currentGroup = roster.data?.group ?? group;
  const reissue = useMutation({
    mutationKey: ["reissue-kid-handoff", userId ?? "anonymous", group.id],
    mutationFn: ({ kidUserId }: { kidUserId: string }) =>
      reissueKidHandoff(group.id, kidUserId),
    gcTime: 0,
    onSuccess: (response) => {
      if (!handoffIsUsable(response)) {
        reissue.reset();
        Alert.alert(
          "Couldn't create sign-in QR",
          "The one-time code was invalid or already expired. Try again.",
        );
        return;
      }
      setHandoff(response);
    },
    onError: (err) => {
      if (!(err instanceof ImperativeRequestSupersededError)) {
        Alert.alert(
          "Couldn't create sign-in QR",
          safeAdultError(err, "Try again. Your child's account has not changed."),
        );
      }
    },
    onSettled: () => setReissueKidId(null),
  });
  const resetReissue = reissue.reset;

  useEffect(() => {
    if (!handoff) return;
    const remainingMs = Date.parse(handoff.expires_at) - Date.now();
    if (!Number.isFinite(remainingMs) || remainingMs <= 0) {
      setHandoff(null);
      resetReissue();
      return;
    }
    const timer = setTimeout(() => {
      setHandoff(null);
      resetReissue();
    }, remainingMs);
    return () => clearTimeout(timer);
  }, [handoff, resetReissue]);

  function closeHandoff() {
    setHandoff(null);
    resetReissue();
  }

  return (
    <RNView style={styles.section}>
      <RNView style={styles.row}>
        <RNView style={{ flex: 1 }}>
          <Text style={styles.heading}>{currentGroup.name}</Text>
          <Text style={styles.help}>
            {currentGroup.adult_count} {currentGroup.adult_count === 1 ? "adult" : "adults"} ·{" "}
            {currentGroup.child_count} {currentGroup.child_count === 1 ? "child" : "children"}
          </Text>
        </RNView>
        {currentGroup.permissions.can_add_child ? (
          <Pressable
            testID="classroom-add-kid-button"
            nativeID="groups-add-child-button"
            accessibilityRole="button"
            accessibilityLabel={`Add a child to ${currentGroup.name}`}
            style={[styles.button, styles.buttonPrimary]}
            onPress={() => setShowAdd(true)}
          >
            <Text style={styles.buttonText}>Add child</Text>
          </Pressable>
        ) : null}
      </RNView>

      {roster.isPending ? (
        <ActivityIndicator style={{ marginTop: 16 }} />
      ) : roster.isError ? (
        <Text style={styles.body}>
          Couldn't load this group. Your children and invitations have not changed.
        </Text>
      ) : (
        <>
          {currentGroup.is_owner ? (
            <OwnerGroupControls
              group={currentGroup}
              adults={roster.data.adults}
              userId={userId ?? "anonymous"}
              sharedGroupsEnabled={sharedGroupsEnabled}
            />
          ) : null}

          <Text style={styles.sectionLabel}>Your children</Text>
          {roster.data.own_children.length === 0 ? (
            <Text style={styles.body}>
              You have no children in this group.
            </Text>
          ) : (
            roster.data.own_children.map((child) => (
              <OwnChildRow
              key={child.user_id}
              child={child}
              reissuePending={reissue.isPending}
              reissueBusy={reissue.isPending && reissueKidId === child.user_id}
              onReissue={() => {
                setReissueKidId(child.user_id);
                reissue.mutate({ kidUserId: child.user_id });
              }}
            />
            ))
          )}

          {roster.data.other_child_count > 0 ? (
            <RNView testID="groups-other-family-count" style={styles.aggregateCard}>
              <Text style={styles.rosterName}>Other families</Text>
              <Text style={styles.rosterMeta}>
                {roster.data.other_child_count}{" "}
                {roster.data.other_child_count === 1 ? "other child is" : "other children are"}{" "}
                in this group. Their names and activity stay private.
              </Text>
            </RNView>
          ) : null}
        </>
      )}

      {currentGroup.permissions.can_add_child ? (
        <AddKidModal
          visible={showAdd}
          groupId={group.id}
          onClose={() => setShowAdd(false)}
          onCreated={(resp) => {
            setShowAdd(false);
            void queryClient.invalidateQueries({
              queryKey: ["groups", userId ?? "anonymous"],
            });
            if (handoffIsUsable(resp)) {
              setHandoff(resp);
            } else {
              Alert.alert(
                "Couldn't create sign-in QR",
                "The one-time code was invalid or already expired. Try again.",
              );
            }
          }}
        />
      ) : null}

      <HandoffModal
        handoff={handoff}
        onClose={closeHandoff}
      />
    </RNView>
  );
}

function OwnChildRow({
  child,
  reissuePending,
  reissueBusy,
  onReissue,
}: {
  child: OwnChildRosterMember;
  reissuePending: boolean;
  reissueBusy: boolean;
  onReissue: () => void;
}) {
  const colorScheme = useColorScheme();
  return (
    <RNView
      testID={`classroom-roster-row-${child.user_id}`}
      nativeID={`groups-own-child-${child.user_id}`}
      style={[
        styles.rosterRow,
        colorScheme === "dark" ? styles.rosterRowDark : styles.rosterRowLight,
      ]}
    >
      <RNView style={styles.rosterIdentity}>
        <Text style={styles.rosterName}>{child.display_name}</Text>
        <Text style={styles.rosterMeta}>child · age {child.age_band ?? "not set"}</Text>
      </RNView>
      <RNView style={styles.rosterActions}>
        <Text style={styles.rosterMeta}>
          {child.observation_count} obs · {child.dex_count} dex
        </Text>
        <Pressable
          testID={`classroom-reissue-kid-${child.user_id}`}
          nativeID={`groups-reissue-child-${child.user_id}`}
          accessibilityRole="button"
          accessibilityLabel={`Create a new sign-in QR for ${child.display_name}`}
          accessibilityHint="Shows a one-time code that expires in 15 minutes."
          accessibilityState={{ disabled: reissuePending, busy: reissueBusy }}
          disabled={reissuePending}
          style={[
            styles.button,
            styles.buttonGhost,
            styles.rosterHandoffButton,
            reissuePending && styles.buttonDisabled,
          ]}
          onPress={onReissue}
        >
          <Text style={[styles.buttonText, styles.buttonGhostText]}>
            {reissueBusy ? "Creating…" : "New sign-in QR"}
          </Text>
        </Pressable>
      </RNView>
    </RNView>
  );
}

function OwnerGroupControls({
  group,
  adults,
  userId,
  sharedGroupsEnabled,
}: {
  group: Group;
  adults: Array<{
    removal_ref: string | null;
    display_name: string;
    is_owner: boolean;
    status: string;
  }>;
  userId: string;
  sharedGroupsEnabled: boolean;
}) {
  const queryClient = useQueryClient();
  const [renameDraft, setRenameDraft] = useState(group.name);
  const [editingName, setEditingName] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const invitationLinkRef = useRef<string | null>(null);
  const [invitationExpiresAt, setInvitationExpiresAt] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [createInvitePending, setCreateInvitePending] = useState(false);

  useEffect(() => {
    setRenameDraft(group.name);
  }, [group.name]);

  useEffect(() => {
    invitationLinkRef.current = null;
    setInvitationExpiresAt(null);
    setCopied(false);
    return () => {
      invitationLinkRef.current = null;
    };
  }, [group.id, userId]);

  const invalidateGroup = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["groups", userId] }),
      queryClient.invalidateQueries({ queryKey: ["group-members", userId, group.id] }),
    ]);
  };

  const rename = useMutation({
    mutationFn: (name: string) => updateGroup(group.id, name),
    onSuccess: async () => {
      setEditingName(false);
      await invalidateGroup();
    },
    onError: (error) => Alert.alert("Couldn't rename group", safeAdultError(error, "Try again.")),
  });
  const archive = useMutation({
    mutationFn: () => archiveGroup(group.id),
    onSuccess: invalidateGroup,
    onError: (error) => Alert.alert("Couldn't archive group", safeAdultError(error, "Try again.")),
  });
  const removeAdult = useMutation({
    mutationFn: (removalRef: string) => removeAdultMember(group.id, removalRef),
    onSuccess: async () => {
      setConfirmRemoveId(null);
      await invalidateGroup();
    },
    onError: (error) => Alert.alert("Couldn't remove parent", safeAdultError(error, "Try again.")),
  });
  const invitations = useQuery({
    queryKey: ["group-adult-invitations", userId, group.id],
    queryFn: () => listAdultInvitations(group.id),
    enabled: sharedGroupsEnabled && group.permissions.can_manage_invitations,
  });
  async function issueInvitation() {
    setCreateInvitePending(true);
    try {
      const response = await createAdultInvitation(group.id);
      if (!validateInvitationUrl(response.invite_url)) {
        Alert.alert("Couldn't create invitation", "The invitation response was invalid. Try again.");
        return;
      }
      invitationLinkRef.current = response.invite_url;
      setInvitationExpiresAt(response.expires_at);
      setCopied(false);
      void queryClient.invalidateQueries({
        queryKey: ["group-adult-invitations", userId, group.id],
      });
    } catch (error) {
      if (!(error instanceof ImperativeRequestSupersededError)) {
        Alert.alert("Couldn't create invitation", safeAdultError(error, "Try again."));
      }
    } finally {
      setCreateInvitePending(false);
    }
  }
  const revokeInvite = useMutation({
    mutationFn: (inviteId: string) => revokeAdultInvitation(group.id, inviteId),
    onSuccess: () => queryClient.invalidateQueries({
      queryKey: ["group-adult-invitations", userId, group.id],
    }),
    onError: (error) => Alert.alert("Couldn't revoke invitation", safeAdultError(error, "Try again.")),
  });

  function closeInvitation() {
    invitationLinkRef.current = null;
    setInvitationExpiresAt(null);
    setCopied(false);
  }

  return (
    <RNView testID="groups-owner-controls" style={styles.ownerPanel}>
      <Text style={styles.sectionLabel}>Group settings</Text>
      {group.permissions.can_rename ? (
        editingName ? (
          <RNView style={styles.row}>
            <TextInput
              testID="groups-rename-input"
              accessibilityLabel="New group name"
              style={[styles.input, { flex: 1 }]}
              value={renameDraft}
              onChangeText={setRenameDraft}
            />
            <Pressable
              testID="groups-rename-save"
              accessibilityRole="button"
              disabled={rename.isPending || !renameDraft.trim()}
              style={[styles.button, styles.buttonPrimary, (rename.isPending || !renameDraft.trim()) && styles.buttonDisabled]}
              onPress={() => rename.mutate(renameDraft.trim())}
            >
              <Text style={styles.buttonText}>{rename.isPending ? "Saving…" : "Save"}</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              style={[styles.button, styles.buttonGhost]}
              onPress={() => {
                setRenameDraft(group.name);
                setEditingName(false);
              }}
            >
              <Text style={styles.buttonGhostText}>Cancel</Text>
            </Pressable>
          </RNView>
        ) : (
          <Pressable
            testID="groups-rename-button"
            accessibilityRole="button"
            style={[styles.button, styles.buttonGhost, styles.inlineButton]}
            onPress={() => setEditingName(true)}
          >
            <Text style={styles.buttonGhostText}>Rename group</Text>
          </Pressable>
        )
      ) : null}

      {sharedGroupsEnabled && group.permissions.can_manage_invitations ? (
        <RNView style={styles.subsection}>
          <RNView style={styles.row}>
            <RNView style={{ flex: 1 }}>
              <Text style={styles.rosterName}>Parent invitations</Text>
              <Text style={styles.rosterMeta}>Links work once and expire after 72 hours.</Text>
            </RNView>
            {group.permissions.can_invite_parents ? (
              <Pressable
                testID="groups-create-invitation"
                accessibilityRole="button"
                accessibilityState={{ disabled: createInvitePending, busy: createInvitePending }}
                disabled={createInvitePending}
                style={[styles.button, styles.buttonPrimary, createInvitePending && styles.buttonDisabled]}
                onPress={() => void issueInvitation()}
              >
                <Text style={styles.buttonText}>{createInvitePending ? "Creating…" : "Invite parent"}</Text>
              </Pressable>
            ) : null}
          </RNView>
          {invitations.isPending ? <ActivityIndicator /> : null}
          {invitations.isError ? (
            <Text style={styles.rosterMeta}>
              Couldn't load invitations. No invitation was changed.
            </Text>
          ) : null}
          {invitations.data?.items.map((invite) => (
            <InvitationMetadataRow
              key={invite.id}
              invite={invite}
              revoking={revokeInvite.isPending}
              onRevoke={() => revokeInvite.mutate(invite.id)}
            />
          ))}
        </RNView>
      ) : null}

      <RNView style={styles.subsection}>
        <Text style={styles.rosterName}>Parents</Text>
        {adults.map((adult) => (
          <RNView key={adult.removal_ref ?? `owner-${adult.display_name}`} style={styles.rosterRow}>
            <RNView style={styles.rosterIdentity}>
              <Text style={styles.rosterName}>{adult.display_name}</Text>
              <Text style={styles.rosterMeta}>
                {adult.is_owner
                  ? "group owner"
                  : adult.status === "left"
                    ? "removed parent"
                    : "active parent"}
              </Text>
            </RNView>
            {group.permissions.can_remove_adults &&
            adult.status === "active" &&
            !adult.is_owner &&
            adult.removal_ref ? (
              confirmRemoveId === adult.removal_ref ? (
                <RNView style={styles.row}>
                  <Pressable
                    accessibilityRole="button"
                    style={[styles.button, styles.buttonDanger]}
                    onPress={() => removeAdult.mutate(adult.removal_ref!)}
                  >
                    <Text style={styles.buttonText}>Confirm remove</Text>
                  </Pressable>
                  <Pressable accessibilityRole="button" style={[styles.button, styles.buttonGhost]} onPress={() => setConfirmRemoveId(null)}>
                    <Text style={styles.buttonGhostText}>Cancel</Text>
                  </Pressable>
                </RNView>
              ) : (
                <Pressable
                  testID={`groups-remove-adult-${adult.removal_ref}`}
                  accessibilityRole="button"
                  style={[styles.button, styles.buttonGhost]}
                  onPress={() => setConfirmRemoveId(adult.removal_ref)}
                >
                  <Text style={styles.buttonGhostText}>Remove</Text>
                </Pressable>
              )
            ) : null}
          </RNView>
        ))}
      </RNView>

      {group.permissions.can_archive ? (
        confirmArchive ? (
          <RNView style={styles.row}>
            <Pressable
              testID="groups-archive-confirm"
              accessibilityRole="button"
              disabled={archive.isPending}
              style={[styles.button, styles.buttonDanger, archive.isPending && styles.buttonDisabled]}
              onPress={() => archive.mutate()}
            >
              <Text style={styles.buttonText}>{archive.isPending ? "Archiving…" : "Confirm archive"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={[styles.button, styles.buttonGhost]} onPress={() => setConfirmArchive(false)}>
              <Text style={styles.buttonGhostText}>Cancel</Text>
            </Pressable>
          </RNView>
        ) : (
          <Pressable
            testID="groups-archive-button"
            accessibilityRole="button"
            style={[styles.button, styles.buttonGhost, styles.inlineButton]}
            onPress={() => setConfirmArchive(true)}
          >
            <Text style={styles.buttonGhostText}>Archive group</Text>
          </Pressable>
        )
      ) : null}

      <Modal visible={invitationExpiresAt != null} transparent animationType="fade" onRequestClose={closeInvitation}>
        <RNView style={styles.modalScrim}>
          <View accessibilityViewIsModal style={styles.modalCard}>
            <Text style={styles.heading}>Invitation ready</Text>
            <Text style={styles.help}>
              Copy it now. For privacy, this one-time link is not shown again. It expires {invitationExpiresAt ? formatInvitationExpiry(invitationExpiresAt) : "soon"}.
            </Text>
            <Pressable
              testID="groups-copy-invitation"
              accessibilityRole="button"
              style={[styles.button, styles.buttonPrimary]}
              onPress={() => {
                const invitationLink = invitationLinkRef.current;
                if (!invitationLink) return;
                void copyInvitationUrl(invitationLink).then(
                  () => setCopied(true),
                  () => Alert.alert("Couldn't copy invitation", "Clipboard access is unavailable. Try again in a supported browser."),
                );
              }}
            >
              <Text style={styles.buttonText}>{copied ? "Copied" : "Copy invitation link"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={[styles.button, styles.buttonGhost]} onPress={closeInvitation}>
              <Text style={styles.buttonGhostText}>Done</Text>
            </Pressable>
          </View>
        </RNView>
      </Modal>
    </RNView>
  );
}

function InvitationMetadataRow({
  invite,
  revoking,
  onRevoke,
}: {
  invite: AdultInvitation;
  revoking: boolean;
  onRevoke: () => void;
}) {
  return (
    <RNView testID={`groups-invitation-${invite.id}`} style={styles.rosterRow}>
      <RNView style={styles.rosterIdentity}>
        <Text style={styles.rosterName}>{invite.state === "pending" ? "Pending invitation" : `${invite.state[0].toUpperCase()}${invite.state.slice(1)} invitation`}</Text>
        <Text style={styles.rosterMeta}>Created {new Date(invite.created_at).toLocaleDateString()} · expires {new Date(invite.expires_at).toLocaleDateString()}</Text>
      </RNView>
      {invite.state === "pending" ? (
        <Pressable
          testID={`groups-revoke-invitation-${invite.id}`}
          accessibilityRole="button"
          disabled={revoking}
          style={[styles.button, styles.buttonGhost, revoking && styles.buttonDisabled]}
          onPress={onRevoke}
        >
          <Text style={styles.buttonGhostText}>Revoke</Text>
        </Pressable>
      ) : null}
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
    mutationKey: ["create-kid", ownerUserId ?? "anonymous", groupId],
    mutationFn: () => createKid(groupId, name.trim(), ageBand),
    gcTime: 0,
    onSuccess: (resp) => {
      void queryClient.invalidateQueries({
        queryKey: ["group-members", ownerUserId ?? "anonymous", groupId],
      });
      setName("");
      setAgeBand("9-10");
      onCreated(resp);
      create.reset();
    },
    onError: (err) => {
      if (!(err instanceof ImperativeRequestSupersededError)) {
        Alert.alert("Couldn't create child", safeAdultError(err, "Try again. No child account was created."));
      }
    },
  });

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <RNView style={styles.modalScrim}>
        <View style={styles.modalCard}>
        <Text style={styles.heading}>Add a child</Text>
        <Text style={styles.help}>
          Creates the account and shows a handoff QR for the child's device.
          </Text>

          <Text style={styles.sectionLabel}>Display name</Text>
          <TextInput
            testID="classroom-kid-display-name"
            accessibilityLabel="Child display name"
            autoComplete="off"
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="First name or nickname"
            placeholderTextColor="#6b7280"
            autoCapitalize="words"
            autoCorrect={false}
            maxLength={80}
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
  const expiryLabel = handoff ? formatHandoffExpiry(handoff.expires_at) : "";
  return (
    <Modal
      testID="classroom-handoff-modal"
      visible={handoff != null}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <RNView style={styles.modalScrim}>
        <View accessibilityViewIsModal style={styles.modalCard}>
          <Text accessibilityRole="header" style={styles.heading}>
            Hand off to {handoff?.display_name}
          </Text>
          <Text style={styles.help}>
            Open The Hinterland Guide on the child's device and scan this
            one-time code. It expires at {expiryLabel}. This does not sign the
            kid out of another device.
          </Text>
          <RNView
            testID="classroom-handoff-qr"
            accessible
            accessibilityRole="image"
            accessibilityLabel={`One-time sign-in QR for ${handoff?.display_name ?? "child"}`}
            style={styles.qrWrap}
          >
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
            testID="classroom-handoff-done-button"
            accessibilityRole="button"
            accessibilityLabel="Close sign-in QR"
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

function handoffIsUsable(handoff: unknown): handoff is CreateKidResponse {
  if (handoff == null || typeof handoff !== "object") return false;
  const candidate = handoff as Partial<Record<keyof CreateKidResponse, unknown>>;
  if (
    typeof candidate.id !== "string" ||
    candidate.id.length === 0 ||
    typeof candidate.display_name !== "string" ||
    candidate.display_name.length === 0 ||
    typeof candidate.age_band !== "string" ||
    typeof candidate.handoff_token !== "string" ||
    candidate.handoff_token.trim().length === 0 ||
    typeof candidate.expires_at !== "string"
  ) {
    return false;
  }
  const expiresAt = Date.parse(candidate.expires_at);
  return (
    Number.isFinite(expiresAt) &&
    expiresAt > Date.now()
  );
}

function formatHandoffExpiry(expiresAt: string): string {
  const parsed = new Date(expiresAt);
  if (!Number.isFinite(parsed.getTime())) return "soon";
  return parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatInvitationExpiry(expiresAt: string): string {
  const parsed = new Date(expiresAt);
  if (!Number.isFinite(parsed.getTime())) return "soon";
  return parsed.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function safeAdultError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const supportCode = err.body?.error.request_id;
    if (err.status === 401) return "Your sign-in expired. Sign in again.";
    if (err.status === 403 || err.status === 404) return "This group action is unavailable.";
    if (err.status === 409) {
      return `This action needs adult attention.${supportCode ? ` Support code: ${supportCode}` : ""}`;
    }
    return `${fallback}${supportCode ? ` Support code: ${supportCode}` : ""}`;
  }
  return fallback;
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
  buttonDanger: { backgroundColor: "#b42318" },
  buttonGhost: {
    borderColor: "#888",
    borderWidth: StyleSheet.hairlineWidth,
    backgroundColor: "#fff",
  },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { fontSize: 14, color: "#fff" },
  buttonGhostText: { color: "#1f2937" },
  inlineButton: { alignSelf: "flex-start" },
  ownerPanel: {
    marginTop: 12,
    padding: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "rgba(31,41,55,0.2)",
    borderRadius: 8,
  },
  subsection: { marginTop: 12 },
  aggregateCard: {
    marginTop: 12,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgba(47,111,235,0.08)",
  },
  rosterRow: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 8,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  rosterIdentity: { flex: 1, minWidth: 160 },
  rosterActions: { alignItems: "flex-end" },
  rosterHandoffButton: { marginTop: 4, paddingHorizontal: 10 },
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
