import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("Observation manual-identification copy", () => {
  const source = readFileSync(
    join(__dirname, "..", "..", "..", "app", "observe-submit.tsx"),
    "utf8",
  );

  it("describes manual text as an identification rather than a general note", () => {
    expect(source).toContain("enter a manual identification");
    expect(source).toContain("Enter a manual identification");
    expect(source).toContain('placeholder="Your identification"');
    expect(source).toContain("Use identification");
    expect(source).not.toContain("Type a note instead");
    expect(source).not.toContain("Your organism note");
    expect(source).not.toContain("Use note");
  });
});
