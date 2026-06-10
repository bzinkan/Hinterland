import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (path) => readFileSync(join(root, path), "utf8");

const pages = {
  "public/index.html": read("public/index.html"),
  "public/privacy.html": read("public/privacy.html"),
  "public/terms.html": read("public/terms.html"),
};

const failures = [];

function expectIncludes(file, text) {
  if (!pages[file].includes(text)) {
    failures.push(`${file} is missing: ${text}`);
  }
}

function expectAbsent(file, pattern, label) {
  if (pattern.test(pages[file])) {
    failures.push(`${file} contains disallowed copy: ${label}`);
  }
}

expectIncludes("public/index.html", "Turn backyard curiosity into real science.");
expectIncludes("public/index.html", "Request pilot access");
expectIncludes("public/index.html", 'id="how-it-works"');
expectIncludes("public/index.html", 'id="sanctuary"');
expectIncludes("public/index.html", 'id="safety"');
expectIncludes("public/index.html", 'id="pilot"');
expectIncludes("public/index.html", 'id="faq"');
expectIncludes("public/index.html", "support@dragonfly-app.net");
expectIncludes("public/index.html", "privacy@dragonfly-app.net");
expectIncludes("public/index.html", 'href="/privacy"');
expectIncludes("public/index.html", 'href="/terms"');

const forbiddenCopy = [
  [/COPPA compliant/i, "COPPA compliant"],
  [/Google Play Families approved/i, "Google Play Families approved"],
  [/fully moderated in real time/i, "fully moderated in real time"],
  [/submitted automatically to iNaturalist/i, "submitted automatically to iNaturalist"],
  [/automatic iNaturalist submission/i, "automatic iNaturalist submission"],
  [/no location collected/i, "no location collected"],
];

for (const [pattern, label] of forbiddenCopy) {
  expectAbsent("public/index.html", pattern, label);
}

if (/<script[\s>]/i.test(pages["public/index.html"])) {
  failures.push("public/index.html must work without JavaScript");
}

if (/<(?:script|iframe|img)[^>]+(?:analytics|googletagmanager|gtag|facebook\.net|doubleclick|pixel)/i.test(pages["public/index.html"])) {
  failures.push("public/index.html appears to include analytics or tracking code");
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log("Static landing checks passed.");
