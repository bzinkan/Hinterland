// Created for Sanctuary 3D (ADR 0011): three.js ships static class blocks,
// which babel-preset-expo does not transform by default. Keep this file
// minimal -- babel-preset-expo autoconfigures everything else (including
// the reanimated/worklets plugin; do NOT add it manually).
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
    plugins: ["@babel/plugin-transform-class-static-block"],
  };
};
