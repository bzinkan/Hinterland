import * as Crypto from "expo-crypto";
import { Directory, File, Paths } from "expo-file-system";
import { Platform } from "react-native";
import * as SQLite from "expo-sqlite";

import type {
  ObservationDraftInput,
  ObservationIdentification,
  ObservationLocationSource,
  ObservationQueueStage,
  QueuedObservation,
} from "@/src/observation/queueTypes";
import { resolveUploadHeaders } from "@/src/observation/queueTypes";
import {
  OBSERVATION_QUEUE_CAP,
  ObservationQueueFullError,
  activeSubmissionQueueKeys,
  admitDeviceQueueEntry,
  exactRetryStage,
  persistFailureStage,
  withSubmissionQueueLock,
} from "@/src/observation/queuePolicy";
import {
  mayDeleteQueueOrphan,
  QUEUE_ORPHAN_MIN_AGE_MS,
} from "@/src/observation/queueFilePolicy";
import type { Observation, PhotoPresignResponse } from "@/src/api/observations";

const DB_NAME = "hinterland-observations.db";
const QUEUE_DIRECTORY = "observation-queue";
export { OBSERVATION_QUEUE_CAP, ObservationQueueFullError };
export const MAX_OBSERVATION_BYTES = 4_000_000;

type QueueRow = {
  submission_key: string;
  owner_user_id: string;
  local_uri: string;
  width: number;
  height: number;
  byte_count: number;
  sha256: string;
  capture_source: "camera" | "library";
  observed_at: string;
  geohash4: string | null;
  location_source: ObservationLocationSource;
  identification_source: "catalog" | "manual_text" | "unknown";
  taxon_id: number | null;
  species_name: string | null;
  place_name: string | null;
  ecology_tags_json: string;
  payload_frozen: number;
  photo_id: string | null;
  upload_url: string | null;
  upload_headers_json: string | null;
  observation_id: string | null;
  observation_json: string | null;
  stage: ObservationQueueStage;
  attempts: number;
  next_attempt_at: string | null;
  last_error_code: string | null;
  last_request_id: string | null;
  failure_stage: "ready" | "presigned" | "uploaded" | null;
  last_failure_retryable: number | null;
  created_at: string;
  updated_at: string;
};

let databasePromise: Promise<SQLite.SQLiteDatabase> | null = null;
const listeners = new Set<() => void>();

export class ObservationDraftFrozenError extends Error {
  constructor() {
    super("This observation is already being saved and cannot be changed.");
    this.name = "ObservationDraftFrozenError";
  }
}

