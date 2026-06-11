/**
 * Platform-split @react-three/fiber entry (ADR 0011 cross-platform
 * addendum). Metro resolves r3f.native.ts on iOS/Android and r3f.web.ts on
 * web; this file is the TypeScript-resolution default (same API surface as
 * the native entry, which mirrors the web one).
 *
 * Scene code must import Canvas/useFrame/useThree from "@/src/sanctuary3d/r3f"
 * -- never from "@react-three/fiber" or "@react-three/fiber/native" directly.
 */

export { Canvas, useFrame, useThree } from "@react-three/fiber/native";
export type { ThreeEvent, RootState } from "@react-three/fiber";
