import * as SecureStore from "expo-secure-store";

import { createSubmissionUlid } from "@/src/observation/ulid";

const PICKER_MARKER_KEY = "dragonfly.observation_picker_request.v1";
const PICKER_MARKER_TTL_MS = 30 * 60_000;

export type PickerRequestMarker = {
  ownerUserId: string;
  requestId: string;
  createdAt: string;
};

export async function beginPickerRequest(
  ownerUserId: string,
): Promise<PickerRequestMarker> {
  const marker = {
    ownerUserId,
    requestId: await createSubmissionUlid(),
    createdAt: new Date().toISOString(),
  };
  await SecureStore.setItemAsync(PICKER_MARKER_KEY, JSON.stringify(marker));
  return marker;
}

export async function readPickerRequestMarker(
  now = new Date(),
): Promise<PickerRequestMarker | null> {
  const raw = await SecureStore.getItemAsync(PICKER_MARKER_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<PickerRequestMarker>;
    const createdAt = typeof value.createdAt === "string" ? Date.parse(value.createdAt) : NaN;
    if (
      typeof value.ownerUserId !== "string" ||
      typeof value.requestId !== "string" ||
      value.requestId.length !== 26 ||
      !Number.isFinite(createdAt) ||
      now.getTime() - createdAt > PICKER_MARKER_TTL_MS
    ) {
      await SecureStore.deleteItemAsync(PICKER_MARKER_KEY);
      return null;
    }
    return {
      ownerUserId: value.ownerUserId,
      requestId: value.requestId,
      createdAt: value.createdAt!,
    };
  } catch {
    await SecureStore.deleteItemAsync(PICKER_MARKER_KEY);
    return null;
  }
}

export async function clearPickerRequest(
  expected?: Pick<PickerRequestMarker, "ownerUserId" | "requestId">,
): Promise<void> {
  if (expected) {
    const current = await readPickerRequestMarker();
    if (!current || !pickerMarkerMatches(current, expected.ownerUserId, expected.requestId)) {
      return;
    }
  }
  await SecureStore.deleteItemAsync(PICKER_MARKER_KEY);
}

export function pickerMarkerMatches(
  marker: PickerRequestMarker | null,
  ownerUserId: string | null,
  requestId: string,
): boolean {
  return (
    marker !== null &&
    ownerUserId !== null &&
    marker.ownerUserId === ownerUserId &&
    marker.requestId === requestId
  );
}