export function subscribeObservationQueue(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notifyQueueChanged(): void {
  for (const listener of listeners) listener();
}

async function getDatabase(): Promise<SQLite.SQLiteDatabase> {
  if (Platform.OS === "web") {
    throw new Error("The durable Observation queue is available on iOS and Android only.");
  }
  if (!databasePromise) {
    databasePromise = SQLite.openDatabaseAsync(DB_NAME).then(async (database) => {
      await database.execAsync(`
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        CREATE TABLE IF NOT EXISTS observation_queue (
          submission_key TEXT PRIMARY KEY NOT NULL,
          owner_user_id TEXT NOT NULL,
          local_uri TEXT NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          byte_count INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          capture_source TEXT NOT NULL CHECK (capture_source IN ('camera', 'library')),
          observed_at TEXT NOT NULL,
          geohash4 TEXT,
          location_source TEXT NOT NULL CHECK (location_source IN ('device_coarse', 'manual_coarse', 'none')),
          identification_source TEXT NOT NULL CHECK (identification_source IN ('catalog', 'manual_text', 'unknown')),
          taxon_id INTEGER,
          species_name TEXT,
          place_name TEXT,
          ecology_tags_json TEXT NOT NULL DEFAULT '{}',
          payload_frozen INTEGER NOT NULL DEFAULT 0,
          photo_id TEXT,
          upload_url TEXT,
          upload_headers_json TEXT,
          observation_id TEXT,
          observation_json TEXT,
          stage TEXT NOT NULL CHECK (stage IN ('ready', 'presigned', 'uploaded', 'complete', 'needs_attention', 'abandoned')),
          attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TEXT,
          last_error_code TEXT,
          last_request_id TEXT,
          failure_stage TEXT,
          last_failure_retryable INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_observation_queue_owner_stage
          ON observation_queue (owner_user_id, stage, updated_at);
      `);
      const columns = await database.getAllAsync<{ name: string }>(
        "PRAGMA table_info(observation_queue)",
      );
      if (!columns.some((column) => column.name === "ecology_tags_json")) {
        await database.execAsync(
          "ALTER TABLE observation_queue ADD COLUMN ecology_tags_json TEXT NOT NULL DEFAULT '{}'",
        );
      }
      if (!columns.some((column) => column.name === "failure_stage")) {
        await database.execAsync(
          "ALTER TABLE observation_queue ADD COLUMN failure_stage TEXT",
        );
      }
      if (!columns.some((column) => column.name === "last_failure_retryable")) {
        await database.execAsync(
          "ALTER TABLE observation_queue ADD COLUMN last_failure_retryable INTEGER",
        );
      }
      await sweepCompletedLocalFilesWithDatabase(database);
      await reconcileQueueDirectoryOrphansWithDatabase(database);
      return database;
    });
  }
  return databasePromise;
}

export async function persistObservationDraft(
  input: ObservationDraftInput,
): Promise<QueuedObservation> {
  if (
    input.width < 50 ||
    input.height < 50 ||
    input.width > 1600 ||
    input.height > 1600
  ) {
    throw new Error("The photo dimensions are not supported. Try another photo.");
  }
  return withSubmissionQueueLock(input.submissionKey, () =>
    persistObservationDraftLocked(input),
  );
}

async function persistObservationDraftLocked(
  input: ObservationDraftInput,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  const replay = await database.getFirstAsync<QueueRow>(
    `SELECT * FROM observation_queue WHERE submission_key = ?`,
    input.submissionKey,
  );
  if (replay) return requireReplayOwner(replay, input.ownerUserId);

  const directory = new Directory(Paths.document, QUEUE_DIRECTORY);
  directory.create({ idempotent: true, intermediates: true });
  const source = new File(input.sourceUri);
  if (!source.exists) throw new Error("The captured photo is no longer available.");
  const destination = new File(directory, `${input.submissionKey}.jpg`);
  if (
    destination.exists &&
    !(await deleteLocalFileWhenUnowned(database, destination.uri))
  ) {
    throw new Error("The observation photo path is already owned by another draft.");
  }
  source.copy(destination);

  try {
    if (destination.size <= 0 || destination.size > MAX_OBSERVATION_BYTES) {
      throw new Error("The photo is too large to save. Try taking it again.");
    }
    const bytes = await destination.bytes();
    const sha256 = hex(
      await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes),
    );
    const now = new Date().toISOString();
    let concurrentReplay: QueueRow | null = null;
    const outcome = await admitDeviceQueueEntry(
      database,
      async (transaction) => {
        concurrentReplay = await transaction.getFirstAsync<QueueRow>(
          `SELECT * FROM observation_queue WHERE submission_key = ?`,
          input.submissionKey,
        );
        return concurrentReplay !== null;
      },
      async (transaction) => {
        await transaction.runAsync(
          `INSERT INTO observation_queue (
            submission_key, owner_user_id, local_uri, width, height, byte_count,
            sha256, capture_source, observed_at, geohash4, location_source,
            identification_source, taxon_id, species_name, place_name,
            ecology_tags_json,
            payload_frozen, stage, attempts, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'none', 'unknown', NULL,
                    NULL, NULL, '{}', 0, 'ready', 0, ?, ?)`,
          input.submissionKey,
          input.ownerUserId,
          destination.uri,
          input.width,
          input.height,
          destination.size,
          sha256,
          input.source,
          input.observedAt,
          now,
          now,
        );
      },
    );
    if (outcome === "existing" && concurrentReplay) {
      return requireReplayOwner(concurrentReplay, input.ownerUserId);
    }
  } catch (error) {
    await deleteLocalFileWhenUnowned(database, destination.uri);
    throw error;
  }
  notifyQueueChanged();
  return requireQueueItem(input.ownerUserId, input.submissionKey);
}

