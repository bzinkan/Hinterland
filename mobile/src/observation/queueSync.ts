import NetInfo from "@react-native-community/netinfo";

import { ApiError } from "@/src/api/client";
import {
  createObservation,
  getObservation,
  presignPhoto,
  type Observation,
  type PhotoPresignResponse,
} from "@/src/api/observations";
import { PhotoUploadError, putPhotoToSignedUrl } from "@/src/api/upload";
import {
  freezeObservationDraft,
  listFrozenPendingObservations,
  markQueueComplete,
  markQueuePresigned,
  markQueueUploaded,
  recordQueueFailure,
  resetQueueForFreshPresign,
} from "@/src/observation/observationQueue";
import { createPayload, type QueuedObservation } from "@/src/observation/queueTypes";

const running = new Map<string, Promise<QueuedObservation>>();
const activeControllers = new Set<AbortController>();
let activeOwnerUserId: string | null = null;

/** Establish a hard account boundary and cancel work from the prior token. */
export function setObservationQueueOwner(ownerUserId: string | null): void {
  if (activeOwnerUserId === ownerUserId) return;
  activeOwnerUserId = ownerUserId;
  for (const controller of activeControllers) controller.abort();
  activeControllers.clear();
}

export type QueueSyncDependencies = {
  presign: (key: string, signal?: AbortSignal) => Promise<PhotoPresignResponse>;
  upload: (
    url: string,
    localUri: string,
    headers: Readonly<Record<string, string>>,
    signal?: AbortSignal,
  ) => Promise<void>;
  create: (
    record: QueuedObservation,
    key: string,
    signal?: AbortSignal,
  ) => Promise<Observation>;
  reconcile: (observationId: string, signal?: AbortSignal) => Promise<Observation>;
  freeze: typeof freezeObservationDraft;
  setPresigned: typeof markQueuePresigned;
  setUploaded: typeof markQueueUploaded;
  setComplete: typeof markQueueComplete;
  resetPresign: typeof resetQueueForFreshPresign;
  setFailure: typeof recordQueueFailure;
  now: () => Date;
  random: () => number;
  ownerIsActive: (ownerUserId: string) => boolean;
};

const defaultDependencies: QueueSyncDependencies = {
  presign: presignPhoto,
  upload: putPhotoToSignedUrl,
  create: (record, key, signal) =>
    createObservation(createPayload(record), key, signal),
  reconcile: getObservation,
  freeze: freezeObservationDraft,
  setPresigned: markQueuePresigned,
  setUploaded: markQueueUploaded,
  setComplete: markQueueComplete,
  resetPresign: resetQueueForFreshPresign,
  setFailure: recordQueueFailure,
  now: () => new Date(),
  random: Math.random,
  ownerIsActive: (ownerUserId) => activeOwnerUserId === ownerUserId,
};

/**
 * Advance one frozen queue entry. Every stage is durable before the next
 * network call, so process death resumes without minting another resource.
 */
export function syncQueuedObservation(
  initial: QueuedObservation,
  dependencies: QueueSyncDependencies = defaultDependencies,
): Promise<QueuedObservation> {
  const runKey = `${initial.ownerUserId}:${initial.submissionKey}`;
  const existing = running.get(runKey);
  if (existing) return existing;

  const controller = new AbortController();
  activeControllers.add(controller);
  const promise = runStages(initial, dependencies, controller.signal).finally(() => {
    running.delete(runKey);
    activeControllers.delete(controller);
  });
  running.set(runKey, promise);
  return promise;
}

