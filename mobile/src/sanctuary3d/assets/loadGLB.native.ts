/**
 * Native GLB byte loader: expo-asset -> expo-file-system File.bytes().
 * Never Blob / fetch(file://) / createObjectURL -- those are the broken
 * paths on Hermes (ADR 0011).
 */

import { Asset } from "expo-asset";
import { File } from "expo-file-system";

export async function loadGLBBytes(moduleId: number): Promise<ArrayBuffer> {
  const asset = Asset.fromModule(moduleId);
  if (!asset.localUri) {
    await asset.downloadAsync();
  }
  const uri = asset.localUri ?? asset.uri;
  if (!uri) {
    throw new Error(`sanctuary3d: asset ${asset.name} has no local URI`);
  }
  const bytes = await new File(uri).bytes();
  // Copy into a tight ArrayBuffer in case the view is offset into a pool.
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}