export async function getQueuedObservation(
  ownerUserId: string,
  submissionKey: string,
): Promise<QueuedObservation | null> {
  const database = await getDatabase();
  const row = await database.getFirstAsync<QueueRow>(
    `SELECT * FROM observation_queue WHERE owner_user_id = ? AND submission_key = ?`,
    ownerUserId,
    submissionKey,
  );
  return row ? fromRow(row) : null;
}

export async function listQueuedObservations(
  ownerUserId: string,
): Promise<QueuedObservation[]> {
  const database = await getDatabase();
  await sweepCompletedLocalFilesWithDatabase(database, ownerUserId);
  const rows = await database.getAllAsync<QueueRow>(
    `SELECT * FROM observation_queue
     WHERE owner_user_id = ? AND stage <> 'abandoned'
     ORDER BY created_at DESC`,
    ownerUserId,
  );
  return rows.map(fromRow);
}

export async function listFrozenPendingObservations(
  ownerUserId: string,
  now = new Date(),
): Promise<QueuedObservation[]> {
  const database = await getDatabase();
  await reconcileQueueDirectoryOrphansWithDatabase(database, now.getTime());
  const rows = await database.getAllAsync<QueueRow>(
    `SELECT * FROM observation_queue
     WHERE owner_user_id = ? AND payload_frozen = 1
       AND stage IN ('ready', 'presigned', 'uploaded')
       AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
     ORDER BY created_at ASC`,
    ownerUserId,
    now.toISOString(),
  );
  return rows.map(fromRow);
}

function requireReplayOwner(row: QueueRow, ownerUserId: string): QueuedObservation {
  if (row.owner_user_id !== ownerUserId) {
    throw new Error("This submission key belongs to another account.");
  }
  return fromRow(row);
}

export async function updateObservationDraft(
  ownerUserId: string,
  submissionKey: string,
  update: ObservationDraftUpdate,
): Promise<QueuedObservation> {
  return writeObservationDraft(ownerUserId, submissionKey, update, false);
}

/** Persist the final payload and submission intent in the same SQLite write. */
export async function finalizeObservationDraft(
  ownerUserId: string,
  submissionKey: string,
  update: ObservationDraftUpdate,
): Promise<QueuedObservation> {
  return writeObservationDraft(ownerUserId, submissionKey, update, true);
}

type ObservationDraftUpdate = {
  geohash4: string | null;
  locationSource: ObservationLocationSource;
  identification: ObservationIdentification;
  placeName?: string | null;
  ecologyTags?: Record<string, string>;
};

async function writeObservationDraft(
  ownerUserId: string,
  submissionKey: string,
  update: ObservationDraftUpdate,
  freeze: boolean,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  const result = await database.runAsync(
    `UPDATE observation_queue
     SET geohash4 = ?, location_source = ?, identification_source = ?,
          taxon_id = ?, species_name = ?, place_name = ?, ecology_tags_json = ?,
          updated_at = ?${freeze ? ", payload_frozen = 1" : ""}
     WHERE owner_user_id = ? AND submission_key = ?
       AND payload_frozen = 0 AND stage = 'ready'`,
    update.geohash4,
    update.locationSource,
    update.identification.source,
    update.identification.taxonId,
    update.identification.speciesName,
    update.placeName ?? null,
    JSON.stringify(update.ecologyTags ?? {}),
    new Date().toISOString(),
    ownerUserId,
    submissionKey,
  );
  if (result.changes !== 1) throw new ObservationDraftFrozenError();
  notifyQueueChanged();
  return requireQueueItem(ownerUserId, submissionKey);
}

export async function freezeObservationDraft(
  ownerUserId: string,
  submissionKey: string,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  await database.runAsync(
    `UPDATE observation_queue SET payload_frozen = 1, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ? AND stage = 'ready'`,
    new Date().toISOString(),
    ownerUserId,
    submissionKey,
  );
  notifyQueueChanged();
  return requireQueueItem(ownerUserId, submissionKey);
}

