export type ObservationWorkIdentity = {
  ownerUserId: string | null;
  submissionKey: string | null;
  generation: number;
  geohash4?: string | null;
};

/** Pure guard used before every state write from slow device/network work. */
export function observationWorkIsCurrent(
  expected: ObservationWorkIdentity,
  current: ObservationWorkIdentity,
  signal?: AbortSignal,
): boolean {
  return (
    signal?.aborted !== true &&
    expected.ownerUserId !== null &&
    expected.ownerUserId === current.ownerUserId &&
    expected.submissionKey !== null &&
    expected.submissionKey === current.submissionKey &&
    expected.generation === current.generation &&
    (expected.geohash4 === undefined || expected.geohash4 === current.geohash4)
  );
}
