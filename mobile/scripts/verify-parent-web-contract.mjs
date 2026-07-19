import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));
const callbackSource = join(projectRoot, "app", "auth", "callback.tsx");
const groupsSource = join(projectRoot, "app", "groups.tsx");
const classroomSource = join(projectRoot, "app", "classroom.tsx");
const sourceConfig = join(projectRoot, "public", "staticwebapp.config.json");
const verifyDist = process.argv.includes("--dist");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function requireFile(path, label) {
  if (!existsSync(path)) fail(`${label} is missing: ${path}`);
  return readFileSync(path, "utf8");
}

function verifyConfig(raw, label) {
  const config = JSON.parse(raw);
  const callbacks = (config.routes ?? []).filter(
    (route) => route.route === "/auth/callback",
  );
  if (callbacks.length !== 1) {
    fail(`${label} must define exactly one /auth/callback route`);
  }
  const callback = callbacks[0];
  if (
    JSON.stringify(callback.methods) !== JSON.stringify(["GET"]) ||
    callback.rewrite !== "/auth/callback.html"
  ) {
    fail(`${label} must rewrite only GET /auth/callback to its static HTML`);
  }
  const headers = callback.headers ?? {};
  for (const [name, value] of Object.entries({
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  })) {
    if (headers[name] !== value) fail(`${label} must set ${name}: ${value}`);
  }
  if (config.navigationFallback != null) {
    fail(`${label} must not hide missing parent routes behind a navigation fallback`);
  }
  const classroomRedirects = (config.routes ?? []).filter(
    (route) => route.route === "/classroom",
  );
  if (classroomRedirects.length !== 1) {
    fail(`${label} must define exactly one legacy /classroom redirect`);
  }
  const classroomRedirect = classroomRedirects[0];
  if (
    JSON.stringify(classroomRedirect.methods) !== JSON.stringify(["GET"]) ||
    classroomRedirect.redirect !== "/groups" ||
    classroomRedirect.statusCode !== 302
  ) {
    fail(`${label} must temporarily redirect GET /classroom to /groups`);
  }
}

const callbackSourceText = requireFile(callbackSource, "parent callback source");
if (!callbackSourceText.includes("Finishing secure sign-in")) {
  fail("parent callback source must contain its route-specific loading marker");
}
const groupsSourceText = requireFile(groupsSource, "groups source");
if (!groupsSourceText.includes("Manage your groups")) {
  fail("groups source must contain its route-specific heading marker");
}
const classroomSourceText = requireFile(classroomSource, "classroom compatibility source");
if (!classroomSourceText.includes('href="/groups"')) {
  fail("legacy classroom route must redirect to /groups");
}
verifyConfig(requireFile(sourceConfig, "parent SWA config"), "parent SWA config");

if (verifyDist) {
  const callbackHtml = requireFile(
    join(projectRoot, "dist", "auth", "callback.html"),
    "exported parent callback",
  );
  if (!callbackHtml.includes("Finishing secure sign-in")) {
    fail("exported callback HTML is missing its route-specific loading marker");
  }
  verifyConfig(
    requireFile(
      join(projectRoot, "dist", "staticwebapp.config.json"),
      "exported parent SWA config",
    ),
    "exported parent SWA config",
  );
  for (const route of ["consent", "sign-in", "groups", "classroom"]) {
    requireFile(join(projectRoot, "dist", `${route}.html`), `exported /${route}`);
  }
}

console.log(
  verifyDist
    ? "Parent web source and exported callback contract look safe."
    : "Parent web callback source contract looks safe.",
);
