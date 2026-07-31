import { Platform } from "react-native";
import renderer, { act } from "react-test-renderer";

import TabLayout from "@/app/(tabs)/_layout";
import { Tabs } from "expo-router";

jest.mock("@expo/vector-icons/FontAwesome", () => ({
  __esModule: true,
  default: "FontAwesome",
}));

jest.mock("expo-router", () => {
  const React = require("react");
  const Screen = jest.fn(() => null);
  const MockTabs = ({ children }: { children: unknown }) =>
    React.createElement(React.Fragment, null, children);
  MockTabs.Screen = Screen;
  return { Tabs: MockTabs };
});

jest.mock("@/components/useColorScheme", () => ({
  useColorScheme: () => "light",
}));

jest.mock("@/components/useClientOnlyValue", () => ({
  useClientOnlyValue: (_server: unknown, client: unknown) => client,
}));

describe("Android picker recovery tab mounting", () => {
  const originalPlatform = Platform.OS;

  afterEach(() => {
    Object.defineProperty(Platform, "OS", {
      configurable: true,
      value: originalPlatform,
    });
    jest.clearAllMocks();
  });

  it("eagerly mounts Observe while another tab owns the foreground", () => {
    Object.defineProperty(Platform, "OS", {
      configurable: true,
      value: "android",
    });

    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<TabLayout />);
    });

    const screenMock = Tabs.Screen as unknown as jest.Mock;
    const observeScreen = screenMock.mock.calls
      .map(([props]) => props)
      .find((props) => props.name === "observe");

    expect(observeScreen).toBeDefined();
    expect(observeScreen.options.lazy).toBe(false);

    act(() => tree.unmount());
  });
});
