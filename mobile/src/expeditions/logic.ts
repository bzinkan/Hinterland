/**
 * Pure expedition helpers -- reward filtering + step ordering.
 *
 * No React imports on purpose: everything here is plain data-in/data-out
 * so the submit flow and the expedition detail screen share one tested
 * implementation.
 */

import type { ObservationReward } from "@/src/api/observations";

/**
 * Rewards that should surface as expedition feedback after a submit:
 * ``expedition_step`` (a step just completed) and ``expedition_complete``
 * (the whole expedition finished). Dispatcher order is preserved.
 */
export function selectExpeditionRewards(
  rewards: ObservationReward[] | undefined,
): ObservationReward[] {
  return (rewards ?? []).filter(
    (r) => r.type === "expedition_step" || r.type === "expedition_complete",
  );
}

/**
 * Rewards that feed the Sanctuary reveal modal: ``world_unlock`` and
 * ``world_evolution``. Moved out of observe-submit so the filter lives
 * next to its expedition sibling and is unit-testable.
 */
export function selectSanctuaryRewards(
  rewards: ObservationReward[] | undefined,
): ObservationReward[] {
  return (rewards ?? []).filter(
    (r) => r.type === "world_unlock" || r.type === "world_evolution",
  );
}

/**
 * First step that has not been completed yet, or ``null`` when every step
 * is done (or the list is empty). Steps arrive from the API in content
 * order, so "first incomplete" is the kid's "up next".
 */
export function nextIncompleteStep<T extends { completed_at: string | null }>(
  steps: readonly T[],
): T | null {
  return steps.find((s) => s.completed_at === null) ?? null;
}