export async function markQueuePresigned(
  record: QueuedObservation,
  response: PhotoPresignResponse,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  await database.runAsync(
    `UPDATE observation_queue
     SET photo_id = ?, upload_url = ?, upload_headers_json = ?,
         observation_id = COALESCE(?, observation_id),
         stage = ?, attempts = 0, next_attempt_at = NULL,
         last_error_code = NULL, last_request_id = NULL,
         failure_stage = NULL, last_failure_retryable = NULL, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ? AND stage = 'ready'`,
    response.photo_id,
    response.upload_url,
    JSON.stringify(resolveUploadHeaders(response)),
    response.observation_id ?? null,
    "presigned",
    new Date().toISOString(),
    record.ownerUserId,
    record.submissionKey,
  );
  notifyQueueChanged();
  return requireQueueItem(record.ownerUserId, record.submissionKey);
}

export async function markQueueUploaded(
  record: QueuedObservation,
): Promise<QueuedObservation> {
  return updateStage(record, "uploaded", {
    uploadUrl: null,
    uploadHeadersJson: null,
  });
}

export async function resetQueueForFreshPresign(
  record: QueuedObservation,
): Promise<QueuedObservation> {
  return updateStage(record, "ready", {
    uploadUrl: null,
    uploadHeadersJson: null,
  });
}

export async function markQueueComplete(
  record: QueuedObservation,
  observation: Observation,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  await database.runAsync(
    `UPDATE observation_queue
     SET observation_id = ?, observation_json = ?, stage = 'complete',
         attempts = 0, next_attempt_at = NULL, last_error_code = NULL,
         last_request_id = NULL, failure_stage = NULL,
         last_failure_retryable = NULL, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ?`,
    observation.id,
    JSON.stringify(observation),
    new Date().toISOString(),
    record.ownerUserId,
    record.submissionKey,
  );
  if (deleteLocalFile(record.localUri)) {
    await database.runAsync(
      `UPDATE observation_queue SET local_uri = ''
       WHERE owner_user_id = ? AND submission_key = ? AND stage = 'complete'`,
      record.ownerUserId,
      record.submissionKey,
    );
  }
  notifyQueueChanged();
  return requireQueueItem(record.ownerUserId, record.submissionKey);
}

export async function recordQueueFailure(
  record: QueuedObservation,
  details: {
    retryable: boolean;
    errorCode: string;
    requestId: string | null;
    nextAttemptAt: string | null;
  },
): Promise<QueuedObservation> {
  const database = await getDatabase();
  const transition = persistFailureStage(record.stage, details.retryable);
  await database.runAsync(
    `UPDATE observation_queue
     SET stage = ?, attempts = attempts + 1, next_attempt_at = ?,
         last_error_code = ?, last_request_id = ?, failure_stage = ?,
         last_failure_retryable = ?, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ?`,
    transition.persistedStage,
    details.nextAttemptAt,
    details.errorCode,
    details.requestId,
    transition.failureStage,
    transition.lastFailureRetryable ? 1 : 0,
    new Date().toISOString(),
    record.ownerUserId,
    record.submissionKey,
  );
  notifyQueueChanged();
  return requireQueueItem(record.ownerUserId, record.submissionKey);
}

export async function retryQueueItem(
  ownerUserId: string,
  submissionKey: string,
): Promise<QueuedObservation> {
  const database = await getDatabase();
  const current = await requireQueueItem(ownerUserId, submissionKey);
  const stage = exactRetryStage(
    current.failureStage,
    current.lastFailureRetryable,
  );
  await database.runAsync(
    `UPDATE observation_queue
     SET stage = ?, next_attempt_at = NULL, last_error_code = NULL,
         last_request_id = NULL, failure_stage = NULL,
         last_failure_retryable = NULL, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ?`,
    stage,
    new Date().toISOString(),
    ownerUserId,
    submissionKey,
  );
  notifyQueueChanged();
  return requireQueueItem(ownerUserId, submissionKey);
}

