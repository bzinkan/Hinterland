import { suggestAdultDisplayName } from "@/src/auth/msal";

describe("suggestAdultDisplayName", () => {
  it("uses a compact editable Microsoft account name without deriving from email", () => {
    expect(suggestAdultDisplayName("  Alex   Adult  ")).toBe("Alex Adult");
    expect(suggestAdultDisplayName(undefined)).toBe("");
  });

  it("fits the server's 80-character display-name contract", () => {
    expect(Array.from(suggestAdultDisplayName("🦋".repeat(81)))).toHaveLength(80);
  });
});
