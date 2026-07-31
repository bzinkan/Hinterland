import { StyleSheet, Text, TextInput } from "react-native";
import renderer, { act } from "react-test-renderer";

import ObservationDetailScreen from "@/app/observation/[id]";
import type { Observation } from "@/src/api/observations";

let mockDetail: Observation;
let mockPhotoHelperEnabled = false;
let mockColorScheme: "light" | "dark" = "light";

jest.mock("expo-router", () => {
  const React = require("react");
  return {
    router: { back: jest.fn(), replace: jest.fn() },
    Stack: { Screen: (props: any) => React.createElement("StackScreen", props) },
    useLocalSearchParams: () => ({ id: "obs-1" }),
  };
});

jest.mock("@/src/auth/session", () => ({
  useAuthSession: (selector?: (state: any) => unknown) => {
    const state = {
      status: "authenticated",
      user: { id: "kid-1", role: "kid", display_name: "Explorer", entra_oid: null },
    };
    return selector ? selector(state) : state;
  },
}));

jest.mock("@/components/useColorScheme", () => ({
  useColorScheme: () => mockColorScheme,
}));

jest.mock("@/src/observation/useObservationDetail", () => ({
  useObservationDetail: () => ({
    data: mockDetail,
    isPending: false,
    isError: false,
    error: null,
    refetch: jest.fn(),
  }),
}));

jest.mock("@/src/observation/useObservationCapabilities", () => ({
  useObservationCapabilities: () => ({ photoHelperEnabled: mockPhotoHelperEnabled }),
}));

jest.mock("@/src/observation/usePhotoUrl", () => ({
  usePhotoUrl: () => ({ isPending: true, isError: false, refetch: jest.fn() }),
}));

jest.mock("@/src/observation/useSpeciesFacts", () => ({
  useSpeciesFacts: () => ({ isPending: false, isError: true }),
}));

function detail(status: Observation["child_presentation_status"]): Observation {
  return {
    id: "obs-1",
    user_id: "kid-1",
    group_id: "group-1",
    photo_id: "photo-1",
    geohash4: null,
    observed_at: "2026-07-09T12:00:00Z",
    location_source: "none",
    taxon_id: null,
    species_name: "Moth",
    identification_source: "manual_text",
    identification_revision: 2,
    place_name: null,
    child_presentation_status: status,
    dispatch_status: "complete",
    rewards: [],
  };
}

const nonImagePresentations = [
  ["pending", "This photo is being checked."],
  ["processing", "This photo is being checked."],
  ["pilot_private", "This photo is private during the pilot."],
  ["adult_review", "An adult is reviewing this photo."],
  ["failed", "This photo is private while we sort out a check."],
] as const;

const expectedThemeColors = {
  light: {
    border: "#6b7280",
    controlBackground: "#ffffff",
    controlText: "#1f2937",
    inputBackground: "#ffffff",
    inputPlaceholder: "#4b5563",
    inputText: "#111827",
    panelBackground: "#f3f4f6",
    panelText: "#111827",
    secondaryText: "#111827",
  },
  dark: {
    border: "#9ca3af",
    controlBackground: "#1f2937",
    controlText: "#f9fafb",
    inputBackground: "#111827",
    inputPlaceholder: "#9ca3af",
    inputText: "#f9fafb",
    panelBackground: "#1a1a1a",
    panelText: "#f9fafb",
    secondaryText: "#d1d5db",
  },
} as const;

function textNode(
  tree: renderer.ReactTestRenderer,
  value: string,
) {
  const textContent = (children: unknown): string =>
    Array.isArray(children)
      ? children.map(textContent).join("")
      : typeof children === "string" || typeof children === "number"
        ? String(children)
        : "";
  return tree.root
    .findAllByType(Text)
    .find((node) => textContent(node.props.children) === value)!;
}

function contrastRatio(
  foreground: string,
  background: string,
  opacity = 1,
): number {
  const parseHex = (hex: string) =>
    hex
      .slice(1)
      .match(/.{2}/g)!
      .map((channel) => Number.parseInt(channel, 16));
  const backgroundChannels = parseHex(background);
  const foregroundChannels = parseHex(foreground).map((channel, index) =>
    Math.round(
      channel * opacity + backgroundChannels[index] * (1 - opacity),
    ),
  );
  const luminance = (hex: string) => {
    const channels = parseHex(hex)
      .map((channel) => channel / 255)
      .map((channel) =>
        channel <= 0.04045
          ? channel / 12.92
          : ((channel + 0.055) / 1.055) ** 2.4,
      );
    return (
      0.2126 * channels[0] +
      0.7152 * channels[1] +
      0.0722 * channels[2]
    );
  };
  const effectiveForeground = `#${foregroundChannels
    .map((channel) => channel.toString(16).padStart(2, "0"))
    .join("")}`;
  const light = Math.max(
    luminance(effectiveForeground),
    luminance(background),
  );
  const dark = Math.min(
    luminance(effectiveForeground),
    luminance(background),
  );
  return (light + 0.05) / (dark + 0.05);
}

