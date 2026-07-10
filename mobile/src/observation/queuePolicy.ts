import type { ObservationQueueStage } from "@/src/observation/queueTypes";

export const OBSERVATION_QUEUE_CAP = 50;

type CountDatabase = {
  getFirstAsync<T>(sql: string, ...params: any[]): Promise<T | null>;
};

type ExclusiveDatabase<TTransaction extends CountDatabase> = {
  withExclusiveTransactionAsync(
    task: (transaction: TTransaction) => Promise<void>,
  ): Promise<void>;
};

export type RetryableQueueStage = "ready" | "presigned" | "uploaded";

export class ObservationQueueFullError extends Error {
  constructor() {
    super(`This device can hold at most ${OBSERVATION_QUEUE_CAP} waiting observations.`);
    this.name = "ObservationQueueFullError";
  }
}

export class ObservationRetryNotAllowedError extends Error {
  constructor() {
    super("This observation needs attention and cannot be retried unchanged.");
    this.name = "ObservationRetryNotAllowedError";
  }
}

/** The cap is intentionally device-wide; there is no owner predicate. */
export async function assertDeviceQueueCapacity(
  database: CountDatabase,
): Promise<void> {
  const active = await database.getFirstAsync<{ count: number }>(
    `SELECT COUNT(*) AS count FROM observation_queue
     WHERE stage <> 'abandoned'`,
  );
  if ((active?.count ?? 0) >= OBSERVATION_QUEUE_CAP) {
    throw new ObservationQueueFullError();
  }
}

/** Serialize count + existence check + insert on SQLite's exclusive handle. */
export async function admitDeviceQueueEntry<TTransaction extends CountDatabase>(
  database: ExclusiveDatabase<TTransaction>,
  exists: (transaction: TTransaction) => Promise<boolean>,
  insert: (transaction: TTransaction) => Promise<void>,
): Promise<"existing" | "inserted"> {
  let outcome: "existing" | "inserted" = "inserted";
  await database.withExclusiveTransactionAsync(async (transaction) => {
    if (await exists(transaction)) {
      outcome = "existing";
      return;
    }
    await assertDeviceQueueCapacity(transaction);
    await insert(transaction);
  });
  return outcome;
}

const submissionLocks = new Map<string, Promise<void>>();

export function activeSubmissionQueueKeys(): ReadonlySet<string> {
  return new Set(submissionLocks.keys());
}

/** Prevent two picker callbacks for one ULID from sharing/deleting one file. */
export async function withSubmissionQueueLock<T>(
  submissionKey: string,
  task: () => Promise<T>,
): Promise<T> {
  const previous = submissionLocks.get(submissionKey) ?? Promise.resolve();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const queued = previous.then(() => gate);
  submissionLocks.set(submissionKey, queued);
  await previous;
  try {
    return await task();
  } finally {
    release();
    if (submissionLocks.get(submissionKey) === queued) {
      submissionLocks.delete(submissionKey);
    }
  }
}

export function persistFailureStage(
  stage: ObservationQueueStage,
  retryable: boolean,
): {
  persistedStage: ObservationQueueStage;
  failureStage: RetryableQueueStage;
  lastFailureRetryable: boolean;
} {
  if (stage !== "ready" && stage !== "presigned" && stage !== "uploaded") {
    throw new Error(`Queue failure cannot be recorded from ${stage}`);
  }
  return {
    persistedStage: retryable ? stage : "needs_attention",
    failureStage: stage,
    lastFailureRetryable: retryable,
  };
}

export function exactRetryStage(
  failureStage: RetryableQueueStage | null,
  lastFailureRetryable: boolean | null,
): RetryableQueueStage {
  if (!lastFailureRetryable || failureStage === null) {
    throw new ObservationRetryNotAllowedError();
  }
  return failureStage;
}

export function mayRenderLocalPhoto(record: {
  payloadFrozen: boolean;
  stage: ObservationQueueStage;
  localUri: string;
}): boolean {
  return !record.payloadFrozen && record.stage === "ready" && record.localUri.length > 0;
}
