import type {
  Observation,
  ObservationCreate,
  PhotoPresignResponse,
} from "@/src/api/observations";

export type ObservationQueueStage =
  | "ready"
  | "presigned"
  | "uploaded"
  | "complete"
  | "needs_attention"
  | "abandoned";

export type ObservationLocationSource =
  | "device_coarse"
  | "manual_coarse"
  | "none";

export type ObservationIdentification =
  | { source: "catalog"; taxonId: number; speciesName: string }
  | { source: "manual_text"; taxonId: null; speciesName: string }
  | { source: "unknown"; taxonId: null; speciesName: null };

export type QueuedObservation = {
  submissionKey: string;
  ownerUserId: string;
  localUri: string;
  width: number;
  height: number;
  byteCount: number;
  sha256: string;
  source: "camera" | "library";
  observedAt: string;
  geohash4: string | null;
  locationSource: ObservationLocationSource;
  identification: ObservationIdentification;
  placeName: string | null;
  ecologyTags: Record<string, string>;
  payloadFrozen: boolean;
  photoId: string | null;
  uploadUrl: string | null;
  uploadHeaders: Record<string, string> | null;
  observationId: string | null;
  observation: Observation | null;
  stage: ObservationQueueStage;
  attempts: number;
  nextAttemptAt: string | null;
  lastErrorCode: string | null;
  lastRequestId: string | null;
  failureStage: "ready" | "presigned" | "uploaded" | null;
  lastFailureRetryable: boolean | null;
  createdAt: string;
  updatedAt: string;
};

export type ObservationDraftInput = {
  submissionKey: string;
  ownerUserId: string;
  sourceUri: string;
  width: number;
  height: number;
  source: "camera" | "library";
  observedAt: string;
};

export function createPayload(
  record: Pick<
    QueuedObservation,
    | "photoId"
    | "observedAt"
    | "geohash4"
    | "locationSource"
    | "identification"
    | "placeName"
    | "ecologyTags"
  >,
): ObservationCreate {
  return {
    photo_id: requirePhotoId(record),
    observed_at: record.observedAt,
    geohash4: record.geohash4,
    location_source: record.locationSource,
    taxon_id: record.identification.taxonId,
    species_name: record.identification.speciesName,
    identification_source: record.identification.source,
    place_name: record.placeName,
    ecology_tags: record.ecologyTags,
  };
}

export function resolveUploadHeaders(
  response: PhotoPresignResponse,
): Record<string, string> {
  return {
    ...(response.upload_headers ??
      response.required_headers ?? {
        "Content-Type": response.content_type,
        "x-ms-blob-type": "BlockBlob",
      }),
  };
}

function requirePhotoId(record: Pick<QueuedObservation, "photoId">): string {
  if (!record.photoId) throw new Error("The queued observation has no photo id");
  return record.photoId;
}
