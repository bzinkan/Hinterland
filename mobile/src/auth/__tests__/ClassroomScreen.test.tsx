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

jest.mock("@tanstack/react-query", () => ({
  useQuery: (options: unknown) => mockUseQuery(options),
  useMutation: (options: unknown) => mockUseMutation(options),
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

jest.mock("@/src/api/groups", () => ({
  createGroup: jest.fn(),
  createKid: jest.fn(),
  listGroupMembers: jest.fn(),
  listGroups: jest.fn(),
  reissueKidHandoff: jest.fn(),
}));

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
  onSuccess?: (value: unknown) => void;
};

function mutationOptions(key: string): MutationOptions {
  return mockUseMutation.mock.calls
    .map(([options]) => options as MutationOptions)
    .find((options) => options.mutationKey?.[0] === key)!;
}

describe("GroupsScreen presentation contract", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    mockColorScheme = "light";
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
          return {
            isPending: false,
            isError: false,
            data: {
              items: [
                {
                  id: "group-1",
                  name: "First Group",
                  join_code: "ABC123",
                  owner_user_id: "parent-1",
                },
                {
                  id: "group-2",
                  name: "Second Group",
                  join_code: "DEF456",
                  owner_user_id: "parent-1",
                },
              ],
            },
          };
        }
        return {
          isPending: false,
          isError: false,
          data: {
            items: [
              {
                user_id: "parent-1",
                membership_id: "member-parent",
                display_name: "Test Parent",
                role: "parent",
                age_band: null,
                observation_count: 0,
                dex_count: 0,
              },
              {
                user_id: "kid-1",
                membership_id: "member-1",
                display_name: "Test Kid",
                role: "kid",
                age_band: "9-10",
                observation_count: 0,
                dex_count: 0,
              },
            ],
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

    const roster = tree.root.findByProps({ testID: "classroom-roster-row-member-1" });
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
