import { execFileSync } from "node:child_process";

const profiles = {
  development: "app.thehinterlandguide.dev",
  preview: "app.thehinterlandguide.staging",
  production: "app.thehinterlandguide",
  "play-internal": "app.thehinterlandguide",
};
const npx = process.platform === "win32" ? "npx.cmd" : "npx";

for (const [profile, packageName] of Object.entries(profiles)) {
  const output = execFileSync(npx, ["expo", "config", "--type", "public", "--json"], {
    encoding: "utf8",
    env: { ...process.env, APP_ENV: profile },
    shell: process.platform === "win32",
  });
  const config = JSON.parse(output);

  const errors = [];
  if (config.slug !== "the-hinterland-guide") errors.push(`slug=${config.slug}`);
  if (config.scheme !== "hinterland") errors.push(`scheme=${config.scheme}`);
  if (config.owner !== "thehinterlandguide") errors.push(`owner=${config.owner}`);
  if (config.android?.package !== packageName) errors.push(`android=${config.android?.package}`);
  if (config.ios?.bundleIdentifier !== packageName) errors.push(`ios=${config.ios?.bundleIdentifier}`);
  if (!String(config.name ?? "").startsWith("The Hinterland Guide")) {
    errors.push(`name=${config.name}`);
  }

  if (errors.length > 0) {
    throw new Error(`${profile} mobile identity check failed: ${errors.join(", ")}`);
  }
}

console.log("All mobile package identities look correct.");
