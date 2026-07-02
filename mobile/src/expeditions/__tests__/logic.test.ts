import type { ObservationReward, RewardType } from "@/src/api/observations";
import {
  filterByEnvironment,
  nextIncompleteStep,
  selectExpeditionRewards,
  selectSanctuaryRewards,
  splitProgress,
} from "@/src/expeditions/logic";

function reward(type: RewardType, title: string = type): ObservationReward {
  return { type, title, detail: "", icon: "icon-key", weight: 1, payload: {} };
}

describe("selectExpeditionRewards", () => {
  it("returns [] when rewards is undefined", () => {
    expect(selectExpeditionRewards(undefined)).toEqual([]);
  });

  it("returns [] when rewards is empty", () => {
    expect(selectExpeditionRewards([])).toEqual([]);
  });

  it("keeps only expedition_step / expedition_complete from a mixed list", () => {
    const mixed = [
      reward("first_find"),
      reward("expedition_step"),
      reward("world_unlock"),
      reward("expedition_complete"),
      reward("rarity_tier"),
    ];
    expect(selectExpeditionRewards(mixed).map((r) => r.type)).toEqual([
      "expedition_step",
      "expedition_complete",
    ]);
  });

  it("preserves dispatcher order", () => {
    const mixed = [
      reward("expedition_step", "step one"),
      reward("repeat_find"),
      reward("expedition_step", "step two"),
    ];
    expect(selectExpeditionRewards(mixed).map((r) => r.title)).toEqual([
      "step one",
      "step two",
    ]);
  });
});

describe("selectSanctuaryRewards", () => {
  it("returns [] when rewards is undefined", () => {
    expect(selectSanctuaryRewards(undefined)).toEqual([]);
  });

  it("returns [] when rewards is empty", () => {
    expect(selectSanctuaryRewards([])).toEqual([]);
  });

  it("keeps only world_unlock / world_evolution from a mixed list", () => {
    const mixed = [
      reward("expedition_step"),
      reward("world_unlock"),
      reward("first_find"),
      reward("world_evolution"),
    ];
    expect(selectSanctuaryRewards(mixed).map((r) => r.type)).toEqual([
      "world_unlock",
      "world_evolution",
    ]);
  });
});

describe("nextIncompleteStep", () => {
  const step = (id: string, completed_at: string | null) => ({
    id,
    completed_at,
  });

  it("returns null for an empty array", () => {
    expect(nextIncompleteStep([])).toBeNull();
  });

  it("returns null when every step is complete", () => {
    const steps = [
      step("a", "2026-06-01T00:00:00Z"),
      step("b", "2026-06-02T00:00:00Z"),
    ];
    expect(nextIncompleteStep(steps)).toBeNull();
  });

  it("returns the first incomplete step in content order", () => {
    const steps = [
      step("a", "2026-06-01T00:00:00Z"),
      step("b", null),
      step("c", null),
    ];
    expect(nextIncompleteStep(steps)?.id).toBe("b");
  });
});

describe("filterByEnvironment", () => {
  const exp = (id: string, environments: string[]) => ({ id, environments });

  it("returns [] for an empty list", () => {
    expect(filterByEnvironment([], "yard")).toEqual([]);
  });

  it("returns every item when env is null", () => {
    const items = [exp("a", ["yard"]), exp("b", ["park", "street"])];
    expect(filterByEnvironment(items, null)).toEqual(items);
  });

  it("keeps only items tagged with the selected environment", () => {
    const items = [exp("a", ["yard", "park"]), exp("b", ["street"])];
    expect(filterByEnvironment(items, "park").map((e) => e.id)).toEqual(["a"]);
  });

  it('includes "other" items for any environment', () => {
    const items = [exp("a", ["other"]), exp("b", ["school"])];
    expect(filterByEnvironment(items, "yard").map((e) => e.id)).toEqual(["a"]);
    expect(filterByEnvironment(items, "school").map((e) => e.id)).toEqual([
      "a",
      "b",
    ]);
  });

  it("preserves input order", () => {
    const items = [
      exp("a", ["yard"]),
      exp("b", ["other"]),
      exp("c", ["yard", "park"]),
    ];
    expect(filterByEnvironment(items, "yard").map((e) => e.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });
});

describe("splitProgress", () => {
  const item = (id: string, completed_at: string | null) => ({
    id,
    completed_at,
  });

  it("returns two empty buckets for an empty list", () => {
    expect(splitProgress([])).toEqual({ inProgress: [], completed: [] });
  });

  it("buckets by completed_at", () => {
    const items = [item("a", null), item("b", "2026-06-01T00:00:00Z")];
    const { inProgress, completed } = splitProgress(items);
    expect(inProgress.map((i) => i.id)).toEqual(["a"]);
    expect(completed.map((i) => i.id)).toEqual(["b"]);
  });

  it("preserves input order within each bucket", () => {
    const items = [
      item("a", "2026-06-01T00:00:00Z"),
      item("b", null),
      item("c", "2026-06-02T00:00:00Z"),
      item("d", null),
    ];
    const { inProgress, completed } = splitProgress(items);
    expect(inProgress.map((i) => i.id)).toEqual(["b", "d"]);
    expect(completed.map((i) => i.id)).toEqual(["a", "c"]);
  });
});
