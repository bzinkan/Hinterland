import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));
const expoCli = join(projectRoot, "node_modules", "expo", "bin", "cli");
const args = [expoCli, "config", "--type", "public", "--json"];

const raw = execFileSync(process.execPath, args, {
  cwd: projectRoot,
  env: { ...process.env, APP_ENV: "play-internal" },
  encoding: "utf8",
  stdio: ["ignore", "pipe", "inherit"],
});

const config = JSON.parse(raw);
const android = config.android ?? {};
const extra = config.extra ?? {};
const blocked = new Set(android.blockedPermissions ?? []);
const permissions = new Set(android.permissions ?? []);
const expectedProjectId = "278f4a33-e1b1-4468-8d02-a51defe03267";

function assert(condition, message) {
  if (!condition) {
    console.error(message);
    process.exit(1);
  }
}

assert(
  config.name === "The Hinterland Guide Internal",
  "play-internal name must be The Hinterland Guide Internal",
);
assert(
  config.owner === "thehinterlandguides-team",
  "EAS project owner must be thehinterlandguides-team",
);
assert(
  extra.eas?.projectId === expectedProjectId,
  `EAS project must be the existing hinterland project (${expectedProjectId})`,
);
assert(android.package === "app.thehinterlandguide", "play-internal package must be app.thehinterlandguide");
assert(extra.appEnv === "play-internal", "extra.appEnv must be play-internal");
assert(
  extra.apiBaseUrl === "https://api.thehinterlandguide.app",
  "play-internal API base URL must be https://api.thehinterlandguide.app",
);
assert(extra.updatesChannel === "play-internal", "updatesChannel must be play-internal");
assert(!("firebase" in extra), "play-internal must not include Firebase config");
assert(
  blocked.has("android.permission.ACCESS_FINE_LOCATION"),
  "play-internal must block ACCESS_FINE_LOCATION",
);
assert(
  blocked.has("android.permission.RECORD_AUDIO"),
  "play-internal must block RECORD_AUDIO",
);
assert(
  permissions.has("android.permission.ACCESS_COARSE_LOCATION"),
  "play-internal must explicitly request ACCESS_COARSE_LOCATION",
);
// app.config.ts bakes devLoginKey=null for store builds; Expo's public
// config serializes that null as `{}`. The property that matters: the
// store artifact must never carry a USABLE key (a non-empty string) --
// the app's gate (src/auth/devLoginPolicy.ts) ignores anything else.
assert(
  typeof extra.devLoginKey !== "string" || extra.devLoginKey.length === 0,
  "play-internal must never embed a dev-login key",
);

console.log("play-internal Expo config looks safe.");
