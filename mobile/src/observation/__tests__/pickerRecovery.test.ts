import * as SecureStore from "expo-secure-store";

import {
  beginPickerRequest,
  clearPickerRequest,
  pickerMarkerMatches,
  readPickerRequestMarker,
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
