import type { Observation, PhotoPresignResponse } from "@/src/api/observations";
import { ApiError } from "@/src/api/client";
import { PhotoUploadError } from "@/src/api/upload";
import {
  queueFailureDetails,
  retryDelayMs,
  setObservationQueueOwner,
  syncQueuedObservation,
  type QueueSyncDependencies,
} from "@/src/observation/queueSync";
import type { QueuedObservation } from "@/src/observation/queueTypes";

function queued(stage: QueuedObservation["stage"]): QueuedObservation {
  return {
    submissionKey: `01JTEST${stage.padEnd(19, "0")}`.slice(0, 26),
    ownerUserId: "kid-1",
    localUri: "file:///observation.jpg",
    width: 1200,
    height: 900,
    byteCount: 100,
    sha256: "a".repeat(64),
    source: "camera",
    observedAt: "2026-07-09T12:00:00.000Z",
    geohash4: null,
    locationSource: "none",
    identification: { source: "unknown", taxonId: null, speciesName: null },
    placeName: null,
    ecologyTags: {},
    payloadFrozen: true,
    photoId: stage === "ready" ? null : "photo-1",
    uploadUrl: stage === "presigned" ? "https://example.blob.core.windows.net/p.jpg" : null,
    uploadHeaders: stage === "presigned" ? { "x-ms-blob-type": "BlockBlob" } : null,
    observationId: null,
    observation: null,
    stage,
    attempts: 0,
    nextAttemptAt: null,
    lastErrorCode: null,
    lastRequestId: null,
    failureStage: null,
    lastFailureRetryable: null,
    createdAt: "2026-07-09T12:00:00.000Z",
    updatedAt: "2026-07-09T12:00:00.000Z",
  };
}

const observation: Observation = {
  id: "observation-1",
  user_id: "kid-1",
  group_id: "group-1",
  photo_id: "photo-1",
  latitude: null,
  longitude: null,
  geohash4: null,
  observed_at: "2026-07-09T12:00:00.000Z",
  location_source: "none",
  taxon_id: null,
  species_name: null,
  identification_source: "unknown",
  identification_revision: 1,
  place_name: null,
  moderation_status: "pilot_private",
  dispatch_status: "complete",
  rewards: [],
};

function dependencies(overrides: Partial<QueueSyncDependencies> = {}): QueueSyncDependencies {
  return {
    presign: jest.fn(),
    upload: jest.fn(),
    create: jest.fn(),
    reconcile: jest.fn(),
    freeze: jest.fn(),
    setPresigned: jest.fn(),
    setUploaded: jest.fn(),
    setComplete: jest.fn(async (record, value) => ({
      ...record,
      stage: "complete",
      observationId: value.id,
      observation: value,
    })),
    resetPresign: jest.fn(),
    setFailure: jest.fn(async (record, failure) => ({
      ...record,
      stage: failure.retryable ? record.stage : "needs_attention",
      lastErrorCode: failure.errorCode,
      failureStage: record.stage as "ready" | "presigned" | "uploaded",
      lastFailureRetryable: failure.retryable,
    })),
    now: () => new Date("2026-07-09T12:00:00.000Z"),
    random: () => 0.5,
    ownerIsActive: () => true,
    ...overrides,
  } as QueueSyncDependencies;
}

