/**
 * Web GLB byte loader: expo-asset resolves the bundled asset to a same-origin
 * static URL in the web export; plain fetch().arrayBuffer() is the native
 * browser path (no expo-file-system on web). Still bundled-asset-only --
 * the URL is part of the app bundle, not a remote CDN.
 */

import { Asset } from "expo-asset";

export async function loadGLBBytes(moduleId: number): Promise<ArrayBuffer> {
  const asset = Asset.fromModule(moduleId);
  if (!asset.localUri) {
    await asset.downloadAsync();
  }
  const uri = asset.localUri ?? asset.uri;
  if (!uri) {
    throw new Error(`sanctuary3d: asset ${asset.name} has no URI`);
  }
  const response = await fetch(uri);
  if (!response.ok) {
    throw new Error(`sanctuary3d: asset fetch failed (${response.status})`);
  }
  return response.arrayBuffer();
}
