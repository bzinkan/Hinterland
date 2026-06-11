/**
 * GLB loading for the Sanctuary 3D diorama.
 *
 * Deliberately NOT drei's useGLTF: the drei/native helpers lean on
 * DOM/Blob APIs that are fragile on Hermes (drei #2493). Bytes come from
 * the platform-split loader (loadGLB.native/.web) and parse through
 * three's GLTFLoader -- the known-good path on both platforms.
 *
 * Offline invariant (docs/sanctuary.md): assets are bundled with the app;
 * resolving a bundled module is a local copy/no-op, not a network fetch.
 */

import { useEffect, useState } from "react";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import { loadGLBBytes } from "@/src/sanctuary3d/assets/loadGLB";

/** Module-level cache: each GLB is parsed once per app session. */
const gltfCache = new Map<number, Promise<GLTF>>();

export function loadGLTFAsset(moduleId: number): Promise<GLTF> {
  let cached = gltfCache.get(moduleId);
  if (!cached) {
    cached = loadGLBBytes(moduleId).then((buffer) =>
      new GLTFLoader().parseAsync(buffer, ""),
    );
    // A failed load should be retryable on next mount, not poisoned forever.
    cached.catch(() => gltfCache.delete(moduleId));
    gltfCache.set(moduleId, cached);
  }
  return cached;
}

export type GLTFState =
  | { status: "loading"; gltf: null; error: null }
  | { status: "ready"; gltf: GLTF; error: null }
  | { status: "error"; gltf: null; error: Error };

/**
 * Hook wrapper. `moduleId` is the value of `require("….glb")` -- stable
 * across renders, so it is a safe effect dependency.
 */
export function useSanctuaryGLTF(moduleId: number): GLTFState {
  const [state, setState] = useState<GLTFState>({
    status: "loading",
    gltf: null,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading", gltf: null, error: null });
    loadGLTFAsset(moduleId).then(
      (gltf) => {
        if (!cancelled) setState({ status: "ready", gltf, error: null });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            gltf: null,
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [moduleId]);

  return state;
}
