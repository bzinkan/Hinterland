import * as SecureStore from "expo-secure-store";

import {
  beginPickerRequest,
  clearPickerRequest,
  pickerMarkerMatches,
  pollPendingPickerResult,
  readPickerRequestMarker,
  type PickerRequestMarker,
} from "@/src/observation/pickerRecovery";

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
}));

jest.mock("@/src/observation/ulid", () => ({
  createSubmissionUlid: jest
    .fn()
    .mockResolvedValue("01J00000000000000000000000"),
}));

const getItemAsync = SecureStore.getItemAsync as jest.MockedFunction<
  typeof SecureStore.getItemAsync
>;
const setItemAsync = SecureStore.setItemAsync as jest.MockedFunction<
  typeof SecureStore.setItemAsync
>;
const deleteItemAsync = SecureStore.deleteItemAsync as jest.MockedFunction<
  typeof SecureStore.deleteItemAsync
>;

describe("Android picker recovery marker", () => {
  let stored: string | null;

  beforeEach(() => {
    stored = null;
    jest.clearAllMocks();
    getItemAsync.mockImplementation(async () => stored);
    setItemAsync.mockImplementation(async (_key, value) => {
      stored = value;
    });
    deleteItemAsync.mockImplementation(async () => {
      stored = null;
    });
  });

  it("persists the initiating owner and stable submission ULID before launch", async () => {
    const marker = await beginPickerRequest("kid-1");

    expect(marker.ownerUserId).toBe("kid-1");
    expect(marker.requestId).toHaveLength(26);
    await expect(readPickerRequestMarker()).resolves.toEqual(marker);
  });

  it("never authorizes a pending picker result for a different account", async () => {
    const marker = await beginPickerRequest("kid-1");

    expect(pickerMarkerMatches(marker, "kid-2", marker.requestId)).toBe(false);
    await clearPickerRequest({ ownerUserId: "kid-2", requestId: marker.requestId });

    await expect(readPickerRequestMarker()).resolves.toEqual(marker);
    expect(deleteItemAsync).not.toHaveBeenCalled();
  });

  it("conditionally clears only the exact owner/request marker", async () => {
    const marker = await beginPickerRequest("kid-1");
    await clearPickerRequest(marker);

    expect(deleteItemAsync).toHaveBeenCalledTimes(1);
    await expect(readPickerRequestMarker()).resolves.toBeNull();
  });

  it("expires stale process-death markers", async () => {
    stored = JSON.stringify({
      ownerUserId: "kid-1",
      requestId: "01J00000000000000000000000",
      createdAt: "2026-07-09T12:00:00.000Z",
    });

    await expect(
      readPickerRequestMarker(new Date("2026-07-09T12:31:00.000Z")),
    ).resolves.toBeNull();
    expect(deleteItemAsync).toHaveBeenCalledTimes(1);
  });
});

describe("pollPendingPickerResult", () => {
  const marker: PickerRequestMarker = {
    ownerUserId: "kid-1",
    requestId: "01J00000000000000000000000",
    createdAt: "2026-07-31T12:00:00.000Z",
  };

  it("retries null native results and returns the first recovered result", async () => {
    const recovered = { assets: [{ uri: "file:///recovered.jpg" }] };
    const readPendingResult = jest
      .fn<Promise<typeof recovered | null>, []>()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(recovered);
    const requestIsStillCurrent = jest.fn().mockResolvedValue(true);
    const wait = jest.fn().mockResolvedValue(undefined);

    await expect(
      pollPendingPickerResult(marker, readPendingResult, requestIsStillCurrent, {
        delaysMs: [20, 40],
        wait,
      }),
    ).resolves.toBe(recovered);

    expect(readPendingResult).toHaveBeenCalledTimes(3);
    expect(wait.mock.calls).toEqual([[20], [40]]);
    expect(requestIsStillCurrent).toHaveBeenCalledWith(marker);
  });

  it("returns null after exhausting the configured attempts", async () => {
    const readPendingResult = jest.fn().mockResolvedValue(null);
    const wait = jest.fn().mockResolvedValue(undefined);

    await expect(
      pollPendingPickerResult(marker, readPendingResult, () => true, {
        delaysMs: [10, 30],
        wait,
      }),
    ).resolves.toBeNull();

    expect(readPendingResult).toHaveBeenCalledTimes(3);
    expect(wait.mock.calls).toEqual([[10], [30]]);
  });

  it("cancels when the exact owner/request marker is no longer current", async () => {
    let current = {
      ownerUserId: marker.ownerUserId,
      requestId: marker.requestId,
    };
    const readPendingResult = jest.fn().mockResolvedValue(null);
    const wait = jest.fn(async () => {
      current = { ...current, requestId: "01J00000000000000000000001" };
    });
    const requestIsStillCurrent = jest.fn(
      async (candidate: PickerRequestMarker) =>
        candidate.ownerUserId === current.ownerUserId &&
        candidate.requestId === current.requestId,
    );

    await expect(
      pollPendingPickerResult(marker, readPendingResult, requestIsStillCurrent, {
        delaysMs: [20, 40],
        wait,
      }),
    ).resolves.toBeNull();

    expect(readPendingResult).toHaveBeenCalledTimes(1);
    expect(wait).toHaveBeenCalledTimes(1);
    expect(requestIsStillCurrent).toHaveBeenCalledWith(marker);
  });

  it("returns a consumed native result when ownership changes during its read", async () => {
    const recovered = { assets: [{ uri: "file:///recovered.jpg" }] };
    let resolveRead!: (value: typeof recovered) => void;
    const nativeRead = new Promise<typeof recovered>((resolve) => {
      resolveRead = resolve;
    });
    let current = true;
    const readPendingResult = jest.fn(() => nativeRead);

    const pending = pollPendingPickerResult(
      marker,
      readPendingResult,
      () => current,
      { delaysMs: [] },
    );
    for (let index = 0; index < 3; index += 1) {
      await Promise.resolve();
    }
    expect(readPendingResult).toHaveBeenCalledTimes(1);

    current = false;
    resolveRead(recovered);

    await expect(pending).resolves.toBe(recovered);
  });
});
