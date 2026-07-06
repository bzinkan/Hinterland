// Metro bundler config for the Hinterland Expo app.
//
// 1. `unstable_enablePackageExports` so Metro honours the "exports" map
//    in package.json -- without it the @azure/msal-common entry points
//    fall back to "main" instead of "exports.browser.import".
//
// 2. `resolveRequest` shim for `@azure/msal-common/browser`. The
//    subpath's package.json declares
//      "main": "../lib/index-browser.cjs",
//      "module": "../dist/index-browser.mjs"
//    but the ESM file actually lives at `../dist-browser/index-browser.
//    mjs`. Metro follows the (incorrect) `module` field even with the
//    exports map on, and dies trying to read the missing file. We
//    intercept the module name and point it at the working file. Same
//    behaviour as `module-resolver` in babel land.
//
//    Upstream issue: https://github.com/AzureAD/microsoft-authentication
//    -library-for-js/issues -- not yet fixed at the time of writing.
//    Revisit when msal-common ships a clean package layout.

const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
config.resolver.unstable_enablePackageExports = true;

const MSAL_COMMON_BROWSER_PATH = path.join(
  __dirname,
  "node_modules",
  "@azure",
  "msal-common",
  "dist-browser",
  "index-browser.mjs"
);

const upstreamResolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === "@azure/msal-common/browser") {
    return { filePath: MSAL_COMMON_BROWSER_PATH, type: "sourceFile" };
  }
  if (upstreamResolveRequest) {
    return upstreamResolveRequest(context, moduleName, platform);
  }
  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
