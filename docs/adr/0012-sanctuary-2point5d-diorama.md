# ADR 0012: Sanctuary 2.5D layered diorama (supersedes ADR 0011)

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Solo author
- **Supersedes:** ADR 0011 (Sanctuary 3D rendering stack)
- **Related:** ADR 0002 (LLMs are author-time, not runtime), ADR 0010
  (Azure target architecture), docs/sanctuary.md (the Sanctuary product
  contract — every invariant there binds this ADR unchanged)

## Context

ADR 0011 chose a true-3D living diorama on three + @react-three/fiber +
expo-gl. The M0 spike passed on the reference device (60 fps, clean
raycasts), and the branch reached a data-driven island scene with a
seven-island archipelago direction and CC0 vegetation dressing. The GO
was real. What changed is the risk-and-velocity calculation, not the
spike result:

1. **The 3D stack is the highest-risk technology in the app.** expo-gl
   is a native crash surface, and its failures can present as hangs
   rather than crashes — ADR 0011 already needed a first-frame watchdog
   for exactly that. Hermes has no WASM, which forecloses Draco/meshopt
   and every decoder-shaped escape hatch and constrains assets to
   quantized textureless GLBs. Shader cold-compile stalls and ANR
   gotchas are real on the 4 GB-Android floor. pmndrs is deprioritizing
   expo-gl in favor of WebGPU, so the stack's native path is aging. All
   of this ships inside a COPPA kids app whose degradation story has to
   be flawless.
2. **The feature needs composition and animation, not navigable
   space.** The v1 interaction contract — vista, tap-to-dive, inspect,
   ambient motion — never uses free navigation. A camera that only ever
   frames authored compositions is a layered-2D problem wearing 3D
   clothes. Nothing in docs/sanctuary.md requires a perspective camera.
3. **Solo-dev art velocity.** The archipelago end-state (ADR 0011
   addendum) needs strongly-themed art per island. Sourcing, licensing,
   and normalizing CC0 GLB packs plus hand-building Blockbench models is
   slower per island than authoring painterly layers, and the painterly
   look the product wants is what layered 2.5D natively produces.

ADR 0011's own rejected-alternatives list kept "2.5D Skia parallax —
gorgeous and cheaper" as the documented fallback. This ADR promotes the
fallback to the decision. The 3D implementation is frozen on
`feature/sanctuary-3d` as a reference branch; its three / R3F / expo-gl
dependencies never enter `main`.

## Decision

### Stack

| Layer | Choice | Why |
|---|---|---|
| Canvas | @shopify/react-native-skia (the SDK 54-compatible release), one `Canvas` for the whole scene | Skia is the RN-native 2D engine; a single canvas keeps compositing, hit-testing, and the frame budget in one place |
| Motion | react-native-reanimated shared values (already a dependency) driving Skia values | Parallax and framing are UI-thread animations; no per-frame JS round-trip |
| Gestures | Pan gesture → reanimated shared values. **No gyro** | Deterministic, no motion-sensitivity concerns, no sensor permission surface |
| Art | Agent-authored painterly SVG layers + sprites, inlined as strings in generated TS | No Metro changes, no new asset extensions, no runtime fetch (offline-render invariant) |
| Audio | expo-audio, default OFF, tap-to-play only | Matches the `sound_assets_available` contract in docs/sanctuary.md §9; never autoplays |

### Scene shape

The world is the **seven-island archipelago** from ADR 0011's
world-design addendum, rendered as a layered painterly vista: seven
floating islands, one per zone, each strongly themed. Two view states:

- **Vista** — all seven islands in one composition. Pan gesture drives
  a few depth layers at different parallax rates (sky, far islands,
  near islands, foreground wisps).
- **Dive** — tapping an island plays an authored framing animation into
  that island's layered close-up, where elements, mystery-cue
  silhouettes, and tap-to-inspect live. "Dive" is a scripted framing
  interpolation, not camera navigation.

### Pure core: ports and new modules

`scenePlan.ts` remains the firewall it was under ADR 0011: API state +
authored content in, plain renderer-agnostic scene description out. No
Skia import ever crosses into it, so a future renderer swap again
touches only the paint layer.

- **Ported unchanged from `feature/sanctuary-3d`** (they were pure by
  design; import paths only): `seededLayout` (deterministic,
  `element_id`-hash-seeded placement — no `Math.random` in the render
  path), `heightfield` (island silhouette generator, reused for layer
  silhouettes), `season/palette` (palette-token remaps for
  season/dormant looks), `dressing` (tier-driven scenery density).
