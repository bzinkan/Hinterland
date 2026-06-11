/**
 * Platform-split GLB byte loader (TS-resolution default; Metro picks
 * loadGLB.native.ts / loadGLB.web.ts at bundle time).
 *
 * Contract: given a Metro asset module id (the value of require("….glb")),
 * resolve the bundled asset and return its bytes as a tight ArrayBuffer
 * suitable for GLTFLoader.parseAsync. Bundled assets only -- the offline
 * invariant means this never fetches a remote URL at render time.
 */

export { loadGLBBytes } from "./loadGLB.native";
