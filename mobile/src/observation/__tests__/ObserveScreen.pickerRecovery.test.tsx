import { AppState, type AppStateStatus, Platform } from "react-native";
import renderer, { act } from "react-test-renderer";

import ObserveScreen from "@/app/(tabs)/observe";
import {
  clearPickerRequest,
  pollPendingPickerResult,
  readPickerRequestMarker,
  type PickerRequestMarker,
} from "@/src/observation/pickerRecovery";
import {
  discardQueuedObservation,
  getQueuedObservation,
  persistObservationDraft,
} from "@/src/observation/observationQueue";
import { useDraftStore } from "@/src/observation/draftStore";
import { router } from "expo-router";

let mockIsFocused = false;
let mockQueueItems: Array<Record<string, unknown>> = [];
const mockSession = {
  status: "authenticated" as const,
  user: {
    id: "kid-1",
    role: "kid" as const,
    display_name: "Explorer",
    entra_oid: null,
  },
};

jest.mock("@expo/vector-icons/FontAwesome", () => ({
  __esModule: true,
  default: "FontAwesome",
}));

jest.mock("expo-camera", () => ({
  CameraView: "CameraView",
  useCameraPermissions: () => [{ granted: true }, jest.fn()],
}));

jest.mock("expo-image-manipulator", () => ({
  SaveFormat: { JPEG: "jpeg" },
  manipulateAsync: jest.fn(async () => ({
    uri: "file:///normalized-picker-photo.jpg",
    width: 640,
    height: 480,
  })),
}));

jest.mock("expo-image-picker", () => ({
  getPendingResultAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));

jest.mock("expo-router", () => ({
  router: {
    push: jest.fn(),
    replace: jest.fn(),
  },
}));

jest.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: { items: [], active_expedition_id: null },
    isPending: false,
    isError: false,
  }),
}));

jest.mock("@react-navigation/native", () => {
  const React = require("react");
  return {
    useIsFocused: () => mockIsFocused,
    useFocusEffect: (callback: () => void | (() => void)) => {
      React.useEffect(() => {
        if (!mockIsFocused) return undefined;
        return callback();
      }, [callback, mockIsFocused]);
    },
  };
});

jest.mock("@/src/auth/session", () => {
  const useAuthSession = (selector?: (state: typeof mockSession) => unknown) =>
    selector ? selector(mockSession) : mockSession;
  useAuthSession.getState = () => mockSession;
  return { useAuthSession };
});

jest.mock("@/src/observation/useObservationQueue", () => ({
  useObservationQueue: () => ({
    items: mockQueueItems,
    loading: false,
    reload: jest.fn(),
  }),
}));

jest.mock("@/src/observation/observationQueue", () => ({
  discardQueuedObservation: jest.fn(),
  getQueuedObservation: jest.fn(),
  persistObservationDraft: jest.fn(),
  ObservationQueueFullError: class ObservationQueueFullError extends Error {},
}));

jest.mock("@/src/observation/pickerRecovery", () => ({
  beginPickerRequest: jest.fn(),
  clearPickerRequest: jest.fn(),
  pollPendingPickerResult: jest.fn(),
  pickerMarkerMatches: (
    marker: PickerRequestMarker | null,
    ownerUserId: string | null,
    requestId: string,
  ) => marker?.ownerUserId === ownerUserId && marker.requestId === requestId,
  readPickerRequestMarker: jest.fn(),
}));

