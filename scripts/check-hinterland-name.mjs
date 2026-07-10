import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const retiredStem = String.fromCharCode(100, 114, 97, 103, 111, 110, 102, 108);
const root = resolve(import.meta.dirname, "..");
const files = execFileSync("git", ["ls-files", "-z"], {
  cwd: root,
  encoding: "utf8",
}).split("\0").filter(Boolean);

const findings = [];
for (const file of files) {
  if (file.toLowerCase().includes(retiredStem)) {
    findings.push(`${file}: retired name in filename`);
    continue;
  }

  const contents = readFileSync(resolve(root, file));
  if (!contents.includes(0) && contents.toString("utf8").toLowerCase().includes(retiredStem)) {
    findings.push(`${file}: retired name in contents`);
  }
}

if (findings.length > 0) {
  console.error("The Hinterland Guide naming gate failed:");
  for (const finding of findings) console.error(`- ${finding}`);
  process.exit(1);
}

console.log("The Hinterland Guide naming gate passed.");
