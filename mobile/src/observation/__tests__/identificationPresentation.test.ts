import {
  emptyIdentificationPresentation,
  identificationScopeKey,
} from "@/src/observation/identificationPresentation";

describe("identification presentation scope", () => {
  it("changes for either canonical owner or observation", () => {
    expect(identificationScopeKey("kid-1", "observation-1")).not.toBe(
      identificationScopeKey("kid-2", "observation-1"),
    );
    expect(identificationScopeKey("kid-1", "observation-1")).not.toBe(
      identificationScopeKey("kid-1", "observation-2"),
    );
  });

  it("resets every visual and loading field while adopting the new revision", () => {
    expect(emptyIdentificationPresentation(7)).toEqual({
      catalogQuery: "",
      catalogResults: [],
      suggestions: [],
      manualSpecies: "",
      revision: 7,
      busy: null,
      searching: false,
      message: null,
    });
  });
});