jest.mock("@/src/api/expeditions", () => ({
  listMyExpeditions: jest.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function flushMicrotasks(rounds = 12): Promise<void> {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve();
  }
}

describe("Observe Android picker process-death recovery", () => {
  const originalPlatform = Platform.OS;
  const marker: PickerRequestMarker = {
    ownerUserId: "kid-1",
    requestId: "01J00000000000000000000000",
    createdAt: "2026-07-31T12:00:00.000Z",
  };
  let appStateListener: ((state: AppStateStatus) => void) | null;
  let removeAppStateListener: jest.Mock;
  let originalSetPhoto: ReturnType<typeof useDraftStore.getState>["setPhoto"];
  let setPhoto: jest.Mock;
  let tree: renderer.ReactTestRenderer | null;

  beforeEach(() => {
    jest.clearAllMocks();
    mockIsFocused = false;
    mockQueueItems = [];
    mockSession.user.id = "kid-1";
    tree = null;
    Object.defineProperty(Platform, "OS", {
      configurable: true,
      value: "android",
    });

    appStateListener = null;
    removeAppStateListener = jest.fn();
    jest.spyOn(AppState, "addEventListener").mockImplementation(
      (_type, listener) => {
        appStateListener = listener;
        return { remove: removeAppStateListener };
      },
    );

    originalSetPhoto = useDraftStore.getState().setPhoto;
    setPhoto = jest.fn((photo) => originalSetPhoto(photo));
    useDraftStore.setState({ photo: null, setPhoto });

    (readPickerRequestMarker as jest.Mock).mockResolvedValue(marker);
    (getQueuedObservation as jest.Mock).mockResolvedValue(null);
    (discardQueuedObservation as jest.Mock).mockResolvedValue(undefined);
    (persistObservationDraft as jest.Mock).mockResolvedValue({
      submissionKey: marker.requestId,
      ownerUserId: marker.ownerUserId,
      localUri: "file:///durable-picker-photo.jpg",
      width: 640,
      height: 480,
      observedAt: marker.createdAt,
      source: "library",
      stage: "ready",
    });
  });

  afterEach(() => {
    if (tree) {
      act(() => tree?.unmount());
      tree = null;
    }
    jest.restoreAllMocks();
    Object.defineProperty(Platform, "OS", {
      configurable: true,
      value: originalPlatform,
    });
    useDraftStore.setState({ photo: null, setPhoto: originalSetPhoto });
  });

  it("queues an active signal, restores the draft before navigation, and survives focus", async () => {
    const firstRecovery = deferred<null>();
    const recoveredResult = {
      canceled: false,
      assets: [
        {
          uri: "file:///picker-result.jpg",
          width: 640,
          height: 480,
        },
      ],
    };
    (pollPendingPickerResult as jest.Mock)
      .mockReturnValueOnce(firstRecovery.promise)
      .mockResolvedValueOnce(recoveredResult);

    await act(async () => {
      tree = renderer.create(<ObserveScreen />);
      await flushMicrotasks();
    });

    expect(pollPendingPickerResult).toHaveBeenCalledTimes(1);
    expect(appStateListener).not.toBeNull();
    expect(clearPickerRequest).not.toHaveBeenCalled();

    act(() => {
      appStateListener?.("active");
    });
    expect(pollPendingPickerResult).toHaveBeenCalledTimes(1);

    await act(async () => {
      firstRecovery.resolve(null);
      await flushMicrotasks();
    });

    expect(pollPendingPickerResult).toHaveBeenCalledTimes(2);
    expect(persistObservationDraft).toHaveBeenCalledTimes(1);
    expect(setPhoto).toHaveBeenCalledTimes(1);
    expect(useDraftStore.getState().photo).toMatchObject({
      submissionKey: marker.requestId,
      ownerUserId: marker.ownerUserId,
      localUri: "file:///durable-picker-photo.jpg",
    });
    expect(router.replace).toHaveBeenCalledTimes(1);
    expect(router.replace).toHaveBeenCalledWith("/observe");
    expect(setPhoto.mock.invocationCallOrder[0]).toBeLessThan(
      (router.replace as jest.Mock).mock.invocationCallOrder[0],
    );
    expect(clearPickerRequest).toHaveBeenCalledTimes(1);
    expect(
      tree!.root.findAllByProps({ testID: "observation-confirm-screen" }).length,
    ).toBeGreaterThan(0);

    mockIsFocused = true;
    await act(async () => {
      tree!.update(<ObserveScreen />);
      await flushMicrotasks();
    });

    expect(
      tree!.root.findAllByProps({ testID: "observation-confirm-screen" }).length,
    ).toBeGreaterThan(0);
    expect(useDraftStore.getState().photo?.submissionKey).toBe(marker.requestId);

    act(() => tree!.unmount());
    tree = null;
    expect(removeAppStateListener).toHaveBeenCalledTimes(1);
  });

  it("restores a durable marker row before consulting the one-shot native result", async () => {
    (getQueuedObservation as jest.Mock).mockResolvedValue({
      submissionKey: marker.requestId,
      ownerUserId: marker.ownerUserId,
      localUri: "file:///already-durable-picker-photo.jpg",
      width: 640,
      height: 480,
      observedAt: marker.createdAt,
      source: "library",
      stage: "ready",
      payloadFrozen: false,
    });

    await act(async () => {
      tree = renderer.create(<ObserveScreen />);
      await flushMicrotasks();
    });

    expect(getQueuedObservation).toHaveBeenCalledWith(
      marker.ownerUserId,
      marker.requestId,
    );
    expect(pollPendingPickerResult).not.toHaveBeenCalled();
    expect(persistObservationDraft).not.toHaveBeenCalled();
    expect(useDraftStore.getState().photo).toMatchObject({
      submissionKey: marker.requestId,
      localUri: "file:///already-durable-picker-photo.jpg",
    });
    expect(router.replace).toHaveBeenCalledWith("/observe");
    expect(clearPickerRequest).toHaveBeenCalledWith(marker);
    expect(
      tree!.root.findAllByProps({ testID: "observation-confirm-screen" }).length,
    ).toBeGreaterThan(0);

    act(() => tree!.unmount());
    tree = null;
  });

  it("makes Retake win atomically over a queued Continue press", async () => {
    mockQueueItems = [
      {
        submissionKey: marker.requestId,
        ownerUserId: marker.ownerUserId,
        localUri: "file:///durable-picker-photo.jpg",
        width: 640,
        height: 480,
        observedAt: marker.createdAt,
        source: "library",
        stage: "ready",
        payloadFrozen: false,
      },
    ];
    (pollPendingPickerResult as jest.Mock).mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file:///picker-result.jpg",
          width: 640,
          height: 480,
        },
      ],
    });
    const discard = deferred<void>();
    (discardQueuedObservation as jest.Mock).mockReturnValueOnce(discard.promise);

    await act(async () => {
      tree = renderer.create(<ObserveScreen />);
      await flushMicrotasks();
    });

    const retake = tree!.root
      .findAll((node) => typeof node.props.onPress === "function")
      .find(
        (node) => node.findAllByProps({ children: "Retake" }).length > 0,
      );
    expect(retake).toBeDefined();
    const continueButton = tree!.root.findByProps({
      testID: "observation-confirm-button",
    });
    const retakePress = retake!.props.onPress;
    const continuePress = continueButton.props.onPress;

    act(() => {
      retakePress();
      continuePress();
    });

    expect(discardQueuedObservation).toHaveBeenCalledTimes(1);
    expect(router.push).not.toHaveBeenCalled();
    expect(useDraftStore.getState().photo).toBeNull();
    expect(
      tree!.root.findAllByProps({ testID: "observation-confirm-screen" }),
    ).toHaveLength(0);
    expect(
      tree!.root.findAllByProps({ testID: "observation-queue-item" }),
    ).toHaveLength(0);

    await act(async () => {
      discard.resolve();
      await flushMicrotasks();
    });
    // The hook's queue snapshot is intentionally still stale here. The
    // successful-delete tombstone must keep the deleted row suppressed.
    expect(
      tree!.root.findAllByProps({ testID: "observation-queue-item" }),
    ).toHaveLength(0);
    act(() => tree!.unmount());
    tree = null;
  });

  it("persists for the initiating owner but withholds UI when the account changes", async () => {
    (pollPendingPickerResult as jest.Mock).mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file:///picker-result.jpg",
          width: 640,
          height: 480,
        },
      ],
    });
    const persisted = deferred<Record<string, unknown>>();
    (persistObservationDraft as jest.Mock).mockReturnValueOnce(persisted.promise);

    await act(async () => {
      tree = renderer.create(<ObserveScreen />);
      await flushMicrotasks();
    });
    expect(persistObservationDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        submissionKey: marker.requestId,
        ownerUserId: "kid-1",
      }),
    );

    mockSession.user.id = "kid-2";
    await act(async () => {
      persisted.resolve({
        submissionKey: marker.requestId,
        ownerUserId: "kid-1",
        localUri: "file:///durable-picker-photo.jpg",
        width: 640,
        height: 480,
        observedAt: marker.createdAt,
        source: "library",
        stage: "ready",
      });
      await flushMicrotasks();
    });

    expect(useDraftStore.getState().photo).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
    expect(clearPickerRequest).not.toHaveBeenCalled();

    act(() => tree!.unmount());
    tree = null;
  });

  it("durably keeps a one-shot result returned after the owning effect is cancelled", async () => {
    const nativeRead = deferred<{
      canceled: false;
      assets: Array<{ uri: string; width: number; height: number }>;
    }>();
    (pollPendingPickerResult as jest.Mock).mockReturnValueOnce(nativeRead.promise);

    await act(async () => {
      tree = renderer.create(<ObserveScreen />);
      await flushMicrotasks();
    });
    expect(pollPendingPickerResult).toHaveBeenCalledTimes(1);

    mockSession.user.id = "kid-2";
    await act(async () => {
      tree!.update(<ObserveScreen />);
      await flushMicrotasks();
    });

    await act(async () => {
      nativeRead.resolve({
        canceled: false,
        assets: [
          {
            uri: "file:///late-native-picker-result.jpg",
            width: 640,
            height: 480,
          },
        ],
      });
      await flushMicrotasks();
    });

    expect(persistObservationDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        submissionKey: marker.requestId,
        ownerUserId: "kid-1",
      }),
    );
    expect(useDraftStore.getState().photo).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
    expect(clearPickerRequest).not.toHaveBeenCalled();

    act(() => tree!.unmount());
    tree = null;
  });
});