describe("Observation detail presentation", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockDetail = detail("pilot_private");
    mockPhotoHelperEnabled = false;
    mockColorScheme = "light";
  });

  afterEach(() => jest.useRealTimers());

  it("keeps catalog/manual/Unknown correction available for metadata-only states", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<ObservationDetailScreen />); });
    const rendered = JSON.stringify(tree.toJSON());
    expect(
      tree.root.findAllByProps({ testID: "observation-detail-screen" }).length,
    ).toBeGreaterThan(0);
    expect(
      tree.root.findAllByProps({ testID: "observation-detail-private-status" }).length,
    ).toBeGreaterThan(0);
    expect(
      tree.root.findAllByProps({ testID: "observation-detail-photo-image" }),
    ).toHaveLength(0);
    expect(
      tree.root.findAllByProps({ testID: "observation-photo-helper-button" }),
    ).toHaveLength(0);
    expect(rendered).toContain("This photo is private during the pilot.");
    expect(rendered).toContain("Improve identification");
    expect(rendered).toContain("Manual identification correction");
    expect(rendered).toContain("Use Unknown");
    expect(rendered).not.toContain("Ask the photo helper");
    act(() => tree.unmount());
  });

  it.each(["light", "dark"] as const)(
    "keeps every metadata-only state and correction control readable in %s mode",
    (colorScheme) => {
      mockColorScheme = colorScheme;
      const colors = expectedThemeColors[colorScheme];

      for (const [status, message] of nonImagePresentations) {
        mockDetail = detail(status);
        let tree!: renderer.ReactTestRenderer;
        act(() => {
          tree = renderer.create(<ObservationDetailScreen />);
        });

        const statusSurface = tree.root.findByProps({
          accessibilityLabel: message,
        });
        const statusSurfaceStyle = StyleSheet.flatten(statusSurface.props.style);
        const statusTextStyle = StyleSheet.flatten(
          textNode(tree, message).props.style,
        );
        expect(statusSurfaceStyle).toMatchObject({
          backgroundColor: colors.panelBackground,
        });
        expect(statusTextStyle).toMatchObject({ color: colors.panelText });
        expect(
          contrastRatio(
            statusTextStyle.color,
            statusSurfaceStyle.backgroundColor,
            statusTextStyle.opacity,
          ),
        ).toBeGreaterThanOrEqual(4.5);

        const headingStyle = StyleSheet.flatten(
          textNode(tree, "Improve identification").props.style,
        );
        expect(headingStyle).toMatchObject({ color: colors.panelText });
        expect(
          contrastRatio(headingStyle.color, colors.panelBackground),
        ).toBeGreaterThanOrEqual(4.5);

        const currentStyle = StyleSheet.flatten(
          textNode(tree, "Current: Moth").props.style,
        );
        expect(currentStyle).toMatchObject({ color: colors.secondaryText });
        expect(
          contrastRatio(
            currentStyle.color,
            colors.panelBackground,
            currentStyle.opacity,
          ),
        ).toBeGreaterThanOrEqual(4.5);

        const manualInput = tree.root.findByProps({
          accessibilityLabel: "Manual identification correction",
        });
        expect(manualInput.type).toBe(TextInput);
        const inputStyle = StyleSheet.flatten(manualInput.props.style);
        expect(inputStyle).toMatchObject({
          backgroundColor: colors.inputBackground,
          borderColor: colors.border,
          color: colors.inputText,
        });
        expect(manualInput.props.placeholderTextColor).toBe(
          colors.inputPlaceholder,
        );
        expect(
          contrastRatio(inputStyle.color, inputStyle.backgroundColor),
        ).toBeGreaterThanOrEqual(4.5);
        expect(
          contrastRatio(
            manualInput.props.placeholderTextColor,
            inputStyle.backgroundColor,
          ),
        ).toBeGreaterThanOrEqual(4.5);
        expect(
          contrastRatio(inputStyle.borderColor, inputStyle.backgroundColor),
        ).toBeGreaterThanOrEqual(3);
        expect(
          contrastRatio(inputStyle.borderColor, colors.panelBackground),
        ).toBeGreaterThanOrEqual(3);

        const unknownControl = tree.root.findByProps({
          accessibilityLabel: "Mark identification Unknown",
        });
        const unknownControlStyle = StyleSheet.flatten(
          unknownControl.props.style,
        );
        expect(unknownControlStyle).toMatchObject({
          backgroundColor: colors.controlBackground,
          borderColor: colors.border,
        });
        const unknownStyle = StyleSheet.flatten(
          textNode(tree, "Use Unknown").props.style,
        );
        expect(unknownStyle).toMatchObject({ color: colors.controlText });
        expect(
          contrastRatio(unknownStyle.color, colors.controlBackground),
        ).toBeGreaterThanOrEqual(4.5);
        expect(
          contrastRatio(
            unknownControlStyle.borderColor,
            unknownControlStyle.backgroundColor,
          ),
        ).toBeGreaterThanOrEqual(3);
        expect(
          contrastRatio(
            unknownControlStyle.borderColor,
            colors.panelBackground,
          ),
        ).toBeGreaterThanOrEqual(3);

        expect(
          tree.root.findAllByProps({
            testID: "observation-detail-photo-image",
          }),
        ).toHaveLength(0);
        expect(
          tree.root.findAllByProps({
            testID: "observation-photo-helper-button",
          }),
        ).toHaveLength(0);

        act(() => tree.unmount());
      }
    },
  );

  it("shows the helper only when both capability and clean-photo gates pass", () => {
    mockDetail = detail("clean");
    mockPhotoHelperEnabled = true;
    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<ObservationDetailScreen />); });
    expect(
      tree.root.findAllByProps({ testID: "observation-photo-helper-button" }).length,
    ).toBeGreaterThan(0);
    expect(JSON.stringify(tree.toJSON())).toContain("Ask the photo helper");
    act(() => tree.unmount());
  });

  it("treats a nonconforming rejected detail response as absent", () => {
    mockDetail = detail("rejected" as never);
    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<ObservationDetailScreen />); });
    const rendered = JSON.stringify(tree.toJSON());
    expect(rendered).toContain("Couldn't find that entry");
    expect(rendered).not.toContain("Improve identification");
    act(() => tree.unmount());
  });
});
