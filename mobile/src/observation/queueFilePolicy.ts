export const QUEUE_ORPHAN_MIN_AGE_MS = 60_000;

export type QueueFileCandidate = {
  uri: string;
  modificationTime: number | null;
};

/** Fail-safe ownership/age policy for document-directory child photos. */
export function mayDeleteQueueOrphan(
  file: QueueFileCandidate,
  referencedUris: ReadonlySet<string>,
  activeSubmissionKeys: ReadonlySet<string>,
  nowMs: number,
  minimumAgeMs = QUEUE_ORPHAN_MIN_AGE_MS,
): boolean {
  if (referencedUris.has(file.uri)) return false;
  const submissionKey = submissionKeyFromQueueUri(file.uri);
  if (!submissionKey || activeSubmissionKeys.has(submissionKey)) return false;
  if (file.modificationTime === null) return false;
  return nowMs - file.modificationTime >= minimumAgeMs;
}

export function submissionKeyFromQueueUri(uri: string): string | null {
  const match = /\/([0-7][0-9A-HJKMNP-TV-Z]{25})\.jpg$/i.exec(uri);
  return match?.[1]?.toUpperCase() ?? null;
}
