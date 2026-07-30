import { execFileSync } from "node:child_process";

const profiles = {
  development: "app.thehinterlandguide.dev",
  preview: "app.thehinterlandguide.staging",
  production: "app.thehinterlandguide",
  "play-internal": "app.thehinterlandguide",
  "adult-qa": "app.thehinterlandguide.qa",
};
const easProjectId = "278f4a33-e1b1-4468-8d02-a51defe03267";
const qaProjectId = "11111111-1111-4111-8111-111111111111";
const qaClientId = "22222222-2222-4222-8222-222222222222";
const qaTenantId = "33333333-3333-4333-8333-333333333333";
const npx = process.platform === "win32" ? "npx.cmd" : "npx";

for (const [profile, packageName] of Object.entries(profiles)) {
  const output = execFileSync(
    npx,
    ["expo", "config", "--type", "public", "--json"],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        APP_ENV: profile,
        HINTERLAND_QA_EAS_PROJECT_ID: qaProjectId,
        HINTERLAND_QA_ENTRA_CLIENT_ID: qaClientId,
        HINTERLAND_QA_ENTRA_TENANT_ID: qaTenantId,
      },
      shell: process.platform === "win32",
    },
  );
  const config = JSON.parse(output);

  const errors = [];
  const isAdultQa = profile === "adult-qa";
  const expectedSlug = isAdultQa ? "hinterland-qa" : "hinterland";
  const expectedScheme = isAdultQa ? "hinterland-qa" : "hinterland";
  const expectedProjectId = isAdultQa ? qaProjectId : easProjectId;
  if (config.slug !== expectedSlug) errors.push(`slug=${config.slug}`);
  if (config.scheme !== expectedScheme) errors.push(`scheme=${config.scheme}`);
  if (config.owner !== "thehinterlandguides-team") errors.push(`owner=${config.owner}`);
  if (config.extra?.eas?.projectId !== expectedProjectId) {
    errors.push(`eas.projectId=${config.extra?.eas?.projectId}`);
  }
  if (config.android?.package !== packageName) errors.push(`android=${config.android?.package}`);
  if (config.ios?.bundleIdentifier !== packageName) errors.push(`ios=${config.ios?.bundleIdentifier}`);
  if (!String(config.name ?? "").startsWith("The Hinterland Guide")) {
    errors.push(`name=${config.name}`);
  }
  if (
    profile === "production" ||
    profile === "play-internal" ||
    profile === "adult-qa"
  ) {
    const blocked = new Set(config.android?.blockedPermissions ?? []);
    const permissions = new Set(config.android?.permissions ?? []);
    for (const permission of [
      "android.permission.ACCESS_FINE_LOCATION",
      "android.permission.RECORD_AUDIO",
      "android.permission.SYSTEM_ALERT_WINDOW",
      "android.permission.READ_EXTERNAL_STORAGE",
      "android.permission.WRITE_EXTERNAL_STORAGE",
    ]) {
      if (!blocked.has(permission)) errors.push(`not-blocked=${permission}`);
    }
    if (!permissions.has("android.permission.ACCESS_COARSE_LOCATION")) {
      errors.push("missing=android.permission.ACCESS_COARSE_LOCATION");
    }
  }

  if (errors.length > 0) {
    throw new Error(`${profile} mobile identity check failed: ${errors.join(", ")}`);
  }
}

console.log("All mobile package identities look correct.");
