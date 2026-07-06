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

function assert(condition, message) {
  if (!condition) {
    console.error(message);
    process.exit(1);
  }
}

assert(config.name === "Hinterland Internal", "play-internal name must be Hinterland Internal");
assert(android.package === "com.dragonfly.app", "play-internal package must be com.dragonfly.app");
assert(extra.appEnv === "play-internal", "extra.appEnv must be play-internal");
assert(extra.updatesChannel === "play-internal", "updatesChannel must be play-internal");
assert(
  blocked.has("android.permission.ACCESS_FINE_LOCATION"),
  "play-internal must block ACCESS_FINE_LOCATION",
);
assert(
  permissions.has("android.permission.ACCESS_COARSE_LOCATION"),
  "play-internal must explicitly request ACCESS_COARSE_LOCATION",
);

console.log("play-internal Expo config looks safe.");
