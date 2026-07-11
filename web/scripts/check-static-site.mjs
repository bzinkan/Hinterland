import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (path) => readFileSync(join(root, path), "utf8");
const readBinary = (path) => readFileSync(join(root, path));

const pages = {
  "public/index.html": read("public/index.html"),
  "public/privacy.html": read("public/privacy.html"),
  "public/terms.html": read("public/terms.html"),
  "public/support.html": read("public/support.html"),
  "public/contact.html": read("public/contact.html"),
};
const staticWebAppConfig = JSON.parse(read("public/staticwebapp.config.json"));
const manifest = JSON.parse(read("public/site.webmanifest"));
const robots = read("public/robots.txt");
const sitemap = read("public/sitemap.xml");
const socialCardSource = read("public/social-card.svg");
const socialCardPng = readBinary("public/social-card.png");
const favicon = read("public/favicon.svg");
const touchIconPng = readBinary("public/apple-touch-icon.png");

const failures = [];
const pilotMailtoSubject = "mailto:support@thehinterlandguide.app?subject=The%20Hinterland%20Guide%20pilot%20access%20request";
const pilotMailtoFields = [
  "Parent%2Fguardian%20name%3A",
  "Email%3A",
  "Number%20of%20kids%3A",
  "Kids%27%20age%20range%3A",
  "Android%20phone%20available%3F%3A",
  "Are%20you%20willing%20to%20test%20with%20your%20child%20present%3F%3A",
  "Anything%20we%20should%20know%3F%3A",
];

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

function countMatches(content, pattern) {
  return Array.from(content.matchAll(pattern)).length;
}

function expectExactlyOneH1(file) {
  const count = countMatches(pages[file], /<h1\b/gi);
  if (count !== 1) {
    failures.push(`${file} must contain exactly one h1, got ${count}`);
  }
}

function expectHeadingHierarchy(file) {
  const headings = Array.from(pages[file].matchAll(/<h([1-6])\b/gi), (match) => Number(match[1]));
  if (headings.length === 0) {
    failures.push(`${file} must contain semantic headings`);
    return;
  }

  if (headings[0] !== 1) {
    failures.push(`${file} first heading must be h1`);
  }

  for (let index = 1; index < headings.length; index += 1) {
    if (headings[index] - headings[index - 1] > 1) {
      failures.push(`${file} skips heading levels from h${headings[index - 1]} to h${headings[index]}`);
      return;
    }
  }
}