export async function discardQueuedObservation(
  ownerUserId: string,
  submissionKey: string,
): Promise<void> {
  const database = await getDatabase();
  const record = await requireQueueItem(ownerUserId, submissionKey);
  if (record.stage !== "ready" || record.payloadFrozen) {
    await database.runAsync(
      `UPDATE observation_queue SET stage = 'abandoned', updated_at = ?
       WHERE owner_user_id = ? AND submission_key = ?`,
      new Date().toISOString(),
      ownerUserId,
      submissionKey,
    );
  } else {
    await database.runAsync(
      `DELETE FROM observation_queue WHERE owner_user_id = ? AND submission_key = ?`,
      ownerUserId,
      submissionKey,
    );
    deleteLocalFile(record.localUri);
  }
  notifyQueueChanged();
}

export async function acknowledgeQueueCompletion(
  ownerUserId: string,
  submissionKey: string,
): Promise<void> {
  const database = await getDatabase();
  const record = await requireQueueItem(ownerUserId, submissionKey);
  if (record.stage !== "complete") return;
  await database.runAsync(
    `DELETE FROM observation_queue WHERE owner_user_id = ? AND submission_key = ?`,
    ownerUserId,
    submissionKey,
  );
  deleteLocalFile(record.localUri);
  notifyQueueChanged();
}

/** Remove a local row only after the server confirms an unattached abandon. */
export async function removeQueuedObservation(
  ownerUserId: string,
  submissionKey: string,
): Promise<void> {
  const database = await getDatabase();
  const record = await requireQueueItem(ownerUserId, submissionKey);
  await database.runAsync(
    `DELETE FROM observation_queue WHERE owner_user_id = ? AND submission_key = ?`,
    ownerUserId,
    submissionKey,
  );
  deleteLocalFile(record.localUri);
  notifyQueueChanged();
}

/** Remove only one canonical owner's local rows/files after deletion is accepted. */
export async function purgeOwnerObservationQueue(ownerUserId: string): Promise<void> {
  const database = await getDatabase();
  const rows = await database.getAllAsync<QueueRow>(
    `SELECT * FROM observation_queue WHERE owner_user_id = ?`,
    ownerUserId,
  );
  await database.runAsync(
    `DELETE FROM observation_queue WHERE owner_user_id = ?`,
    ownerUserId,
  );
  for (const row of rows) deleteLocalFile(row.local_uri);
  notifyQueueChanged();
}

async function updateStage(
  record: QueuedObservation,
  stage: ObservationQueueStage,
  clear: { uploadUrl: null; uploadHeadersJson: null },
): Promise<QueuedObservation> {
  const database = await getDatabase();
  await database.runAsync(
    `UPDATE observation_queue SET stage = ?, upload_url = ?,
       upload_headers_json = ?, next_attempt_at = NULL,
       last_error_code = NULL, last_request_id = NULL,
       failure_stage = NULL, last_failure_retryable = NULL, updated_at = ?
     WHERE owner_user_id = ? AND submission_key = ?`,
    stage,
    clear.uploadUrl,
    clear.uploadHeadersJson,
    new Date().toISOString(),
    record.ownerUserId,
    record.submissionKey,
  );
  notifyQueueChanged();
  return requireQueueItem(record.ownerUserId, record.submissionKey);
}

async function requireQueueItem(
  ownerUserId: string,
  submissionKey: string,
): Promise<QueuedObservation> {
  const record = await getQueuedObservation(ownerUserId, submissionKey);
  if (!record) throw new Error("The saved observation could not be found.");
  return record;
}

