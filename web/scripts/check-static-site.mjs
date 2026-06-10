import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (path) => readFileSync(join(root, path), "utf8");

const pages = {
  "public/index.html": read("public/index.html"),
  "public/privacy.html": read("public/privacy.html"),
  "public/terms.html": read("public/terms.html"),
  "public/support.html": read("public/support.html"),
  "public/contact.html": read("public/contact.html"),
};
const staticWebAppConfig = JSON.parse(read("public/staticwebapp.config.json"));

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
expectIncludes("public/index.html", 'href="/support"');
expectIncludes("public/index.html", 'href="/contact"');

expectIncludes("public/privacy.html", "This page is written for the Dragonfly pilot and will be updated before broader release.");
expectIncludes("public/privacy.html", "Organism photos");
expectIncludes("public/privacy.html", "Observation location");
expectIncludes("public/privacy.html", "Species selection");
expectIncludes("public/privacy.html", "Kid display name or nickname");
expectIncludes("public/privacy.html", "No ads.");
expectIncludes("public/privacy.html", "No selling or renting personal data.");
expectIncludes("public/privacy.html", "iNaturalist public submission is pilot-limited");
expectIncludes("public/privacy.html", "privacy@dragonfly-app.net");
expectIncludes("public/privacy.html", "Last updated: June 10, 2026.");

expectIncludes("public/terms.html", "Dragonfly is a beta/pilot product");
expectIncludes("public/terms.html", "Kids should use Dragonfly only with adult permission");
expectIncludes("public/terms.html", "No emergency or safety use");
expectIncludes("public/terms.html", "Do not upload harmful, inappropriate");
expectIncludes("public/terms.html", "No public social network features");
expectIncludes("public/terms.html", "support@dragonfly-app.net");

expectIncludes("public/support.html", "support@dragonfly-app.net");
expectIncludes("public/support.html", "Device model");
expectIncludes("public/support.html", "Android version");
expectIncludes("public/support.html", "Wrong account data visible");
expectIncludes("public/support.html", "A photo or privacy concern");
expectIncludes("public/support.html", 'href="/privacy"');
expectIncludes("public/support.html", 'href="/contact"');

expectIncludes("public/contact.html", "support@dragonfly-app.net");
expectIncludes("public/contact.html", "privacy@dragonfly-app.net");
expectIncludes("public/contact.html", "Request pilot access");
expectIncludes("public/contact.html", "Dragonfly is in limited Android testing");

const rewrites = new Map(
  staticWebAppConfig.routes.map((route) => [route.route, route.rewrite]),
);

for (const [route, rewrite] of [
  ["/privacy", "/privacy.html"],
  ["/terms", "/terms.html"],
  ["/support", "/support.html"],
  ["/contact", "/contact.html"],
]) {
  if (rewrites.get(route) !== rewrite) {
    failures.push(`public/staticwebapp.config.json must rewrite ${route} to ${rewrite}`);
  }
}

const forbiddenCopy = [
  [/COPPA compliant/i, "COPPA compliant"],
  [/Google Play Families approved/i, "Google Play Families approved"],
  [/fully moderated in real time/i, "fully moderated in real time"],
  [/submitted automatically to iNaturalist/i, "submitted automatically to iNaturalist"],
  [/automatic iNaturalist submission/i, "automatic iNaturalist submission"],
  [/no location collected/i, "no location collected"],
];

for (const file of Object.keys(pages)) {
  for (const [pattern, label] of forbiddenCopy) {
    expectAbsent(file, pattern, label);
  }
}

for (const [file, content] of Object.entries(pages)) {
  if (/<script[\s>]/i.test(content)) {
    failures.push(`${file} must work without JavaScript`);
  }

  if (/<(?:script|iframe|img)[^>]+(?:analytics|googletagmanager|gtag|facebook\.net|doubleclick|pixel)/i.test(content)) {
    failures.push(`${file} appears to include analytics or tracking code`);
  }
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log("Static landing checks passed.");
