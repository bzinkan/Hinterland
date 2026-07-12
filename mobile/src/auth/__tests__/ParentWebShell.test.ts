import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("parent web shell theme contract", () => {
  it("keeps the server-rendered shell aligned with the light-only web theme", () => {
    const source = readFileSync(
      resolve(__dirname, "../../../app/+html.tsx"),
      "utf8",
    );

    expect(source).toContain("color-scheme: light");
    expect(source).toContain('<meta name="theme-color" content="#ffffff" />');
    expect(source).not.toContain("@media (prefers-color-scheme: dark)");
  });
});
