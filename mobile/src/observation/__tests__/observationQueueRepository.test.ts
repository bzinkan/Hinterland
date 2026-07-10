import type { Observation } from "@/src/api/observations";
import type { QueuedObservation } from "@/src/observation/queueTypes";

const mockDelete = jest.fn();
const mockRunAsync = jest.fn(async () => ({ changes: 1 }));
const mockExecAsync = jest.fn(async () => undefined);
const mockGetAllAsync = jest.fn(async (sql: string) => {
  if (sql.includes("PRAGMA table_info")) {
    return [
      { name: "ecology_tags_json" },
      { name: "failure_stage" },
      { name: "last_failure_retryable" },
    ];
  }
  return [];
});
const mockGetFirstAsync = jest.fn();

jest.mock("expo-file-system", () => ({
  Directory: jest.fn().mockImplementation(() => ({
    exists: false,
    list: jest.fn(() => []),
  })),
  File: jest.fn().mockImplementation((uri) => ({
    uri: typeof uri === "string" ? uri : "file:///documents/private.jpg",
    exists: true,
    delete: mockDelete,
  })),
  Paths: { document: "file:///documents" },
}));

jest.mock("expo-sqlite", () => ({
  openDatabaseAsync: jest.fn(async () => {
    const database: any = {
    execAsync: mockExecAsync,
    getAllAsync: mockGetAllAsync,
    getFirstAsync: mockGetFirstAsync,
    runAsync: mockRunAsync,
      withExclusiveTransactionAsync: jest.fn(
        async (task: (transaction: unknown) => Promise<void>): Promise<void> => {
          await task(database);
        },
      ),
    };
    return database;
  }),
}));

import {
  finalizeObservationDraft,
  markQueueComplete,
} from "@/src/observation/observationQueue";

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

const record: QueuedObservation = {
  submissionKey: "01J00000000000000000000000",
  ownerUserId: "kid-1",
  localUri: "file:///documents/observation-queue/private.jpg",
  width: 1200,
  height: 900,
  byteCount: 123_456,
  sha256: "a".repeat(64),
  source: "camera",
  observedAt: "2026-07-09T12:00:00.000Z",
  geohash4: null,
  locationSource: "none",
  identification: { source: "unknown", taxonId: null, speciesName: null },
  placeName: null,
  ecologyTags: {},
  payloadFrozen: true,
  photoId: "photo-1",
  uploadUrl: null,
  uploadHeaders: null,
  observationId: null,
  observation: null,
  stage: "uploaded",
  attempts: 0,
  nextAttemptAt: null,
  lastErrorCode: null,
  lastRequestId: null,
  failureStage: null,
  lastFailureRetryable: null,
  createdAt: "2026-07-09T12:00:00.000Z",
  updatedAt: "2026-07-09T12:00:00.000Z",
};

function queueRow(overrides: Record<string, unknown> = {}) {
  return {
    submission_key: record.submissionKey,
    owner_user_id: record.ownerUserId,
    local_uri: record.localUri,
    width: record.width,
    height: record.height,
    byte_count: record.byteCount,
    sha256: record.sha256,
    capture_source: record.source,
    observed_at: record.observedAt,
    geohash4: null,
    location_source: "none",
    identification_source: "unknown",
    taxon_id: null,
    species_name: null,
    place_name: null,
    ecology_tags_json: "{}",
    payload_frozen: 1,
    photo_id: record.photoId,
    upload_url: null,
    upload_headers_json: null,
    observation_id: null,
    observation_json: null,
    stage: "ready",
    attempts: 0,
    next_attempt_at: null,
    last_error_code: null,
    last_request_id: null,
    failure_stage: null,
    last_failure_retryable: null,
    created_at: record.createdAt,
    updated_at: record.updatedAt,
    ...overrides,
  };
}

describe("Observation SQLite repository", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetAllAsync.mockImplementation(async (sql: string) => {
      if (sql.includes("PRAGMA table_info")) {
        return [
          { name: "ecology_tags_json" },
          { name: "failure_stage" },
          { name: "last_failure_retryable" },
        ];
      }
      return [];
    });
    mockGetFirstAsync.mockResolvedValue(queueRow({
      local_uri: "",
      observation_id: observation.id,
      observation_json: JSON.stringify(observation),
      stage: "complete",
    }));
  });

  it("atomically freezes the final payload before any network stage can begin", async () => {
    mockGetFirstAsync.mockResolvedValue(queueRow());

    const result = await finalizeObservationDraft(
      record.ownerUserId,
      record.submissionKey,
      {
        geohash4: null,
        locationSource: "none",
        identification: record.identification,
        placeName: null,
        ecologyTags: {},
      },
    );

    expect(mockRunAsync).toHaveBeenCalledWith(
      expect.stringMatching(/updated_at = \?, payload_frozen = 1/),
      null,
      "none",
      "unknown",
      null,
      null,
      null,
      "{}",
      expect.any(String),
      record.ownerUserId,
      record.submissionKey,
    );
    expect(result.payloadFrozen).toBe(true);
  });

  it("commits the response, deletes completed bytes, and retains reconciliation metadata", async () => {
    const result = await markQueueComplete(record, observation);

    expect(mockDelete).toHaveBeenCalledTimes(1);
    expect(mockRunAsync).toHaveBeenCalledWith(
      expect.stringContaining("stage = 'complete'"),
      observation.id,
      JSON.stringify(observation),
      expect.any(String),
      record.ownerUserId,
      record.submissionKey,
    );
    expect(mockRunAsync).toHaveBeenCalledWith(
      expect.stringContaining("SET local_uri = ''"),
      record.ownerUserId,
      record.submissionKey,
    );
    expect(result).toMatchObject({
      stage: "complete",
      localUri: "",
      byteCount: record.byteCount,
      sha256: record.sha256,
      observationId: observation.id,
      observation,
    });
  });
});