- **New pure modules, same testability rule** (plain data in/out, no
  Skia, jest-only): `projection.ts` (world → layer-space mapping),
  `vistaLayout.ts` (island positions in the vista composition),
  `framing.ts` (vista↔dive framing definitions + interpolation),
  `vistaPlan.ts` (vista-level scene description), `hitTest.ts`
  (tap → island/element resolution; replaces GL raycasting).

### Flag and fallback

`SANCTUARY_3D` is renamed **`SANCTUARY_DIORAMA`** with identical
decision inputs and the same wiring shape (eas.json env → `extra`):

- Build flag: `1` for development/preview, `0` for
  play-internal/production until the post-pilot flag flip.
- Screen reader on → 2D screen.
- Kid-facing "Simple view" preference → 2D screen.
- The GL crash latch becomes a **render watchdog**: no first frame
  within the deadline → 2D screen, with the same persisted three-strike
  latch semantics.

`Sanctuary2DScreen.tsx` remains the **permanent** fallback: the
error-boundary target, the degradation floor, the screen-reader
default, and the "Simple view" escape hatch. Every "no" lands on the 2D
screen, never on a blank canvas.

## Art strategy

- **Agent-authored, best-effort painterly SVG.** Background / midground
  / foreground layers per island plus sprite elements. A committed
  generator + recipe produces each asset deterministically —
  regeneration is reproducible, and tuning happens in the recipe, not
  by hand-editing output. If the best-effort art disappoints, the
  recipes are the seam where commissioned or hand-authored art drops in
  later without touching the renderer.
- **Inlined as strings into generated TS.** No Metro config changes, no
  new bundler asset types, nothing fetched at render time.
- **Palette-token tinting.** Art references named palette slots;
  seasonal and dormant looks are token remaps of the same slots, never
  duplicate art — the same one-palette rule the GLB pipeline enforced.
- **Skia SVG element allowlist**, enforced by the generator and the
  validator because Skia's SVG support is partial: `path`, `rect`,
  `circle`, `ellipse`, `g`, `linearGradient`, `radialGradient`, `defs`,
  `use`. **No `filter`, no `mask`, no CSS, no `text`.**
- **Dormant zones cost zero art.** An asleep island is its live art
  rendered through a desaturating `ColorMatrix`. The "asleep, not
  locked" framing from docs/sanctuary.md carries over: no padlocks, no
  progress meters in the scene.

## Asset pipeline

`scripts/sanctuary_assets/assets.json` gains `layer`, `sprite`, and
`audio` asset classes alongside the GLB-era entries, and `validate.mjs`
gains the matching budget gates:

| Class | Per-file budget | Aggregate budget |
|---|---|---|
| layer (SVG) | ≤ 8 KB | SVG total ≤ 400 KB |
| sprite (SVG) | ≤ 6 KB | (shared SVG total) |
| audio | ≤ 300 KB | audio total ≤ 3 MB |

Licenses: `CC0` or `OWNED` (authored in this repo) only — the same
provenance-ledger + CI-gate posture as the ADR 0011 pipeline.

## Feature upgrades in scope

- **Photo-on-tap.** Inspecting an unlocked element can show the kid's
  own source photo. The clean-moderation gate is the **backend's**: the
  client renders only photo URLs the API chose to return and is never
  the safety gate. This must be re-validated against the post-#161
  photo-URL authorization model (geoprivacy + submit gates + adult-only
  unmoderated photo URLs) before the surface ships.
- **Expedition souvenirs.** Derived at read time from completed
  `expedition_progress` rows joined to authored
  `content/sanctuary/souvenirs.json`. No new tables, no new writer, no
  dispatcher change.
- **Silhouette hints.** `mystery_cues` carry no icon field, so
  silhouettes come from a static zone → sprite map in the diorama
  module. Never reveals the answer; same mystery-cue copy rules.
- **Soundscapes.** Per-zone ambient beds via expo-audio. Default OFF,
  tap-to-play, no autoplay, no microphone permission, no analytics —
  flipping `sound_assets_available` to `true` per the existing wire
  contract.

## Consequences

- **One EAS dev-client rebuild** — skia and expo-audio are native
  modules; EAS Update cannot deliver them. Store binaries rebuild at
  the same time. This is the only native-surface change; expo-gl and
  three never enter `main`.
- **A GO/NO-GO spike gates further investment**: the vista plus one
  dive running on the S21 Ultra (physical), with a throttled-emulator
  run as the mid-range proxy. If Skia can't hold the frame budget, the
  answer is scope reduction (fewer layers, fewer sprites), not a return
  to GL.
- **Deferred**, in rough order of likely return: gyro parallax,
  particle effects, SkSL wind/water shaders, Rive/Lottie for hero
  moments, per-device quality tiers.
- The 3D branch keeps its value as a reference: the vista framing,
  archipelago layout, and palette work inform the 2.5D art, and its
  pure core is the part that merges.

