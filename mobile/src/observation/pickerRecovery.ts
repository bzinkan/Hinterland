import * as SecureStore from "expo-secure-store";

import { createSubmissionUlid } from "@/src/observation/ulid";

const PICKER_MARKER_KEY = "hinterland.observation_picker_request.v1";
const PICKER_MARKER_TTL_MS = 30 * 60_000;
const DEFAULT_PENDING_RESULT_DELAYS_MS = [
  200,
  200,
  200,
  200,
  200,
  200,
  200,
  200,
  200,
] as const;

export type PickerRequestMarker = {
  ownerUserId: string;
  requestId: string;
  createdAt: string;
};

type PendingPickerResultPollOptions = {
  delaysMs?: readonly number[];
  wait?: (delayMs: number) => Promise<void>;
  isCancelled?: () => boolean;
};

async function waitFor(delayMs: number): Promise<void> {
  await new Promise<void>((resolve) => setTimeout(resolve, delayMs));
}

/**
 * Polls Android's process-death picker result while the exact persisted
 * owner/request pair remains current. One read is made immediately, followed
 * by one read after each configured delay.
 */
export async function pollPendingPickerResult<T>(
  marker: PickerRequestMarker,
  readPendingResult: () => Promise<T | null>,
  requestIsStillCurrent: (
    marker: PickerRequestMarker,
  ) => boolean | Promise<boolean>,
  options: PendingPickerResultPollOptions = {},
): Promise<T | null> {
  const delaysMs = options.delaysMs ?? DEFAULT_PENDING_RESULT_DELAYS_MS;
  const wait = options.wait ?? waitFor;
  const isCancelled = options.isCancelled ?? (() => false);

  const mayContinue = async () =>
    !isCancelled() && (await requestIsStillCurrent(marker));

  for (let attempt = 0; attempt <= delaysMs.length; attempt += 1) {
    if (!(await mayContinue())) return null;

    const result = await readPendingResult();
    // Android's native slot is one-shot. Once a read that began under the
    // exact owner/request returns a value, hand it back for durable storage
    // under that snapshot even if lifecycle/account state changed meanwhile.
    // Presentation remains separately gated by the active owner.
    if (result !== null) return result;

    // Null is not consumptive, so stop before another read when the request is
    // no longer current or the owning effect has been cancelled.
    if (!(await mayContinue())) return null;

    if (attempt === delaysMs.length) break;
    await wait(delaysMs[attempt]);
  }

  return null;
}

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
