import renderer, { act } from "react-test-renderer";

import ReviewQueueScreen from "@/app/review-queue";

const mockRemoveQueries = jest.fn();
const mockInvalidateQueries = jest.fn();
const photoUrlState: {
  data?: { url: string; expires_at: string };
  isPending: boolean;
  isError: boolean;
} = {
  data: {
    url: "https://private.invalid/review-sas",
    expires_at: "2026-07-30T12:01:00Z",
  },
  isPending: false,
  isError: false,
};
const mockUsePhotoUrl = jest.fn(
  (_photoId: string, _enabled: boolean) => photoUrlState,
);

jest.mock("expo-router", () => {
  const React = require("react");
  return {
    router: { back: jest.fn() },
    Stack: { Screen: (props: unknown) => React.createElement("StackScreen", props) },
  };
});

jest.mock("@/src/auth/session", () => ({
  useAuthSession: () => ({
    status: "authenticated",
    user: {
      id: "adult-1",
      role: "parent",
      display_name: "Adult",
      entra_oid: "entra-1",
    },
  }),
}));

jest.mock("@/src/observation/usePhotoUrl", () => ({
  usePhotoUrl: (photoId: string, enabled: boolean) =>
    mockUsePhotoUrl(photoId, enabled),
}));

jest.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({
    removeQueries: mockRemoveQueries,
    invalidateQueries: mockInvalidateQueries,
  }),
  useQuery: () => ({
    data: {
      items: [
        {
          id: "review-1",
          group_id: "group-1",
          photo_id: "photo-1",
          observation_id: "observation-1",
          status: "pending",
          reason: "provider threshold",
          created_at: "2026-07-30T11:00:00Z",
        },
      ],
      next_cursor: null,
    },
    isPending: false,
    isError: false,
    isRefetching: false,
    refetch: jest.fn(),
  }),
  useMutation: () => ({
    mutate: jest.fn(),
    isPending: false,
    variables: undefined,
  }),
}));

describe("adult review photo URL lifecycle", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-07-30T12:00:00Z"));
    photoUrlState.data = {
      url: "https://private.invalid/review-sas",
      expires_at: "2026-07-30T12:01:00Z",
    };
    photoUrlState.isPending = false;
    photoUrlState.isError = false;
    mockUsePhotoUrl.mockClear();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("uses the shared 60-second photo hook and renders a usable URL", () => {
    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ReviewQueueScreen />);
    });

    expect(mockUsePhotoUrl).toHaveBeenCalledWith("photo-1", true);
    expect(
      tree.root.findAllByProps({ testID: "review-queue-photo-image" }).length,
    ).toBeGreaterThan(0);
    act(() => tree.unmount());
  });

  it("unmounts the image once its short-lived URL enters the safety margin", () => {
    photoUrlState.data = {
      url: "https://private.invalid/expired-review-sas",
      expires_at: "2026-07-30T12:00:10Z",
    };

    let tree!: renderer.ReactTestRenderer;
    act(() => {
      tree = renderer.create(<ReviewQueueScreen />);
    });

    expect(
      tree.root.findAllByProps({ testID: "review-queue-photo-image" }),
    ).toHaveLength(0);
    expect(
      tree.root.findAll((node) => node.props.children === "image unavailable").length,
    ).toBeGreaterThan(0);
    act(() => tree.unmount());
  });
});