async function runStages(
  initial: QueuedObservation,
  dependencies: QueueSyncDependencies,
  signal: AbortSignal,
): Promise<QueuedObservation> {
  let record = initial;
  let refreshedExpiredUpload = false;

  try {
    if (!record.payloadFrozen) {
      record = await dependencies.freeze(record.ownerUserId, record.submissionKey);
    }
    while (true) {
      if (signal.aborted || !dependencies.ownerIsActive(record.ownerUserId)) {
        return record;
      }
      if (
        record.stage === "complete" ||
        record.stage === "needs_attention" ||
        record.stage === "abandoned"
      ) {
        return record;
      }

      if (record.stage === "ready") {
        const response = await dependencies.presign(record.submissionKey, signal);
        if (signal.aborted || !dependencies.ownerIsActive(record.ownerUserId)) {
          return record;
        }
        record = await dependencies.setPresigned(record, response);
        if (response.observation_id && !response.upload_url) {
          const observation = await dependencies.reconcile(
            response.observation_id,
            signal,
          );
          if (signal.aborted || !dependencies.ownerIsActive(record.ownerUserId)) {
            return record;
          }
          return dependencies.setComplete(record, observation);
        }
        if (!response.upload_url) {
          throw new Error("The upload reservation did not include an upload URL.");
        }
        continue;
      }

      if (record.stage === "presigned") {
        if (!record.uploadUrl || !record.uploadHeaders) {
          record = await dependencies.resetPresign(record);
          continue;
        }
        try {
          await dependencies.upload(
            record.uploadUrl,
            record.localUri,
            record.uploadHeaders,
            signal,
          );
        } catch (error) {
          if (isAbortError(error)) return record;
          if (
            error instanceof PhotoUploadError &&
            (error.status === 401 || error.status === 403) &&
            !refreshedExpiredUpload
          ) {
            refreshedExpiredUpload = true;
            record = await dependencies.resetPresign(record);
            continue;
          }
          throw error;
        }
        if (signal.aborted || !dependencies.ownerIsActive(record.ownerUserId)) {
          return record;
        }
        record = await dependencies.setUploaded(record);
        continue;
      }

      if (record.stage === "uploaded") {
        const observation = await dependencies.create(
          record,
          record.submissionKey,
          signal,
        );
        if (signal.aborted || !dependencies.ownerIsActive(record.ownerUserId)) {
          return record;
        }
        return dependencies.setComplete(record, observation);
      }

      return record;
    }
  } catch (error) {
    if (signal.aborted || isAbortError(error)) return record;
    const failure = queueFailureDetails(error, record.attempts, dependencies);
    return dependencies.setFailure(record, failure);
  }
}

/** Resume committed work only; untouched drafts remain local and editable. */
export async function resumeOwnerObservationQueue(
  ownerUserId: string,
): Promise<QueuedObservation[]> {
  const network = await NetInfo.fetch();
  if (network.isConnected === false || network.isInternetReachable === false) {
    return [];
  }
  const records = await listFrozenPendingObservations(ownerUserId);
  const results: QueuedObservation[] = [];
  // Sequential uploads avoid memory spikes on low-end W1 Android devices.
  for (const record of records) {
    if (activeOwnerUserId !== ownerUserId) break;
    results.push(await syncQueuedObservation(record));
  }
  return results;
}

export function queueFailureDetails(
  error: unknown,
  previousAttempts: number,
  dependencies: Pick<QueueSyncDependencies, "now" | "random"> = defaultDependencies,
): {
  retryable: boolean;
  errorCode: string;
  requestId: string | null;
  nextAttemptAt: string | null;
} {
  const status =
    error instanceof ApiError || error instanceof PhotoUploadError
      ? error.status
      : null;
  const retryable =
    status === 408 ||
    status === 429 ||
    (status != null && status >= 500) ||
    status == null;
  const requestId =
    error instanceof ApiError ? (error.body?.error.request_id ?? null) : null;
  const code =
    error instanceof ApiError
      ? (error.body?.error.code ?? `http_${error.status}`)
      : error instanceof PhotoUploadError
        ? `upload_http_${error.status}`
        : "network_unavailable";
  const delayMs = retryable
    ? retryDelayMs(previousAttempts + 1, dependencies.random())
    : null;
  return {
    retryable,
    errorCode: code,
    requestId,
    nextAttemptAt:
      delayMs == null
        ? null
        : new Date(dependencies.now().getTime() + delayMs).toISOString(),
  };
}

export function retryDelayMs(attempt: number, randomValue: number): number {
  const boundedAttempt = Math.max(1, Math.min(attempt, 8));
  const base = Math.min(5 * 60_000, 1_000 * 2 ** (boundedAttempt - 1));
  const jitter = 0.75 + Math.max(0, Math.min(randomValue, 1)) * 0.5;
  return Math.round(base * jitter);
}

export function kidSafeQueueMessage(record: QueuedObservation): string {
  if (record.stage !== "needs_attention") {
    return "Your observation is safe on this device and will try again.";
  }
  switch (record.lastErrorCode) {
    case "idempotency_conflict":
      return "This saved draft no longer matches the server copy. Ask an adult for help.";
    case "photo_too_large":
    case "invalid_photo":
      return "This photo could not be saved. Try taking a new photo.";
    default:
      return "This observation needs an adult to check it before retrying.";
  }
}

function isAbortError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "AbortError" ||
      error.name === "ImperativeRequestSupersededError")
  );
}