function expectAccessibleRoleImages(file) {
  const roleImages = Array.from(pages[file].matchAll(/<svg\b[^>]*role=["']img["'][^>]*>/gi));
  for (const match of roleImages) {
    if (!/aria-(?:label|labelledby)=/i.test(match[0])) {
      failures.push(`${file} has an svg role="img" without an accessible label`);
    }
  }

  if (/aria-hidden=["']true["'][^>]*>\s*<svg\b[^>]*role=["']img["']/i.test(pages[file])) {
    failures.push(`${file} has a decorative svg with a conflicting role="img"`);
  }
}

function expectNoInterfaceAntiPatterns(file) {
  const antiPatterns = [
    [/transition\s*:\s*all\b/i, "transition: all"],
    [/outline\s*:\s*none\b/i, "outline: none"],
    [/user-scalable\s*=\s*no/i, "user-scalable=no"],
    [/maximum-scale\s*=\s*1/i, "maximum-scale=1"],
    [/<a\b[^>]*>\s*(?:click here|here|learn more|read more)\s*<\/a>/i, "generic link text"],
  ];

  for (const [pattern, label] of antiPatterns) {
    expectAbsent(file, pattern, label);
  }
}

function expectPngDimensions(file, buffer, width, height) {
  const signature = "89504e470d0a1a0a";
  if (buffer.subarray(0, 8).toString("hex") !== signature) {
    failures.push(`${file} must be a PNG file`);
    return;
  }

  if (buffer.toString("ascii", 12, 16) !== "IHDR") {
    failures.push(`${file} is missing a PNG IHDR chunk`);
    return;
  }

  const actualWidth = buffer.readUInt32BE(16);
  const actualHeight = buffer.readUInt32BE(20);
  if (actualWidth !== width || actualHeight !== height) {
    failures.push(`${file} must be ${width}x${height}, got ${actualWidth}x${actualHeight}`);
  }
}

expectIncludes("public/index.html", "Turn backyard curiosity into real science.");
expectIncludes("public/index.html", "curious explorers of all ages");
expectIncludes("public/index.html", "<title>The Hinterland Guide &mdash; Real nature, real science for curious kids</title>");
expectIncludes("public/index.html", 'content="The Hinterland Guide is a field app for kids ages 9&ndash;12. Kids log real outdoor observations, build a personal Dex, complete nature expeditions, and grow their own Sanctuary."');
expectIncludes("public/index.html", '<link rel="canonical" href="https://thehinterlandguide.app/">');
expectIncludes("public/index.html", '<meta name="robots" content="index,follow">');
expectIncludes("public/index.html", '<meta name="theme-color" content="#2f6f4e">');
expectIncludes("public/index.html", '<meta property="og:title" content="The Hinterland Guide &mdash; Turn backyard curiosity into real science">');
expectIncludes("public/index.html", '<meta property="og:description" content="An invite-only field app where kids make real nature observations and grow a living Sanctuary from what they discover.">');
expectIncludes("public/index.html", '<meta property="og:image" content="https://thehinterlandguide.app/social-card.png">');
expectIncludes("public/index.html", '<meta name="twitter:card" content="summary_large_image">');
expectIncludes("public/index.html", '<meta name="twitter:title" content="The Hinterland Guide &mdash; Turn backyard curiosity into real science">');
expectIncludes("public/index.html", '<meta name="twitter:image" content="https://thehinterlandguide.app/social-card.png">');
expectIncludes("public/index.html", '<link rel="apple-touch-icon" href="/apple-touch-icon.png">');
expectIncludes("public/index.html", '<link rel="manifest" href="/site.webmanifest">');
expectIncludes("public/index.html", '<script type="application/ld+json">');
expectIncludes("public/index.html", '"@type": "Organization"');
expectIncludes("public/index.html", '"url": "https://thehinterlandguide.app/"');
expectIncludes("public/index.html", 'scroll-padding-top: 96px;');
expectIncludes("public/index.html", 'scroll-margin-top: 96px;');
expectIncludes("public/index.html", '@media (prefers-reduced-motion: reduce)');
expectIncludes("public/index.html", '@media (forced-colors: active)');
expectIncludes("public/index.html", '-webkit-tap-highlight-color');
expectIncludes("public/index.html", 'aria-label="Request The Hinterland Guide pilot access by email"');
expectIncludes("public/index.html", "Request pilot access");
expectIncludes("public/index.html", pilotMailtoSubject);
expectIncludes("public/index.html", "Parent%2Fguardian%20name%3A%0D%0AEmail%3A");
expectIncludes("public/index.html", "Please do not include your child&rsquo;s full name in this request.");
expectIncludes("public/index.html", "The Hinterland Guide is in a small supervised pilot. We&rsquo;ll reply if we can include your family in the next test group.");
expectIncludes("public/index.html", "Known families only during this Internal Testing phase.");
expectIncludes("public/index.html", "The pilot request email asks for adult contact details and kids&rsquo; age range only.");
expectIncludes("public/index.html", "What happens next?");
expectIncludes("public/index.html", "We review pilot requests.");
expectIncludes("public/index.html", "If selected, we send Android internal-testing instructions.");
expectIncludes("public/index.html", "A parent or guardian helps create the kid account.");
expectIncludes("public/index.html", "The first test should happen with the adult present.");
expectIncludes("public/index.html", 'id="how-it-works"');
expectIncludes("public/index.html", 'id="sanctuary"');
expectIncludes("public/index.html", 'id="safety"');
expectIncludes("public/index.html", 'id="pilot"');
expectIncludes("public/index.html", 'id="faq"');
expectIncludes("public/index.html", "Is The Hinterland Guide public?");
expectIncludes("public/index.html", "What data does The Hinterland Guide collect?");
expectIncludes("public/index.html", "Can teachers use The Hinterland Guide?");
expectIncludes("public/index.html", "Is The Hinterland Guide available now?");
expectIncludes("public/index.html", "How do I get support?");
expectIncludes("public/index.html", "support@thehinterlandguide.app");
expectIncludes("public/index.html", "privacy@thehinterlandguide.app");
expectIncludes("public/index.html", 'href="/privacy"');
expectIncludes("public/index.html", 'href="/terms"');
expectIncludes("public/index.html", 'href="/support"');
expectIncludes("public/index.html", 'href="/contact"');

expectIncludes("public/privacy.html", "This page is written for the Hinterland Guide pilot and will be updated before broader release.");
expectIncludes("public/privacy.html", "curious explorers of all ages");
expectIncludes("public/privacy.html", "Organism photos");
expectIncludes("public/privacy.html", "optional four-character coarse-area geohash");
expectIncludes("public/privacy.html", "Species selection");
expectIncludes("public/privacy.html", "Kid display name or nickname");
expectIncludes("public/privacy.html", "Consent setup keeps a random proof");
expectIncludes("public/privacy.html", "No ads.");
expectIncludes("public/privacy.html", "No selling or renting personal data.");
expectIncludes("public/privacy.html", "iNaturalist public submission and photo-identification suggestions are");
expectIncludes("public/privacy.html", "privacy@thehinterlandguide.app");
expectIncludes("public/privacy.html", "Last updated: July 11, 2026.");

expectIncludes("public/terms.html", "The Hinterland Guide is a beta/pilot product");
expectIncludes("public/terms.html", "Kids should use The Hinterland Guide only with adult permission");
expectIncludes("public/terms.html", "No emergency or safety use");
expectIncludes("public/terms.html", "Do not upload harmful, inappropriate");
expectIncludes("public/terms.html", "No public social network features");
expectIncludes("public/terms.html", "support@thehinterlandguide.app");

expectIncludes("public/support.html", "support@thehinterlandguide.app");
expectIncludes("public/support.html", "Device model");
expectIncludes("public/support.html", "Android version");
expectIncludes("public/support.html", "Wrong account data visible");
expectIncludes("public/support.html", "A photo or privacy concern");
expectIncludes("public/support.html", 'href="/privacy"');
expectIncludes("public/support.html", 'href="/contact"');

expectIncludes("public/contact.html", "support@thehinterlandguide.app");
expectIncludes("public/contact.html", "privacy@thehinterlandguide.app");
expectIncludes("public/contact.html", "Request pilot access");
expectIncludes("public/contact.html", pilotMailtoSubject);
expectIncludes("public/contact.html", "Parent%2Fguardian%20name%3A%0D%0AEmail%3A");
expectIncludes("public/contact.html", "Please do not include your child&rsquo;s full name in this request.");
expectIncludes("public/contact.html", "The Hinterland Guide is in a small supervised pilot. We&rsquo;ll reply if we can");
expectIncludes("public/contact.html", "The Hinterland Guide is in limited Android testing");

for (const [file, url] of [
  ["public/privacy.html", "https://thehinterlandguide.app/privacy"],
  ["public/terms.html", "https://thehinterlandguide.app/terms"],
  ["public/support.html", "https://thehinterlandguide.app/support"],
  ["public/contact.html", "https://thehinterlandguide.app/contact"],
]) {
  expectIncludes(file, `<link rel="canonical" href="${url}">`);
  expectIncludes(file, '<meta name="robots" content="index,follow">');
  expectIncludes(file, '<meta name="theme-color" content="#2f6f4e">');
  expectIncludes(file, '<meta property="og:image" content="https://thehinterlandguide.app/social-card.png">');
  expectIncludes(file, '<meta name="twitter:card" content="summary_large_image">');
  expectIncludes(file, '<link rel="apple-touch-icon" href="/apple-touch-icon.png">');
  expectIncludes(file, '<link rel="manifest" href="/site.webmanifest">');
}

for (const file of ["public/index.html", "public/contact.html"]) {
  for (const field of pilotMailtoFields) {
    expectIncludes(file, field);
  }
}

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
  [/addictive/i, "addictive"],
  [/collect them all/i, "collect them all"],
  [/guaranteed safe/i, "guaranteed safe"],
  [/Google Play Families approved/i, "Google Play Families approved"],
  [/Google Play approved/i, "Google Play approved"],
  [/fully moderated in real time/i, "fully moderated in real time"],
  [/real-time moderation/i, "real-time moderation"],
  [/safe for all classrooms/i, "safe for all classrooms"],
  [/submitted automatically to iNaturalist/i, "submitted automatically to iNaturalist"],
  [/automatic iNaturalist submission/i, "automatic iNaturalist submission"],
  [/no location collected/i, "no location collected"],
  [/precise location is not collected/i, "precise-location reassurance"],
  [/The Hinterland Guide%20Android%20pilot/i, "old Android pilot mailto subject"],
  [/Kid%20age%20range/i, "old kid age mailto field"],
  [/Adult%20name%3A/i, "old adult name mailto field"],
  [/child(?:%27|'|&rsquo;)s%20full%20name%3A/i, "child full name mailto field"],
  [/school%20name%3A/i, "school name mailto field"],
];

for (const file of Object.keys(pages)) {
  for (const [pattern, label] of forbiddenCopy) {
    expectAbsent(file, pattern, label);
  }
}

for (const [file, content] of Object.entries(pages)) {
  expectExactlyOneH1(file);
  expectHeadingHierarchy(file);
  expectAccessibleRoleImages(file);
  expectNoInterfaceAntiPatterns(file);

  const executableScript = /<script(?![^>]*type\s*=\s*['"]?application\/ld\+json['"]?)[\s>]/i;
  if (executableScript.test(content)) {
    failures.push(`${file} must work without executable JavaScript`);
  }

  if (/<(?:script|iframe|img)[^>]+(?:analytics|googletagmanager|gtag|facebook\.net|doubleclick|pixel)/i.test(content)) {
    failures.push(`${file} appears to include analytics or tracking code`);
  }
}

if (manifest.name !== "The Hinterland Guide") {
  failures.push("public/site.webmanifest must name the app The Hinterland Guide");
}

if (manifest.theme_color !== "#2f6f4e") {
  failures.push("public/site.webmanifest must use the Hinterland Guide theme color");
}

for (const icon of ["/favicon.svg", "/apple-touch-icon.png"]) {
  if (!manifest.icons.some((manifestIcon) => manifestIcon.src === icon)) {
    failures.push(`public/site.webmanifest is missing icon ${icon}`);
  }
}

if (!manifest.icons.some((manifestIcon) => manifestIcon.src === "/apple-touch-icon.png" && manifestIcon.type === "image/png")) {
  failures.push("public/site.webmanifest must declare apple-touch-icon.png as image/png");
}

expectPngDimensions("public/social-card.png", socialCardPng, 1200, 630);
expectPngDimensions("public/apple-touch-icon.png", touchIconPng, 180, 180);

for (const text of [
  "User-agent: *",
  "Allow: /",
  "Sitemap: https://thehinterlandguide.app/sitemap.xml",
]) {
  if (!robots.includes(text)) {
    failures.push(`public/robots.txt is missing: ${text}`);
  }
}

for (const url of [
  "https://thehinterlandguide.app/",
  "https://thehinterlandguide.app/privacy",
  "https://thehinterlandguide.app/terms",
  "https://thehinterlandguide.app/support",
  "https://thehinterlandguide.app/contact",
]) {
  if (!sitemap.includes(`<loc>${url}</loc>`)) {
    failures.push(`public/sitemap.xml is missing ${url}`);
  }
}

for (const text of ["The Hinterland Guide", "Real nature.", "Real science.", "Closed beta"]) {
  if (!socialCardSource.includes(text)) {
    failures.push(`public/social-card.svg is missing: ${text}`);
  }
}

if (!favicon.includes("The Hinterland Guide")) {
  failures.push("public/favicon.svg should include The Hinterland Guide title text");
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log("Static landing checks passed.");
