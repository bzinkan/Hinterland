/**
 * One-shot first-frame reporter for the render watchdog. Isolated
 * component (spike FrameHud pattern) so its runOnJS never re-renders the
 * canvas subtree; the parent unmounts it after the first pulse. Shared by
 * the biome scene and the retired vista renderer.
 */

import { runOnJS, useFrameCallback, useSharedValue } from "react-native-reanimated";

export function FirstFramePulse({ onFirstFrame }: { onFirstFrame: () => void }) {
  const seen = useSharedValue(false);
  useFrameCallback(() => {
    if (!seen.value) {
      seen.value = true;
      runOnJS(onFirstFrame)();
    }
  });
  return null;
}
