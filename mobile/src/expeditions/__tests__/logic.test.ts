import type { ObservationReward, RewardType } from "@/src/api/observations";
import {
  nextIncompleteStep,
  selectExpeditionRewards,
  selectSanctuaryRewards,
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
