import { StyleSheet, Text, TextInput } from "react-native";
import renderer, { act, type ReactTestInstance } from "react-test-renderer";

import ClassroomScreen from "@/app/classroom";
import { useAuthSession } from "@/src/auth/session";

const mockUseQuery = jest.fn();
const mockMutate = jest.fn();
const mockInvalidateQueries = jest.fn();
let mockColorScheme: "light" | "dark" = "light";

jest.mock("expo-router", () => ({
  router: { back: jest.fn() },
  Stack: { Screen: () => null },
}));

jest.mock("react-native-qrcode-svg", () => "QRCode");

jest.mock("@/components/useColorScheme", () => ({
  useColorScheme: () => mockColorScheme,
}));

jest.mock("@tanstack/react-query", () => ({
  useQuery: (options: unknown) => mockUseQuery(options),
  useMutation: () => ({ isPending: false, mutate: mockMutate }),
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}));

jest.mock("@/src/api/groups", () => ({
  createGroup: jest.fn(),
  createKid: jest.fn(),
  listGroupMembers: jest.fn(),
  listGroups: jest.fn(),
}));

function textChild(control: ReactTestInstance, value: string): ReactTestInstance {
  return control.findAllByType(Text).find((node) => node.props.children === value)!;
}

describe("ClassroomScreen presentation contract", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    mockColorScheme = "light";
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
                { id: "group-1", name: "First Group", join_code: "ABC123" },
                { id: "group-2", name: "Second Group", join_code: "DEF456" },
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
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  it("keeps inactive controls and inputs readable on the light parent surface", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ClassroomScreen />);
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
    expect(input.props.accessibilityLabel).toBe("Kid display name");
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
      tree = renderer.create(<ClassroomScreen />);
      jest.runOnlyPendingTimers();
    });

    const roster = tree.root.findByProps({ testID: "classroom-roster-row-member-1" });
    expect(StyleSheet.flatten(roster.props.style)).toMatchObject({
      borderBottomColor: "rgba(255,255,255,0.1)",
    });

    act(() => tree.unmount());
  });
});
