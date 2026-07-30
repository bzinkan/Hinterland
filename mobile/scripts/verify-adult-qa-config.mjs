import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const projectRoot = fileURLToPath(new URL("..", import.meta.url));
const expoCli = join(projectRoot, "node_modules", "expo", "bin", "cli");
const qaProjectId =
  process.env.HINTERLAND_QA_EAS_PROJECT_ID ??
  "11111111-1111-4111-8111-111111111111";
const qaClientId =
  process.env.HINTERLAND_QA_ENTRA_CLIENT_ID ??
  "22222222-2222-4222-8222-222222222222";
const qaTenantId =
  process.env.HINTERLAND_QA_ENTRA_TENANT_ID ??
  "33333333-3333-4333-8333-333333333333";

const raw = execFileSync(
  process.execPath,
  [expoCli, "config", "--type", "public", "--json"],
  {
    cwd: projectRoot,
    env: {
      ...process.env,
      APP_ENV: "adult-qa",
      SANCTUARY_DIORAMA: "1",
      HINTERLAND_QA_EAS_PROJECT_ID: qaProjectId,
      HINTERLAND_QA_ENTRA_CLIENT_ID: qaClientId,
      HINTERLAND_QA_ENTRA_TENANT_ID: qaTenantId,
    },
    encoding: "utf8",
    stdio: ["ignore", "pipe", "inherit"],
  },
);

const config = JSON.parse(raw);
const android = config.android ?? {};
const extra = config.extra ?? {};
const blocked = new Set(android.blockedPermissions ?? []);
const permissions = new Set(android.permissions ?? []);
const easJson = JSON.parse(readFileSync(join(projectRoot, "eas.json"), "utf8"));

function assert(condition, message) {
  if (!condition) {
    console.error(message);
    process.exit(1);
  }
}

assert(config.name === "The Hinterland Guide QA", "Adult QA display name is wrong");
assert(config.slug === "hinterland-qa", "Adult QA must use the separate EAS slug");
assert(config.owner === "thehinterlandguides-team", "Adult QA EAS owner is wrong");
assert(config.scheme === "hinterland-qa", "Adult QA scheme must be hinterland-qa");
assert(
  extra.eas?.projectId === qaProjectId,
  "Adult QA must bind to its separate EAS project ID",
);
assert(
  qaProjectId !== "278f4a33-e1b1-4468-8d02-a51defe03267",
  "Adult QA must not reuse the W1 EAS project",
);
assert(
  android.package === "app.thehinterlandguide.qa",
  "Adult QA Android package is wrong",
);
assert(
  config.ios?.bundleIdentifier === "app.thehinterlandguide.qa",
  "Adult QA iOS bundle is wrong",
);
assert(extra.appEnv === "adult-qa", "Adult QA appEnv is wrong");
assert(
  extra.apiBaseUrl === "https://qa-api.thehinterlandguide.app",
  "Adult QA API URL is wrong",
);
assert(
  extra.parentWebBaseUrl === "https://qa-parents.thehinterlandguide.app",
  "Adult QA parent-web URL is wrong",
);
assert(extra.updatesChannel === "adult-qa", "Adult QA updates channel is wrong");
assert(extra.expectedApiLane === "adult_qa", "Adult QA expected release lane is wrong");
assert(extra.enforceReleaseLane === true, "Adult QA must enforce the release lane");
assert(extra.entra?.clientId === qaClientId, "Adult QA Entra client is wrong");
assert(
  extra.entra?.authority === `https://login.microsoftonline.com/${qaTenantId}`,
  "Adult QA Entra tenant is wrong",
);
assert(
  extra.entra?.redirectUri ===
    "https://qa-parents.thehinterlandguide.app/auth/callback",
  "Adult QA Entra callback is wrong",
);
assert(config.updates?.enabled === true, "Adult QA must include the updates runtime");
assert(
  config.updates?.url === `https://u.expo.dev/${qaProjectId}`,
  "Adult QA updates URL must use the QA EAS project",
);
assert(
  config.updates?.checkAutomatically === "NEVER",
  "Adult QA evidence builds must execute the embedded bundle",
);
assert(
  config.runtimeVersion?.policy === "appVersion",
  "Adult QA runtime compatibility must follow appVersion",
);
assert(
  easJson.build?.["adult-qa"]?.channel === "adult-qa",
  "The Adult QA EAS profile must inject the adult-qa channel",
);
assert(
  easJson.build?.["adult-qa"]?.android?.buildType === "app-bundle",
  "Adult QA must produce a Play app bundle",
);
assert(extra.sanctuaryDiorama === true, "Adult QA must enable stable Sanctuary 2.5D");
assert(!("firebase" in extra), "Adult QA must not include Firebase config");
assert(
  typeof extra.devLoginKey !== "string" || extra.devLoginKey.length === 0,
  "Adult QA must never embed a development login key",
);

for (const permission of [
  "android.permission.ACCESS_FINE_LOCATION",
  "android.permission.RECORD_AUDIO",
  "android.permission.SYSTEM_ALERT_WINDOW",
  "android.permission.READ_EXTERNAL_STORAGE",
  "android.permission.WRITE_EXTERNAL_STORAGE",
  "android.permission.FOREGROUND_SERVICE",
  "android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK",
  "android.permission.MODIFY_AUDIO_SETTINGS",
]) {
  assert(blocked.has(permission), `Adult QA must block ${permission}`);
}
assert(
  permissions.has("android.permission.ACCESS_COARSE_LOCATION"),
  "Adult QA must explicitly request coarse location",
);

console.log("adult-qa Expo config is isolated and fail-closed.");
