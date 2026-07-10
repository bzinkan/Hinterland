import renderer, { act } from "react-test-renderer";

import FieldJournalScreen from "@/app/(tabs)/index";
import type { DexListItem } from "@/src/api/dex";
import type { ObservationListItem } from "@/src/api/observations";
import type { QueuedObservation } from "@/src/observation/queueTypes";

const mockObservations: ObservationListItem[] = [];
const mockDex: DexListItem[] = [];
const mockQueue: QueuedObservation[] = [];
const mockPhotoUrlState = {
  isPending: false,
  isError: false,
  data: {
    url: "https://private.invalid/short-sas",
    expires_at: "2099-01-01T00:00:00Z",
  },
  refetch: jest.fn(),
};

jest.mock("@shopify/flash-list", () => {
  const React = require("react");
  const { View } = require("react-native");
  return {
    FlashList: ({ data, renderItem, ListHeaderComponent, ListEmptyComponent, testID }: any) =>
      React.createElement(
        View,
        { testID },
        ListHeaderComponent,
        data.length
          ? data.map((item: any, index: number) =>
              React.createElement(
                React.Fragment,
                { key: item.id },
                renderItem({ item, index }),
              ),
            )
          : ListEmptyComponent,
      ),
  };
});

jest.mock("expo-router", () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

jest.mock("@/src/auth/session", () => ({
  useAuthSession: (selector?: (state: any) => unknown) => {
    const state = {
      status: "authenticated",
      user: { id: "kid-1", role: "kid", display_name: "Explorer", entra_oid: null },
    };
    return selector ? selector(state) : state;
  },
}));

jest.mock("@/src/observation/useMyObservations", () => ({
  useMyObservations: () => ({
    data: { pages: [{ items: mockObservations, next_cursor: null }] },
    error: null,
    isPending: false,
    isRefetching: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    refetch: jest.fn(),
    fetchNextPage: jest.fn(),
  }),
}));

jest.mock("@/src/observation/useMyDex", () => ({
  useMyDex: () => ({
    data: { pages: [{ items: mockDex, next_cursor: null }] },
    error: null,
    isPending: false,
    isRefetching: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    refetch: jest.fn(),
    fetchNextPage: jest.fn(),
  }),
}));

jest.mock("@/src/observation/useObservationQueue", () => ({
  useObservationQueue: () => ({
    items: mockQueue,
    loading: false,
    reload: jest.fn(),
  }),
}));

jest.mock("@/src/observation/usePhotoUrl", () => ({
  usePhotoUrl: jest.fn(() => mockPhotoUrlState),
}));

function observation(
  id: string,
  child_presentation_status: ObservationListItem["child_presentation_status"],
): ObservationListItem {
  return {
    id,
    photo_id: `photo-${id}`,
    submission_ulid: `submission-${id}`,
    geohash4: null,
    observed_at: "2026-07-09T12:00:00Z",
    location_source: "none",
    taxon_id: null,
    species_name: `Find ${id}`,
    identification_source: "unknown",
    place_name: null,
    child_presentation_status,
    dispatch_status: "complete",
  };
}

function dex(index: number): DexListItem {
  return {
    id: `dex-${index}`,
    taxon_id: index,
    species_name: `Species ${index}`,
    common_name: `Species ${index}`,
    scientific_name: null,
    iconic_taxon: null,
    first_observation_id: `obs-${index}`,
    representative_photo_id: null,
    first_seen_at: "2026-07-09T12:00:00Z",
    observation_count: 1,
    latest_seen_at: "2026-07-09T12:00:00Z",
  };
}

describe("Field Journal screen", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockObservations.splice(0);
    mockDex.splice(0);
    mockQueue.splice(0);
  });

  afterEach(() => jest.useRealTimers());

  it("renders private entries and queued work as metadata-only truthful cards", () => {
    mockObservations.push(
      observation("pilot", "pilot_private"),
      observation("pending", "pending"),
      observation("adult", "adult_review"),
      observation("failed", "failed"),
    );
    mockQueue.push({
      submissionKey: "local-only",
      ownerUserId: "kid-1",
      observedAt: "2026-07-10T12:00:00Z",
      identification: { source: "manual_text", taxonId: null, speciesName: "Moth" },
      placeName: null,
      stage: "uploaded",
      lastRequestId: null,
    } as QueuedObservation);

    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<FieldJournalScreen />); });
    const rendered = JSON.stringify(tree.toJSON());
    expect(
      tree.root.findAllByProps({ testID: "field-journal-observation-card" }).length,
    ).toBeGreaterThan(0);
    expect(
      tree.root.findAllByProps({ testID: "field-journal-private-status" }).length,
    ).toBeGreaterThan(0);
    expect(
      tree.root.findAllByProps({ testID: "field-journal-photo-image" }),
    ).toHaveLength(0);
    expect(rendered).toContain("This photo is private during the pilot.");
    expect(rendered).toContain("This photo is being checked.");
    expect(rendered).toContain("An adult is reviewing this photo.");
    expect(rendered).toContain("This photo is private while we sort out a check.");
    expect(rendered).toContain("Waiting to sync");
    expect(rendered).toContain("Moth");
    expect(rendered).not.toContain("file://");
    act(() => tree.unmount());
  });

  it("switches accessible tabs and virtualizes a large Species data contract", () => {
    mockDex.push(...Array.from({ length: 500 }, (_, index) => dex(index)));
    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<FieldJournalScreen />); });
    const speciesTab = tree.root.findAll(
      (node) =>
        node.props.accessibilityLabel === "Species Field Journal tab" &&
        node.props.accessibilityState?.selected === false,
    );
    expect(speciesTab.length).toBeGreaterThan(0);
    act(() => speciesTab[0].props.onPress());
    expect(
      tree.root.findAllByProps({
        accessibilityHint: "Opens the first accepted Field Journal entry for this species",
      }).length,
    ).toBeGreaterThanOrEqual(500);
    act(() => tree.unmount());
  });

  it("unmounts an already-open clean image as soon as status becomes private", () => {
    mockObservations.push(observation("changing", "clean"));
    let tree!: renderer.ReactTestRenderer;
    act(() => { tree = renderer.create(<FieldJournalScreen />); });
    expect(
      tree.root.findAllByProps({ testID: "field-journal-photo-image" }).length,
    ).toBeGreaterThan(0);
    expect(JSON.stringify(tree.toJSON())).toContain("short-sas");

    mockObservations[0] = observation("changing", "adult_review");
    act(() => tree.update(<FieldJournalScreen />));
    const rendered = JSON.stringify(tree.toJSON());
    expect(
      tree.root.findAllByProps({ testID: "field-journal-photo-image" }),
    ).toHaveLength(0);
    expect(rendered).not.toContain("short-sas");
    expect(rendered).toContain("An adult is reviewing this photo.");
    act(() => tree.unmount());
  });
});