function fromRow(row: QueueRow): QueuedObservation {
  const identification: ObservationIdentification =
    row.identification_source === "catalog" && row.taxon_id != null
      ? {
          source: "catalog",
          taxonId: row.taxon_id,
          speciesName: row.species_name ?? "Unknown taxon",
        }
      : row.identification_source === "manual_text" && row.species_name
        ? { source: "manual_text", taxonId: null, speciesName: row.species_name }
        : { source: "unknown", taxonId: null, speciesName: null };
  return {
    submissionKey: row.submission_key,
    ownerUserId: row.owner_user_id,
    localUri: row.local_uri,
    width: row.width,
    height: row.height,
    byteCount: row.byte_count,
    sha256: row.sha256,
    source: row.capture_source,
    observedAt: row.observed_at,
    geohash4: row.geohash4,
    locationSource: row.location_source,
    identification,
    placeName: row.place_name,
    ecologyTags: parseRecord(row.ecology_tags_json) ?? {},
    payloadFrozen: row.payload_frozen === 1,
    photoId: row.photo_id,
    uploadUrl: row.upload_url,
    uploadHeaders: parseRecord(row.upload_headers_json),
    observationId: row.observation_id,
    observation: parseObservation(row.observation_json),
    stage: row.stage,
    attempts: row.attempts,
    nextAttemptAt: row.next_attempt_at,
    lastErrorCode: row.last_error_code,
    lastRequestId: row.last_request_id,
    failureStage: row.failure_stage,
    lastFailureRetryable:
      row.last_failure_retryable === null
        ? null
        : row.last_failure_retryable === 1,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function parseRecord(value: string | null): Record<string, string> | null {
  if (!value) return null;
  try {
    return JSON.parse(value) as Record<string, string>;
  } catch {
    return null;
  }
}

function parseObservation(value: string | null): Observation | null {
  if (!value) return null;
  try {
    return JSON.parse(value) as Observation;
  } catch {
    return null;
  }
}

function deleteLocalFile(uri: string): boolean {
  if (!uri) return true;
  try {
    const file = new File(uri);
    if (file.exists) file.delete();
    return true;
  } catch {
    // The database row is authoritative; a later OS cleanup can remove a
    // file whose URI is no longer accessible.
    return false;
  }
}

export async function sweepCompletedLocalFiles(
  ownerUserId?: string,
): Promise<void> {
  const database = await getDatabase();
  await sweepCompletedLocalFilesWithDatabase(database, ownerUserId);
  notifyQueueChanged();
}

async function sweepCompletedLocalFilesWithDatabase(
  database: SQLite.SQLiteDatabase,
  ownerUserId?: string,
): Promise<void> {
  const rows = await database.getAllAsync<{
    owner_user_id: string;
    submission_key: string;
    local_uri: string;
  }>(
    `SELECT owner_user_id, submission_key, local_uri
     FROM observation_queue
     WHERE stage = 'complete' AND local_uri <> ''
       ${ownerUserId ? "AND owner_user_id = ?" : ""}`,
    ...(ownerUserId ? [ownerUserId] : []),
  );
  for (const row of rows) {
    if (!deleteLocalFile(row.local_uri)) continue;
    await database.runAsync(
      `UPDATE observation_queue SET local_uri = ''
       WHERE owner_user_id = ? AND submission_key = ? AND stage = 'complete'`,
      row.owner_user_id,
      row.submission_key,
    );
  }
}

async function deleteLocalFileWhenUnowned(
  database: Pick<SQLite.SQLiteDatabase, "getFirstAsync">,
  uri: string,
): Promise<boolean> {
  try {
    const owner = await database.getFirstAsync<{ submission_key: string }>(
      `SELECT submission_key FROM observation_queue WHERE local_uri = ? LIMIT 1`,
      uri,
    );
    if (owner) return false;
    return deleteLocalFile(uri);
  } catch {
    // Fail closed: a database read failure must never delete a potentially
    // referenced child photo.
    return false;
  }
}

async function reconcileQueueDirectoryOrphansWithDatabase(
  database: SQLite.SQLiteDatabase,
  nowMs = Date.now(),
): Promise<void> {
  const directory = new Directory(Paths.document, QUEUE_DIRECTORY);
  if (!directory.exists) return;
  const referenced = await database.getAllAsync<{ local_uri: string }>(
    `SELECT local_uri FROM observation_queue WHERE local_uri <> ''`,
  );
  const referencedUris = new Set(referenced.map((row) => row.local_uri));
  const activeKeys = activeSubmissionQueueKeys();
  for (const entry of directory.list()) {
    if (!(entry instanceof File)) continue;
    if (
      mayDeleteQueueOrphan(
        { uri: entry.uri, modificationTime: entry.modificationTime },
        referencedUris,
        activeKeys,
        nowMs,
        QUEUE_ORPHAN_MIN_AGE_MS,
      )
    ) {
      deleteLocalFile(entry.uri);
    }
  }
}

function hex(value: ArrayBuffer): string {
  return Array.from(new Uint8Array(value), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
