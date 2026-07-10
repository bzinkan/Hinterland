import renderer, { act } from "react-test-renderer";

import ObservationDetailScreen from "@/app/observation/[id]";
import type { Observation } from "@/src/api/observations";

let mockDetail: Observation;
let mockPhotoHelperEnabled = false;

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

describe("Observation detail presentation", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockDetail = detail("pilot_private");
    mockPhotoHelperEnabled = false;
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
