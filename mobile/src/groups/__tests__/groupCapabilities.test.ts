import { sharedGroupsIsEnabled } from "@/src/groups/useGroupCapabilities";

describe("shared-Groups capability", () => {
  it("enables only an explicit true flag", () => {
    expect(
      sharedGroupsIsEnabled({
        name: "Hinterland",
        env: "dev",
        version: "test",
        capabilities: { groups: { shared_groups_enabled: true } },
      }),
    ).toBe(true);
  });

  it("fails closed for false, absent, malformed, and unavailable metadata", () => {
    expect(
      sharedGroupsIsEnabled({
        name: "Hinterland",
        env: "dev",
        version: "test",
        capabilities: { groups: { shared_groups_enabled: false } },
      }),
    ).toBe(false);
    expect(sharedGroupsIsEnabled({ name: "Hinterland", env: "dev", version: "test" })).toBe(false);
    expect(
      sharedGroupsIsEnabled({
        name: "Hinterland",
        env: "dev",
        version: "test",
        capabilities: { groups: { shared_groups_enabled: "yes" as unknown as boolean } },
      }),
    ).toBe(false);
    expect(sharedGroupsIsEnabled(null)).toBe(false);
  });
});
