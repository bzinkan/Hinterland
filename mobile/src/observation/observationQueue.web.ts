/** Web adult-console shim: the durable child submission queue is native-only. */
import type { Observation, PhotoPresignResponse } from "@/src/api/observations";
import type {
  ObservationDraftInput,
  ObservationIdentification,
  ObservationLocationSource,
  QueuedObservation,
} from "@/src/observation/queueTypes";

export const OBSERVATION_QUEUE_CAP = 50;
export const MAX_OBSERVATION_BYTES = 4_000_000;

export class ObservationQueueFullError extends Error {}
export class ObservationDraftFrozenError extends Error {}

export function subscribeObservationQueue(_listener: () => void): () => void {
  return () => undefined;
}

export async function listQueuedObservations(
  _ownerUserId: string,
): Promise<QueuedObservation[]> {
  return [];
}

export async function listFrozenPendingObservations(
  _ownerUserId: string,
  _now?: Date,
): Promise<QueuedObservation[]> {
  return [];
}

export async function getQueuedObservation(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<QueuedObservation | null> {
  return null;
}

export async function purgeOwnerObservationQueue(_ownerUserId: string): Promise<void> {}

export async function sweepCompletedLocalFiles(_ownerUserId?: string): Promise<void> {}

export async function persistObservationDraft(
  _input: ObservationDraftInput,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function updateObservationDraft(
  _ownerUserId: string,
  _submissionKey: string,
  _update: {
    geohash4: string | null;
    locationSource: ObservationLocationSource;
    identification: ObservationIdentification;
    placeName?: string | null;
    ecologyTags?: Record<string, string>;
  },
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function finalizeObservationDraft(
  _ownerUserId: string,
  _submissionKey: string,
  _update: {
    geohash4: string | null;
    locationSource: ObservationLocationSource;
    identification: ObservationIdentification;
    placeName?: string | null;
    ecologyTags?: Record<string, string>;
  },
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function freezeObservationDraft(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function markQueuePresigned(
  _record: QueuedObservation,
  _response: PhotoPresignResponse,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function markQueueUploaded(
  _record: QueuedObservation,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function resetQueueForFreshPresign(
  _record: QueuedObservation,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function markQueueComplete(
  _record: QueuedObservation,
  _observation: Observation,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function recordQueueFailure(
  _record: QueuedObservation,
  _details: {
    retryable: boolean;
    errorCode: string;
    requestId: string | null;
    nextAttemptAt: string | null;
  },
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function retryQueueItem(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<QueuedObservation> {
  throw unsupported();
}

export async function discardQueuedObservation(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<void> {
  throw unsupported();
}

export async function acknowledgeQueueCompletion(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<void> {
  throw unsupported();
}

export async function removeQueuedObservation(
  _ownerUserId: string,
  _submissionKey: string,
): Promise<void> {
  throw unsupported();
}

function unsupported(): Error {
  return new Error("The durable Observation queue is available on iOS and Android only.");
}