describe("syncQueuedObservation", () => {
  it("replays a lost create response with the original submission key", async () => {
    const initial = queued("uploaded");
    const create = jest.fn(async () => observation);
    const result = await syncQueuedObservation(initial, dependencies({ create }));

    expect(create).toHaveBeenCalledWith(
      initial,
      initial.submissionKey,
      expect.any(AbortSignal),
    );
    expect(result.stage).toBe("complete");
    expect(result.observationId).toBe(observation.id);
  });

  it("refreshes an expired Azure SAS once without changing the submission key", async () => {
    const initial = queued("presigned");
    const ready = { ...initial, stage: "ready" as const, uploadUrl: null, uploadHeaders: null };
    const refreshed: PhotoPresignResponse = {
      photo_id: "photo-1",
      upload_url: "https://example.blob.core.windows.net/fresh.jpg",
      upload_headers: { "Content-Type": "image/jpeg", "x-ms-blob-type": "BlockBlob" },
      object_name: "pending/uploads/photo-1.jpg",
      bucket: "photos",
      content_type: "image/jpeg",
      expires_at: "2026-07-09T12:15:00.000Z",
    };
    const upload = jest
      .fn()
      .mockRejectedValueOnce(new PhotoUploadError(403, "expired"))
      .mockResolvedValueOnce(undefined);
    const deps = dependencies({
      upload,
      resetPresign: jest.fn(async () => ready),
      presign: jest.fn(async () => refreshed),
      setPresigned: jest.fn(async (record) => ({
        ...record,
        stage: "presigned" as const,
        photoId: "photo-1",
        uploadUrl: refreshed.upload_url,
        uploadHeaders: refreshed.upload_headers ?? null,
      })),
      setUploaded: jest.fn(async (record) => ({ ...record, stage: "uploaded" as const, uploadUrl: null })),
      create: jest.fn(async () => observation),
    });

    const result = await syncQueuedObservation(initial, deps);

    expect(deps.presign).toHaveBeenCalledWith(
      initial.submissionKey,
      expect.any(AbortSignal),
    );
    expect(upload).toHaveBeenCalledTimes(2);
    expect(result.stage).toBe("complete");
  });

  it("does not persist a network result after the active account changes", async () => {
    const initial = queued("uploaded");
    const ownerIsActive = jest.fn().mockReturnValueOnce(true).mockReturnValue(false);
    const setComplete = jest.fn();
    const result = await syncQueuedObservation(
      initial,
      dependencies({
        ownerIsActive,
        create: jest.fn(async () => observation),
        setComplete,
      }),
    );

    expect(result.stage).toBe("uploaded");
    expect(setComplete).not.toHaveBeenCalled();
  });

  it("aborts in-flight work when the canonical owner changes", async () => {
    const initial = queued("uploaded");
    setObservationQueueOwner(initial.ownerUserId);
    let sawAbort = false;
    const create = jest.fn(
      async (_record: QueuedObservation, _key: string, signal?: AbortSignal) =>
        new Promise<Observation>((_resolve, reject) => {
          signal?.addEventListener("abort", () => {
            sawAbort = true;
            const error = new Error("cancelled");
            error.name = "AbortError";
            reject(error);
          });
        }),
    );
    const promise = syncQueuedObservation(
      initial,
      dependencies({ create, ownerIsActive: () => true }),
    );

    await Promise.resolve();
    setObservationQueueOwner("kid-2");
    const result = await promise;

    expect(sawAbort).toBe(true);
    expect(result.stage).toBe("uploaded");
  });

  it.each(["ready", "presigned", "uploaded"] as const)(
    "persists a retryable fault at the exact %s stage",
    async (stage) => {
      const failure = new Error(`network failed during ${stage}`);
      const deps = dependencies({
        presign:
          stage === "ready"
            ? jest.fn(async () => {
                throw failure;
              })
            : jest.fn(),
        upload:
          stage === "presigned"
            ? jest.fn(async () => {
                throw failure;
              })
            : jest.fn(),
        create:
          stage === "uploaded"
            ? jest.fn(async () => {
                throw failure;
              })
            : jest.fn(),
      });

      const result = await syncQueuedObservation(queued(stage), deps);

      expect(deps.setFailure).toHaveBeenCalledWith(
        expect.objectContaining({ stage }),
        expect.objectContaining({ retryable: true }),
      );
      expect(result).toMatchObject({
        stage,
        failureStage: stage,
        lastFailureRetryable: true,
      });
    },
  );

  it("moves a non-retryable upload validation error to needs_attention", async () => {
    const result = await syncQueuedObservation(
      queued("presigned"),
      dependencies({
        upload: jest.fn(async () => {
          throw new PhotoUploadError(400, "invalid JPEG");
        }),
      }),
    );

    expect(result).toMatchObject({
      stage: "needs_attention",
      failureStage: "presigned",
      lastFailureRetryable: false,
    });
  });
});

describe("queue retry policy", () => {
  it("uses bounded exponential delay with deterministic jitter", () => {
    expect(retryDelayMs(1, 0.5)).toBe(1_000);
    expect(retryDelayMs(20, 0.5)).toBeLessThanOrEqual(5 * 60_000);
  });

  it("does not retry a validation response", () => {
    const failure = queueFailureDetails(new PhotoUploadError(400, "bad"), 0, {
      now: () => new Date("2026-07-09T12:00:00.000Z"),
      random: () => 0.5,
    });
    expect(failure.retryable).toBe(false);
    expect(failure.nextAttemptAt).toBeNull();
  });

  it("surfaces idempotency conflicts with the adult support request id", () => {
    const failure = queueFailureDetails(
      new ApiError(
        409,
        {
          error: {
            code: "idempotency_conflict",
            message: "payload mismatch",
            request_id: "request-123",
          },
        },
        "payload mismatch",
      ),
      0,
      {
        now: () => new Date("2026-07-09T12:00:00.000Z"),
        random: () => 0.5,
      },
    );

    expect(failure).toEqual({
      retryable: false,
      errorCode: "idempotency_conflict",
      requestId: "request-123",
      nextAttemptAt: null,
    });
  });
});
