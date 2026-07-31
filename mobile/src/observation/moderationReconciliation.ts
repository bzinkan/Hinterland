import type {
  Observation,
  ObservationListResponse,
} from "@/src/api/observations";

/**
 * Foreground reconciliation cadence for an Observation whose moderation
 * presentation is still changing.
 *
 * W1's relay and moderation jobs are both scheduled once per minute. The
 * server owns that transition latency; mobile only owns how quickly a visible
 * pending/processing response is replaced after the server has committed the
 * next state.
 */
export const OBSERVATION_RECONCILE_INTERVAL_MS = 5_000;

/** Healthy deployed W1 bound from create response to server pilot_private. */
export const W1_NOOP_SERVER_TRANSITION_SLO_MS = 150_000;

/**
 * Protected-device bound: server SLO plus two foreground reconciliation
 * intervals (one in-flight request and one render/refetch opportunity).
 */
export const W1_NOOP_VISIBLE_TRANSITION_SLO_MS =
  W1_NOOP_SERVER_TRANSITION_SLO_MS +
  2 * OBSERVATION_RECONCILE_INTERVAL_MS;

export function childPresentationNeedsReconciliation(
  status: string | null | undefined,
): boolean {
  return status === "pending" || status === "processing";
}

export function listPresentationPollInterval(
  data:
    | {
        pages: ObservationListResponse[];
      }
    | undefined,
): number | false {
  const hasNonterminalPresentation = data?.pages.some((page) =>
    page.items.some((item) =>
      childPresentationNeedsReconciliation(item.child_presentation_status),
    ),
  );
  return hasNonterminalPresentation
    ? OBSERVATION_RECONCILE_INTERVAL_MS
    : false;
}

export function detailPresentationPollInterval(
  data: Pick<Observation, "child_presentation_status"> | undefined,
): number | false {
  return childPresentationNeedsReconciliation(
    data?.child_presentation_status,
  )
    ? OBSERVATION_RECONCILE_INTERVAL_MS
    : false;
}
