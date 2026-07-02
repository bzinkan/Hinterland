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

/**
 * Expeditions that fit the kid's current surroundings. ``null`` means no
 * chip is selected ("All") and everything passes. Items tagged ``other``
 * match every environment -- an anywhere-style expedition fits whatever
 * context the kid picked, so it never disappears behind a chip.
 */
export function filterByEnvironment<T extends { environments: string[] }>(
  items: readonly T[],
  env: string | null,
): T[] {
  if (env === null) {
    return [...items];
  }
  return items.filter(
    (item) =>
      item.environments.includes(env) || item.environments.includes("other"),
  );
}

/**
 * Split progress items into in-progress vs completed buckets. Input order
 * is preserved within each bucket, so the server's sort carries through
 * to both the in-progress rows and the trophy shelf.
 */
export function splitProgress<T extends { completed_at: string | null }>(
  items: readonly T[],
): { inProgress: T[]; completed: T[] } {
  const inProgress: T[] = [];
  const completed: T[] = [];
  for (const item of items) {
    (item.completed_at === null ? inProgress : completed).push(item);
  }
  return { inProgress, completed };
}