## Rejected alternatives

- **Continuing the ADR 0011 true-3D track** — superseded not because
  the spike failed but because the same product outcome is reachable
  without the app's highest-risk dependency stack (see Context).
- **Extending the RN-primitives 2D screen** — cannot express painterly
  depth or ambient motion; it stays as the permanent fallback, not the
  destination.
- **Rive/Lottie as the primary renderer** — a new runtime and
  authoring-tool dependency for composition layered Skia already
  expresses; retained on the deferred list for hero moments only.
- **WebView canvas** — a browser surface inside a COPPA kids app for
  zero product benefit.

## Addendum — composition pivot: biome chooser + full-bleed scenes (2026-07-06)

The D7 milestone shipped the seven-island archipelago vista this ADR
originally specified, and putting it on device is what changed the
decision. This addendum revises the **scene shape** section only; the
stack, the pure-core firewall, the flag lattice, and the art pipeline
invariants all carry over unchanged.

### Decision

The Sanctuary's main view is now a **biome chooser + full-bleed biome
scenes**:

- **Chooser** — the tab's landing view is a native RN scrollable list of
  seven large biome cards (zone title, awake depth-tier progress or a
  calm dormant mystery hint, palette-derived colors). No canvas; a card
  tap enters that zone's scene, Back returns.
- **Biome scene** — one zone rendered as a big layered 2.5D backdrop
  (WoW-zone-vista x Nintendo-diorama feel): sky gradient with a per-zone
  tint, then FAR / MID / GROUND depth bands spanning the whole scene
  width, plus optional FORE framing accents. The ground runs along the
  bottom of the screen — **a biome is a place you stand, not a floating
  island**: no plateau edge, no rock underside. The scene is ~1.6
  screens wide; horizontal drag pans with clamp + decay, bands move at
  ~0.2 / 0.5 / 1.0 / 1.3 of the pan (fore outruns the camera for the
  near-frame feel), and mid/fore sway in the wind pivoted at the ground
  line. Inhabitant sprites stand on the ground band, placed by remapping
  the existing seeded island-local layout onto ground coordinates
  (`groundRemap.ts`, pure + unit-tested, painter order and relative
  spread preserved, 44dp+ hit targets).

### Why

- **The kid picks a destination.** A chooser makes each biome a place
  you deliberately go, which matches the product's "visit your meadow"
  fiction better than panning past six other islands to reach it.
- **Full-bleed backdrops carry painterly art far better than small
  floating islands.** The archipelago squeezed each zone's art into a
  fraction of the canvas; a whole-screen layered backdrop is where
  hand-painted gradients, hill bands, and haze actually read.
- **The vista pan was mostly empty sky on portrait phones.** Seven
  islands in one 2.5-screen canvas left the majority of every frame
  unpainted atmosphere; a per-zone scene spends every pixel on the zone.

### Retired

- The archipelago vista as a routed surface: `vistaLayout.ts` slots, the
  vista composition, and pan-between-islands interaction. The vista
  renderer (`DioramaScene.tsx` and its islands-on-one-canvas path) stays
  in-tree this milestone as reference but is **unrouted** — nothing
  navigates to it.
- The vista->dive camera flight (`framing.ts` still backs its tests;
  scenes frame themselves).

### Kept unchanged (contracts that did not move)

- SkPicture-only rendering: record once per palette state, replay per
  frame; no per-frame SVG DOM, no per-frame React state.
- Palette-token art: every color still flows through
  `svgCache.substitute` + `paletteSlots`; dormant scenes are baked
  desaturated-slot recordings (zero steady-state layer cost) and the
  wake moment is the same transient saveLayer saturation lerp, held
  until the tab is focused.
- Silhouette hints + cue cards on cued dormant zones.
- The render watchdog + persisted 3-strike latch, now armed per scene
  mount (the chooser is native RN and cannot strike).
- The `SANCTUARY_DIORAMA` flag lattice, `Sanctuary2DScreen` as the
  permanent fallback, and `ElementInspectModal` reused as-is.
- The D4 spike (`app/dev/diorama-spike.tsx`) stays frozen and compiling.

### Migration path

Biomes get full backdrop art one at a time through the existing asset
pipeline (new `backdrop` kind: four 1024x640 bands per zone, ≤16 KB per
band, same {{palette-token}} + Skia-allowlist + determinism gates).
**Meadow ships first** (Elwynn/Nagrand grassland vocabulary: rolling
hill bands, wildflower drifts, soft treeline, winding pale path). A zone
without a backdrop set renders the documented interim instead: its
existing island layer art centered in the scene over a simple ground
gradient — so all seven biomes are enterable from day one and each
upgrade is a pure content drop with no renderer change.
