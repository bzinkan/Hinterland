import { Alert, Modal, StyleSheet, Text, TextInput } from "react-native";
import renderer, { act, type ReactTestInstance } from "react-test-renderer";

import GroupsScreen from "@/app/groups";
import { useAuthSession } from "@/src/auth/session";

const mockUseQuery = jest.fn();
const mockUseMutation = jest.fn();
const mockMutate = jest.fn();
const mockReset = jest.fn();
const mockInvalidateQueries = jest.fn();
let mockColorScheme: "light" | "dark" = "light";
let mockSharedGroupsEnabled = true;

jest.mock("expo-router", () => ({
  router: { back: jest.fn(), replace: jest.fn() },
  Stack: { Screen: () => null },
}));

const mockedRouter = jest.requireMock("expo-router").router as {
  replace: jest.Mock;
};

jest.mock("react-native-qrcode-svg", () => "QRCode");

jest.mock("@/components/useColorScheme", () => ({
  useColorScheme: () => mockColorScheme,
}));

jest.mock("@/src/groups/useGroupCapabilities", () => ({
  useGroupCapabilities: () => ({
    sharedGroupsEnabled: mockSharedGroupsEnabled,
    query: { isPending: false, isSuccess: true, isError: false },
  }),
}));

jest.mock("@tanstack/react-query", () => ({
  useQuery: (options: unknown) => mockUseQuery(options),
  useMutation: (options: unknown) => mockUseMutation(options),
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

jest.mock("@/src/api/groups", () => ({
  archiveGroup: jest.fn(),
  createAdultInvitation: jest.fn(),
  createGroup: jest.fn(),
  createKid: jest.fn(),
  listAdultInvitations: jest.fn(),
  listGroupMembers: jest.fn(),
  listGroups: jest.fn(),
  listOwnedChildren: jest.fn(),
  placeOwnedChildInGroup: jest.fn(),
  removeAdultMember: jest.fn(),
  reissueKidHandoff: jest.fn(),
  revokeAdultInvitation: jest.fn(),
  updateGroup: jest.fn(),
}));

jest.mock("@/src/groups/invitationToken", () => ({
  copyInvitationUrl: jest.fn(),
  validateInvitationUrl: jest.fn(() => true),
}));

const mockedCreateAdultInvitation = jest.requireMock("@/src/api/groups")
  .createAdultInvitation as jest.Mock;

const ownerGroup = {
  id: "group-1",
  name: "First Group",
  is_owner: true,
  adult_count: 2,
  child_count: 2,
  own_children_count: 1,
  permissions: {
    can_rename: true,
    can_archive: true,
    can_invite_parents: true,
    can_manage_invitations: true,
    can_remove_adults: true,
    can_add_child: true,
  },
};

const secondOwnerGroup = { ...ownerGroup, id: "group-2", name: "Second Group" };
let ownerGroups = [ownerGroup, secondOwnerGroup];
let ownedChildrenByUser: Record<
  string,
  Array<{
    id: string;
    display_name: string;
    age_band: string | null;
    active_group_id: string | null;
  }>
> = {};
let rosterChildrenByUserGroup: Record<
  string,
  Array<{
    user_id: string;
    display_name: string;
    age_band: string | null;
    status: string;
    observation_count: number;
    dex_count: number;
    rarest_tier: string | null;
    last_observed_at: string | null;
  }>
> = {};

const joinedGroup = {
  ...ownerGroup,
  is_owner: false,
  permissions: {
    can_rename: false,
    can_archive: false,
    can_invite_parents: false,
    can_manage_invitations: false,
    can_remove_adults: false,
    can_add_child: true,
  },
};

function textChild(control: ReactTestInstance, value: string): ReactTestInstance {
  return control.findAllByType(Text).find((node) => node.props.children === value)!;
}

function handoffModal(tree: renderer.ReactTestRenderer): ReactTestInstance {
  return tree.root
    .findAllByType(Modal)
    .find((node) => node.props.testID === "classroom-handoff-modal")!;
}

type MutationOptions = {
  mutationKey?: readonly unknown[];
  gcTime?: number;
  onSuccess?: (value: unknown, variables?: unknown) => void | Promise<void>;
};

function mutationOptions(key: string): MutationOptions {
  return mockUseMutation.mock.calls
    .map(([options]) => options as MutationOptions)
    .filter((options) => options.mutationKey?.[0] === key)
    .at(-1)!;
}

describe("GroupsScreen presentation contract", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    mockColorScheme = "light";
    mockSharedGroupsEnabled = true;
    ownerGroups = [ownerGroup, secondOwnerGroup];
    ownedChildrenByUser = {};
    rosterChildrenByUserGroup = {};
    mockUseMutation.mockImplementation(() => ({
      isPending: false,
      mutate: mockMutate,
      reset: mockReset,
    }));
    useAuthSession.getState().setAuthenticated({
      id: "parent-1",
      entra_oid: "entra-1",
      role: "parent",
      display_name: "Test Parent",
    });
    mockUseQuery.mockImplementation(
      (options: { queryKey: [string, ...unknown[]] }) => {
        if (options.queryKey[0] === "groups") {
          const joined = options.queryKey[1] === "parent-2";
          const teacher = options.queryKey[1] === "teacher-1";
          return {
            isPending: false,
            isError: false,
            data: {
              items: teacher ? [] : joined ? [joinedGroup] : ownerGroups,
            },
          };
        }
        if (options.queryKey[0] === "group-adult-invitations") {
          return {
            isPending: false,
            isError: false,
            data: { items: [] },
          };
        }
        if (options.queryKey[0] === "owned-children") {
          const userId = String(options.queryKey[1]);
          return {
            isPending: false,
            isError: false,
            data: { items: ownedChildrenByUser[userId] ?? [] },
          };
        }
        const userId = String(options.queryKey[1]);
        const groupId = String(options.queryKey[2]);
        const joined = userId === "parent-2";
        return {
          isPending: false,
          isError: false,
          data: {
            group: joined ? joinedGroup : ownerGroup,
            adults: joined
              ? []
              : [
              {
                removal_ref: null,
                display_name: "Test Parent",
                is_owner: true,
                status: "active",
              },
              {
                removal_ref: "opaque-remove-parent-2",
                display_name: "Other Parent",
                is_owner: false,
                status: "active",
              },
            ],
            own_children: rosterChildrenByUserGroup[`${userId}:${groupId}`] ?? (joined
              ? [
                  {
                    user_id: "kid-2",
                    display_name: "Other Parent Kid",
                    age_band: "9-10",
                    status: "active",
                    observation_count: 0,
                    dex_count: 0,
                    rarest_tier: null,
                    last_observed_at: null,
                  },
                ]
              : [
                  {
                    user_id: "kid-1",
                    display_name: "Test Kid",
                    age_band: "9-10",
                    status: "active",
                    observation_count: 0,
                    dex_count: 0,
                    rarest_tier: null,
                    last_observed_at: null,
                  },
                ]),
            other_child_count: 1,
          },
        };
      },
    );
  });

  afterEach(() => {
    act(() => jest.runOnlyPendingTimers());
    jest.useRealTimers();
  });

  it("shows an explicit sign-in action instead of a disabled-query spinner", () => {
    useAuthSession.getState().setAnonymous();
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    expect(JSON.stringify(tree.toJSON())).toContain("Sign in to manage groups");
    expect(
      (mockUseQuery.mock.calls[0][0] as { enabled: boolean }).enabled,
    ).toBe(false);
    act(() => {
      tree.root.findByProps({ testID: "groups-sign-in-button" }).props.onPress();
    });
    expect(mockedRouter.replace).toHaveBeenCalledWith("/sign-in");

    act(() => tree.unmount());
  });

  it("keeps the loading state bounded to canonical identity resolution", () => {
    useAuthSession.getState().setInitializing();
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    expect(JSON.stringify(tree.toJSON())).not.toContain("Sign in to manage groups");
    expect(
      (mockUseQuery.mock.calls[0][0] as { enabled: boolean }).enabled,
    ).toBe(false);

    act(() => tree.unmount());
  });

  it("does not expose the adult Groups surface to a kid session", () => {
    useAuthSession.getState().setAuthenticated({
      id: "kid-1",
      entra_oid: null,
      role: "kid",
      display_name: "Test Kid",
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Groups are managed by adults");
    expect(rendered).not.toContain("Test Parent");
    expect(
      (mockUseQuery.mock.calls[0][0] as { enabled: boolean }).enabled,
    ).toBe(false);

    act(() => tree.unmount());
  });

  it("shows owner administration using only opaque removal references", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    expect(tree.root.findAllByProps({ testID: "groups-owner-controls" }).length).toBeGreaterThan(0);
    expect(tree.root.findAllByProps({ testID: "groups-create-invitation" }).length).toBeGreaterThan(0);
    expect(tree.root.findAllByProps({ testID: "groups-rename-button" }).length).toBeGreaterThan(0);
    expect(tree.root.findAllByProps({ testID: "groups-archive-button" }).length).toBeGreaterThan(0);
    expect(
      tree.root.findAllByProps({ testID: "groups-remove-adult-opaque-remove-parent-2" }),
    ).not.toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain("membership_id");
    act(() => tree.unmount());
  });

  it("keeps revocation controls available during a sharing rollout rollback", () => {
    ownerGroup.permissions.can_invite_parents = false;
    ownerGroup.permissions.can_manage_invitations = true;
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    const invitationQuery = mockUseQuery.mock.calls
      .map(([options]) => options as { queryKey: [string, ...unknown[]]; enabled?: boolean })
      .find((options) => options.queryKey[0] === "group-adult-invitations");
    expect(invitationQuery?.enabled).toBe(true);
    expect(JSON.stringify(tree.toJSON())).toContain("Parent invitations");
    expect(tree.root.findAllByProps({ testID: "groups-create-invitation" })).toHaveLength(0);
    expect(
      tree.root.findAllByProps({ testID: "groups-remove-adult-opaque-remove-parent-2" }),
    ).not.toHaveLength(0);

    act(() => tree.unmount());
    ownerGroup.permissions.can_invite_parents = true;
  });

  it("hides invitation creation and retrieval when the release capability is unavailable", () => {
    mockSharedGroupsEnabled = false;
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    const invitationQuery = mockUseQuery.mock.calls
      .map(([options]) => options as { queryKey: [string, ...unknown[]]; enabled?: boolean })
      .find((options) => options.queryKey[0] === "group-adult-invitations");
    expect(invitationQuery?.enabled).toBe(false);
    expect(tree.root.findAllByProps({ testID: "groups-create-invitation" })).toHaveLength(0);
    expect(JSON.stringify(tree.toJSON())).not.toContain("Parent invitations");
    expect(tree.root.findAllByProps({ testID: "groups-rename-button" }).length).toBeGreaterThan(0);

    act(() => tree.unmount());
  });

  it("shows a joined parent only their own child plus an aggregate peer count", () => {
    useAuthSession.getState().setAuthenticated({
      id: "parent-2",
      entra_oid: "entra-2",
      role: "parent",
      display_name: "Other Parent",
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Other Parent Kid");
    expect(rendered).toContain("other child is");
    expect(rendered).not.toContain("Test Kid");
    expect(tree.root.findAllByProps({ testID: "groups-owner-controls" })).toHaveLength(0);
    expect(tree.root.findAllByProps({ testID: "groups-create-invitation" })).toHaveLength(0);
    expect(
      tree.root.findAllByProps({ testID: "classroom-add-kid-button" }).length,
    ).toBeGreaterThan(0);
    act(() => tree.unmount());
  });

  it("shows only ungrouped owned children and places one in the selected eligible group", async () => {
    ownedChildrenByUser["parent-1"] = [
      {
        id: "child-ungrouped",
        display_name: "Finch",
        age_band: "9-10",
        active_group_id: null,
      },
      {
        id: "child-grouped",
        display_name: "Already Grouped",
        age_band: "11-12",
        active_group_id: "group-1",
      },
    ];
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Children not in a group");
    expect(rendered).toContain("Finch");
    expect(rendered).not.toContain("Already Grouped");

    act(() => {
      tree.root
        .findByProps({ testID: "groups-place-child-group-child-ungrouped-group-2" })
        .props.onPress();
    });
    act(() => {
      tree.root.findByProps({ testID: "groups-place-child-child-ungrouped" }).props.onPress();
    });
    expect(mockMutate).toHaveBeenLastCalledWith({
      childId: "child-ungrouped",
      groupId: "group-2",
    });
    expect(mutationOptions("place-owned-child").gcTime).toBe(0);

    await act(async () => {
      await mutationOptions("place-owned-child").onSuccess?.(undefined, {
        childId: "child-ungrouped",
        groupId: "group-2",
      });
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["owned-children", "parent-1"],
    });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["group-members", "parent-1", "group-2"],
    });
    expect(
      tree.root.findByProps({ testID: "classroom-group-tab-group-2" }).props
        .accessibilityState.selected,
    ).toBe(true);

    ownedChildrenByUser["parent-1"][0].active_group_id = "group-2";
    rosterChildrenByUserGroup["parent-1:group-2"] = [
      {
        user_id: "child-ungrouped",
        display_name: "Finch",
        age_band: "9-10",
        status: "active",
        observation_count: 0,
        dex_count: 0,
        rarest_tier: null,
        last_observed_at: null,
      },
    ];
    act(() => {
      tree.update(<GroupsScreen />);
    });
    expect(tree.root.findAllByProps({ testID: "groups-ungrouped-children" })).toHaveLength(0);
    expect(
      tree.root.findAllByProps({ testID: "classroom-reissue-kid-child-ungrouped" }).length,
    ).toBeGreaterThan(0);
    act(() => tree.unmount());
  });

  it("drops ungrouped-child presentation and target selection on account switch", () => {
    ownedChildrenByUser["parent-1"] = [
      {
        id: "child-a",
        display_name: "Finch",
        age_band: "9-10",
        active_group_id: null,
      },
    ];
    ownedChildrenByUser["parent-2"] = [
      {
        id: "child-b",
        display_name: "Wren",
        age_band: "11-12",
        active_group_id: null,
      },
    ];
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    act(() => {
      tree.root
        .findByProps({ testID: "groups-place-child-group-child-a-group-2" })
        .props.onPress();
    });

    act(() => {
      useAuthSession.getState().setAuthenticated({
        id: "parent-2",
        entra_oid: "entra-2",
        role: "parent",
        display_name: "Other Parent",
      });
    });

    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Wren");
    expect(rendered).not.toContain("Finch");
    expect(tree.root.findAllByProps({ testID: "groups-place-child-group-child-b-group-2" })).toHaveLength(0);
    expect(
      tree.root.findByProps({ testID: "groups-place-child-group-child-b-group-1" }).props
        .accessibilityState.selected,
    ).toBe(true);
    act(() => tree.unmount());
  });

  it("does not grant compatibility teacher accounts group creation", () => {
    useAuthSession.getState().setAuthenticated({
      id: "teacher-1",
      entra_oid: "entra-teacher",
      role: "teacher",
      display_name: "Teacher",
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });

    expect(JSON.stringify(tree.toJSON())).toContain("compatibility account cannot create groups");
    expect(tree.root.findAllByProps({ testID: "classroom-new-group-button" })).toHaveLength(0);
    expect(tree.root.findAllByProps({ testID: "classroom-create-first-group-button" })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it("clears a selected group when it is archived or disappears", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    act(() => {
      tree.root.findByProps({ testID: "classroom-group-tab-group-2" }).props.onPress();
    });
    expect(JSON.stringify(tree.toJSON())).toContain("Second Group");

    ownerGroups = [ownerGroup];
    act(() => {
      tree.update(<GroupsScreen />);
    });
    expect(JSON.stringify(tree.toJSON())).toContain("First Group");
    expect(tree.root.findAllByProps({ testID: "classroom-group-tab-group-2" })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it("keeps inactive controls and inputs readable on the light parent surface", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
      jest.runOnlyPendingTimers();
    });

    const activeTab = tree.root.findByProps({
      testID: "classroom-group-tab-group-1",
    });
    const inactiveTab = tree.root.findByProps({
      testID: "classroom-group-tab-group-2",
    });
    expect(activeTab.props.accessibilityRole).toBe("button");
    expect(activeTab.props.accessibilityState).toEqual({ selected: true });
    expect(StyleSheet.flatten(activeTab.props.style)).toMatchObject({
      minHeight: 44,
      minWidth: 44,
    });
    expect(inactiveTab.props.accessibilityState).toEqual({ selected: false });
    expect(StyleSheet.flatten(textChild(activeTab, "First Group").props.style)).toMatchObject({
      color: "#fff",
      opacity: 1,
    });
    expect(
      StyleSheet.flatten(textChild(inactiveTab, "Second Group").props.style),
    ).toMatchObject({ color: "#1f2937" });

    act(() => {
      tree.root.findByProps({ testID: "classroom-new-group-button" }).props.onPress();
    });
    const createGroup = tree.root.findByProps({
      testID: "classroom-create-group-button",
    });
    expect(createGroup.props.disabled).toBe(true);
    expect(createGroup.props.accessibilityState).toMatchObject({ disabled: true });
    expect(StyleSheet.flatten(createGroup.props.style)).toMatchObject({ opacity: 0.4 });

    act(() => {
      tree.root.findByProps({ testID: "classroom-add-kid-button" }).props.onPress();
    });
    const input = tree.root.findByProps({ testID: "classroom-kid-display-name" });
    expect(input.type).toBe(TextInput);
    expect(input.props.accessibilityLabel).toBe("Child display name");
    expect(input.props.placeholderTextColor).toBe("#6b7280");
    expect(input.props.maxLength).toBe(80);
    expect(StyleSheet.flatten(input.props.style)).toMatchObject({
      color: "#1f2937",
      backgroundColor: "#fff",
    });

    const selectedAge = tree.root.findByProps({ testID: "classroom-age-band-9-10" });
    const unselectedAge = tree.root.findByProps({ testID: "classroom-age-band-11-12" });
    expect(selectedAge.props.accessibilityRole).toBe("radio");
    expect(selectedAge.props.accessibilityState).toEqual({ checked: true });
    expect(unselectedAge.props.accessibilityState).toEqual({ checked: false });
    expect(StyleSheet.flatten(textChild(selectedAge, "9-10").props.style)).toMatchObject({
      color: "#fff",
    });
    expect(StyleSheet.flatten(textChild(unselectedAge, "11-12").props.style)).toMatchObject({
      color: "#1f2937",
    });

    const cancel = tree.root
      .findAllByType(Text)
      .find((node) => node.props.children === "Cancel")!;
    expect(StyleSheet.flatten(cancel.props.style)).toMatchObject({
      color: "#1f2937",
    });

    act(() => tree.unmount());
  });

  it("uses a divider that remains visible in native dark mode", () => {
    mockColorScheme = "dark";
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
      jest.runOnlyPendingTimers();
    });

    const roster = tree.root.findByProps({ testID: "classroom-roster-row-kid-1" });
    expect(StyleSheet.flatten(roster.props.style)).toMatchObject({
      borderBottomColor: "rgba(255,255,255,0.1)",
    });

    act(() => tree.unmount());
  });

  it("offers an accessible owner-only reissue action on kid rows", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
      jest.runOnlyPendingTimers();
    });

    const action = tree.root.findByProps({
      testID: "classroom-reissue-kid-kid-1",
    });
    expect(action.props.accessibilityRole).toBe("button");
    expect(action.props.accessibilityLabel).toBe(
      "Create a new sign-in QR for Test Kid",
    );
    expect(action.props.accessibilityHint).toContain("expires in 15 minutes");
    expect(action.props.accessibilityState).toEqual({
      disabled: false,
      busy: false,
    });
    expect(StyleSheet.flatten(action.props.style)).toMatchObject({
      minHeight: 44,
      minWidth: 44,
    });
    expect(
      tree.root.findAllByProps({ testID: "classroom-reissue-kid-parent-1" }),
    ).toHaveLength(0);

    act(() => action.props.onPress());
    expect(mockMutate).toHaveBeenCalledWith({ kidUserId: "kid-1" });

    act(() => tree.unmount());
  });

  it("shows one no-store handoff result and clears it on Done", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    const reissueOptions = mutationOptions("reissue-kid-handoff");
    expect(reissueOptions.gcTime).toBe(0);

    act(() => {
      reissueOptions.onSuccess?.({
        id: "kid-1",
        display_name: "Test Kid",
        age_band: "9-10",
        handoff_token: "synthetic-one-time-token",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
    });

    expect(handoffModal(tree).props.visible).toBe(true);
    const qr = tree.root.findByProps({ testID: "classroom-handoff-qr" });
    expect(qr.props.accessibilityRole).toBe("image");
    expect(qr.props.accessibilityLabel).toBe("One-time sign-in QR for Test Kid");

    act(() =>
      tree.root
        .findByProps({ testID: "classroom-handoff-done-button" })
        .props.onPress(),
    );
    expect(handoffModal(tree).props.visible).toBe(false);
    expect(mockReset).toHaveBeenCalled();

    act(() => tree.unmount());
  });

  it.each([
    ["missing token", { expires_at: new Date(Date.now() + 60_000).toISOString() }],
    [
      "null token",
      { handoff_token: null, expires_at: new Date(Date.now() + 60_000).toISOString() },
    ],
    [
      "non-string token",
      { handoff_token: 7, expires_at: new Date(Date.now() + 60_000).toISOString() },
    ],
    ["missing expiry", { handoff_token: "synthetic-one-time-token" }],
    ["null expiry", { handoff_token: "synthetic-one-time-token", expires_at: null }],
    ["non-string expiry", { handoff_token: "synthetic-one-time-token", expires_at: 7 }],
    ["invalid expiry", { handoff_token: "synthetic-one-time-token", expires_at: "not-a-date" }],
  ])("fails closed for a malformed handoff response: %s", (_label, malformed) => {
    const alert = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    const reissueOptions = mutationOptions("reissue-kid-handoff");

    act(() => {
      reissueOptions.onSuccess?.({
        id: "kid-1",
        display_name: "Test Kid",
        age_band: "9-10",
        ...malformed,
      });
    });

    expect(alert).toHaveBeenCalledWith(
      "Couldn't create sign-in QR",
      "The one-time code was invalid or already expired. Try again.",
    );
    expect(handoffModal(tree).props.visible).toBe(false);
    expect(mockReset).toHaveBeenCalled();

    alert.mockRestore();
    act(() => tree.unmount());
  });

  it("removes the QR exactly at server expiry", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    const reissueOptions = mutationOptions("reissue-kid-handoff");

    act(() => {
      reissueOptions.onSuccess?.({
        id: "kid-1",
        display_name: "Test Kid",
        age_band: "9-10",
        handoff_token: "synthetic-one-time-token",
        expires_at: new Date(Date.now() + 1_000).toISOString(),
      });
    });
    expect(handoffModal(tree).props.visible).toBe(true);

    act(() => jest.advanceTimersByTime(1_001));
    expect(handoffModal(tree).props.visible).toBe(false);
    expect(mockReset).toHaveBeenCalled();

    act(() => tree.unmount());
  });

  it("drops the QR and owner action when the authenticated account changes", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    const reissueOptions = mutationOptions("reissue-kid-handoff");
    act(() => {
      reissueOptions.onSuccess?.({
        id: "kid-1",
        display_name: "Test Kid",
        age_band: "9-10",
        handoff_token: "synthetic-one-time-token",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
    });
    expect(handoffModal(tree).props.visible).toBe(true);

    act(() => {
      useAuthSession.getState().setAuthenticated({
        id: "parent-2",
        entra_oid: "entra-2",
        role: "parent",
        display_name: "Other Parent",
      });
    });

    expect(handoffModal(tree).props.visible).toBe(false);
    expect(
      tree.root.findAllByProps({ testID: "classroom-reissue-kid-kid-1" }),
    ).toHaveLength(0);

    act(() => tree.unmount());
  });

  it("drops the copy-only invitation when the authenticated account changes", async () => {
    mockedCreateAdultInvitation.mockResolvedValue({
      id: "invite-1",
      state: "pending",
      created_at: "2026-07-18T12:00:00Z",
      expires_at: "2026-07-21T12:00:00Z",
      redeemed_at: null,
      revoked_at: null,
      invite_url: `https://parents.example/group-invite#token=${"A".repeat(48)}`,
    });
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    await act(async () => {
      tree.root.findByProps({ testID: "groups-create-invitation" }).props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(tree.root.findAllByType(Modal).filter((node) => node.props.visible)).toHaveLength(1);

    act(() => {
      useAuthSession.getState().setAuthenticated({
        id: "parent-2",
        entra_oid: "entra-2",
        role: "parent",
        display_name: "Other Parent",
      });
    });
    expect(tree.root.findAllByType(Modal).filter((node) => node.props.visible)).toHaveLength(0);
    expect(tree.root.findAllByProps({ testID: "groups-copy-invitation" })).toHaveLength(0);
    act(() => tree.unmount());
  });

  it("drops the initial create response from mutation state after showing its QR", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<GroupsScreen />);
    });
    const createOptions = mutationOptions("create-kid");
    expect(createOptions.gcTime).toBe(0);
    mockReset.mockClear();

    act(() => {
      createOptions.onSuccess?.({
        id: "kid-new",
        display_name: "New Kid",
        age_band: "9-10",
        handoff_token: "synthetic-initial-token",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      });
    });

    expect(handoffModal(tree).props.visible).toBe(true);
    expect(mockReset).toHaveBeenCalledTimes(1);

    act(() => tree.unmount());
  });
});
