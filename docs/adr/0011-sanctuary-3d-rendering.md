# ADR 0011: Sanctuary 3D rendering stack

> **Status: Superseded by [ADR 0012](0012-sanctuary-2point5d-diorama.md)
> (renderer decision), 2026-07-05.** Historical record — the 3D
> implementation lives frozen on `feature/sanctuary-3d`; its
> three / react-three-fiber / expo-gl stack and dependencies never merge
> to `main`. The renderer-agnostic core this ADR produced (scenePlan,
> seeded placement, heightfield, palette, dressing) ports to `main`
> unchanged under ADR 0012. Everything below is preserved verbatim from
> the branch, including the original "Proposed" status line.

- **Status:** Proposed (accepted when the M7 flag flip ships)
- **Date:** 2026-06-10
- **Deciders:** Solo author
- **Related:** ADR 0010 (Azure target architecture), docs/sanctuary.md §10 (Sanctuary mobile UX contract)

## Context

The Sanctuary shipped as a 2D React-Native-primitives diorama (PRs #95–#104):
colored zone bands, emoji symbols, text chips. The spec always deferred "final
illustration, audio bed, and soft motion" to later PRs. This ADR decides the
rendering stack for that work: a low-poly, true-3D **living diorama** — a
single stylized island whose zones wake, deepen, and gain inhabitants as the
kid's real observations accumulate.

Decisions taken with the product owner (2026-06-10):

1. True 3D (not 2.5D parallax, not a game-engine embed).
2. Art from CC0 packs (Kenney, Quaternius, Poly Pizza) + a handful of authored
   Blockbench models; no commissioned art before the pilot proves engagement.
3. v1 interaction = living diorama: idle camera drift, pinch zoom, constrained
   orbit (±30°), tap-to-inspect, ambient animation, unlock reveal sequence.
   No walk-around character; no map.
4. Parallel track behind a build flag. The W1 pilot ships the 2D sanctuary
   untouched; 3D ships as the first post-pilot binary update.

## Decision

### Stack

| Layer | Choice | Why |
|---|---|---|
| Scene graph | three@0.184.0 (pinned exact) | Standard; pin because upstream r3f/expo coupling is version-sensitive |
| React renderer | @react-three/fiber ^9.6.1 (`/native` entry) | 9.4.2+ carries the SDK 54 expo-file-system workaround; 9.5+ supports React 19 |
| GL surface | expo-gl ~16.0.10 (SDK 54 bundled version) | The only maintained GL surface under Expo managed workflow |
| Helpers (drei) | **Not used in v1** | drei/native leans on DOM/Blob APIs that break on Hermes (drei #2493); we hand-roll the ~3 helpers we need |
| GLB loading | expo-asset → `File.bytes()` → `GLTFLoader.parseAsync(ArrayBuffer)` | The known-good RN path. Never Blob / fetch(file://) / createObjectURL |
| Compression | `KHR_mesh_quantization` only (gltf-transform `quantize`) | Draco and meshopt need WASM decoders; **Hermes has no WASM**. Quantization needs no runtime decoder |
| Gestures | react-native-gesture-handler ~2.28.0 writing mutable refs; camera math in `useFrame` | three objects live on the JS thread; Reanimated worklets (UI thread) cannot touch the camera without round-trips. Reanimated stays out of the GL path entirely |
| Assets | Bundled GLBs under `mobile/assets/sanctuary/models/` | Offline-render invariant (docs/sanctuary.md §10): no network at render time |

### Flag and fallback

- Build-time flag `SANCTUARY_3D` (eas.json env → `extra.sanctuary3d`):
  `1` for development/preview, `0` for play-internal/production until the
  post-pilot flag-flip milestone.
- The 2D screen is extracted to `mobile/src/sanctuary/Sanctuary2DScreen.tsx`
  and is **permanent**: it is the error-boundary fallback, the low-end
  degradation floor, the screen-reader default, and the kid-facing
  "Simple view" escape hatch. The tab route becomes a flag chooser.
- Runtime overrides layer on the build flag: screen reader on → 2D;
  crash latch (3 GL crashes, persisted) → 2D; mount watchdog (no first frame
  in 5 s) → 2D, because native GL crashes can present as hangs (expo #41543).

### Asset pipeline

`scripts/sanctuary_assets/` (Node + gltf-transform, own package.json):
raw CC0 packs in git-ignored `.cache/` (re-downloadable via `sources.json`
provenance: pack, URL, license, sha256 — CC0-only enforced) → `normalize.mjs`
(dedup/flatten/join/weld → simplify to budget → strip textures → single
palette material → scale-normalize, origin at base, Y-up → whitelist+rename
animations → quantize → prune) → committed GLBs → `build_manifest.mjs` emits
`mobile/src/sanctuary/assetManifest.gen.ts` (icon key → require(glb) +
anchor + palette slots + animation) → `validate.mjs` CI gate (icon-key
coverage vs `content/sanctuary/*.json`, per-model + total size budgets,
palette compliance, manifest drift).

Budgets: ≤3k tris/60 KB props, ≤8k tris/500 KB animated creatures, ≤10k
tris/1 MB island base; **≤15 MB total bundled assets (20 MB hard ceiling)**;
≤60 draw calls / 150k tris on screen (mid-tier), ≤35 / 80k (floor).

### Non-negotiables carried over

The 3D surface inherits every Sanctuary invariant unchanged: no map, no
precise location, no external calls at render time, no social surface, no
streak/FOMO framing, no analytics pings, deterministic scene (element
placement seeded by `element_id` hash — no `Math.random` in the render path),
backend/API/content contract untouched.

## M0 spike results — **GO** (verified 2026-06-10)

Device: Samsung Galaxy S21 Ultra (SM-G998U), Android 15, new architecture
(`Running "main" with fabric:true` confirmed in logcat). EAS build
4f04c78a (development profile), driven via adb/scrcpy.

- [x] Quantized, textureless GLB (`spike-tree.glb`, generated by
      `scripts/sanctuary_assets/make_test_model.mjs`) renders lit with crisp
      flat shading — no blank canvas, no crash, no pixelStorei errors
- [x] FPS readout: **60** (HUD steady at 60 through idle-spin animation)
- [x] Tap raycast: 3 taps on the tree → `taps: 3` (exact; no phantom hits)
- [ ] APK/AAB size delta: development APK is 207 MB (debug, all ABIs — not
      meaningful). Measure the real delta on the first release/play-internal
      AAB after the flag flip; budget gate stays the CI asset-size check.
- [x] Decision: **proceed to M1+** (M1 shipped in the same branch; next is
      M2 data-driven scene with the web platform-split per the
      cross-platform addendum below)

One field note: the deep link `exp+hinterland://expo-development-client/…`
is also claimed by Expo Go when installed — launch the dev client
explicitly (`adb shell am start -n app.thehinterlandguide.dev/.MainActivity -a
android.intent.action.VIEW -d "<link>"`) or pick the server from the dev
client's launcher UI.

## World-design direction: biome archipelago (2026-06-10, product owner)

The end-state Sanctuary is **seven floating islands — one per zone, each a
distinct biome** — not one island with seven regions. A kid's observation
routes to the island whose biome it belongs to (long-term example: a
penguin photographed in an arctic context feeds an arctic biome island).

Implementation notes:

- The current single-island terrain is L1 look-dev scaffolding. The
  architecture already isolates the change: `zoneAnchors.ts` becomes
  per-island positions, `heightfield.ts` becomes a per-island
  mini-heightfield generator, `cameraViews.ts` gets per-island fly-to
  framings. `scenePlan`, element placement, the manifest, and all data
  flow are untouched. Target: the L2 terrain pass, before zone-specific
  asset dressing begins (so art lands on the final layout).
- An archipelago also composes better visually: each island can be
  strongly themed (palette, silhouette, props) without crowding, and the
  camera "dive" becomes a flight to an island.
- True biome routing (arctic, desert, ...) beyond today's seven
  iconic-taxon zones is a BACKEND/content vocabulary change
  (`SanctuaryZoneId` literal, zone routing map, authored content) — a
  Phase 2+ ADR of its own. The 3D layer renders whatever zones the API
  ships, so the renderer does not block that evolution.

## Cross-platform addendum (2026-06-10)

The Sanctuary must eventually render in the parents web app (Expo web,
react-native-web) as well as native. Decision: build the scene layer
platform-split from M2 onward — `SanctuaryCanvas.native.tsx` (r3f `/native`
+ expo-gl) vs `SanctuaryCanvas.web.tsx` (r3f DOM + WebGL), and a web GLB
loader using `fetch(assetUri).arrayBuffer()` (browsers have no
expo-file-system; they also have real WASM, so none of the Hermes
constraints apply on web). Everything above the canvas (scenePlan, palette,
anchors, manifest, GLBs) is already platform-agnostic. Kid web sign-in is a
separate post-pilot product decision (own ADR); adults-on-web works with
existing Entra auth once the web tab is unhidden behind the same flag.

## Consequences

- A new native-module surface (expo-gl, gesture-handler) enters the binary.
  It ships dark (flag off) in the pilot build; Sentry crash reporting must
  land before the flag flips (M6 gate).
- pmndrs is deprioritizing expo-gl in favor of WebGPU for r3f v10. Versions
  are pinned exactly; all three/r3f usage is contained behind the pure
  `scenePlan.ts` boundary so a future renderer swap touches only the scene
  layer.
- Dev iteration requires a dev-client build (the `development` EAS profile
  already uses `developmentClient: true`); emulator GL is unrepresentative —
  perf claims are only valid from physical devices.

## Rejected alternatives

- **2.5D Skia/Rive parallax** — gorgeous and cheaper, but the product call
  was true 3D; kept as the documented fallback if the M0 spike fails.
- **Unity/Godot as a library** — wildly over-scoped for a diorama; new build
  system, new license surface, kid-data SDK review burden.
- **drei + expo-three** — convenience layers with native-path breakage
  history; we use three + r3f directly.
- **CDN-delivered assets** — violates the offline-render invariant and adds
  a kid-facing network dependency for zero pilot benefit.
