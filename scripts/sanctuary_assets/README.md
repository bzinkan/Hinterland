# Sanctuary art pipeline (2.5D diorama, ADR 0012)

Deterministic, agent-authored painterly SVG for the Sanctuary diorama:
island parallax layers and element/fallback/souvenir/scenery sprites,
inlined as strings into generated TS so nothing is fetched at render
time and Metro needs no new asset types. Plain node, zero dependencies.

## The loop

```
author/recipes/*.json          <- tune art HERE (never edit svg/ by hand)
        |
        v
node author/generate_layers.mjs    7 zones x 4 bands -> svg/layers/
node author/generate_sprites.mjs   60 sprites        -> svg/sprites/
        |
        v
node build_manifest.mjs        inlines svg/ into
                               mobile/src/sanctuary/art/islandLayers.gen.ts
                               mobile/src/sanctuary/art/sprites.gen.ts
        |
        v
node validate.mjs              coverage / allowlist / budgets / licenses / drift
```

`npm run all` runs the whole loop. `node preview.mjs` composes throwaway
island previews into `.preview/` (git-ignored) for eyeballing in a
browser.

## Invariants (CI-enforced by validate.mjs + sanctuary-assets.yml)

- **Determinism.** Generators are seeded (`author/lib/rand.mjs`, the
  same fnv1a32/mulberry32 the app uses); rerunning produces
  byte-identical files. CI regenerates and `git diff --exit-code`s.
- **Coverage, either/or.** Every content icon key
  (`content/sanctuary/*.json`), every required souvenir id, and every
  `DRESSING_RULES` scenery key resolves to a sprite OR a
  `placeholders.json` allowlist entry -- never both, nothing stale.
- **Skia allowlist.** Only `path/rect/circle/ellipse/g/linearGradient/
  radialGradient/defs/use` (+ `stop`) with fill/opacity/transform-class
  attributes. No `filter`, no `mask`, no CSS, no `text`. The builder
  (`author/lib/svg.mjs`) cannot emit anything else; the linter re-checks
  committed files.
- **Palette tokens only.** Every color is a `{{slot}}` placeholder from
  the canonical vocabulary in `author/lib/tokens.mjs`; seasonal/dormant
  looks are token remaps, never duplicate art.
- **Budgets.** layer SVG <= 8 KB, sprite SVG <= 6 KB, svg/ total
  <= 400 KB (assets.json `categories`/`totals`).
- **Licenses.** `OWNED` (authored here; provenance = recipe path) or
  `CC0` (ledgered in sources.json with url+sha256 -- kept for the future
  audio beds). Nothing else can pass.
- **No drift.** The generated TS is rebuilt in-memory and byte-compared;
  hand-edits to `*.gen.ts` cannot merge.

## Adding art

1. Add the assets.json entry (name, kind, out path, zone/iconKey/
   sceneryKey, source) and, for sprites, a recipe in
   `author/recipes/sprites.json` (layers: the zone recipe).
2. If the art isn't ready yet, list the icon key in `placeholders.json`
   instead -- the app draws its typed fallback shape until the sprite
   lands.
3. `npm run all`, commit assets.json + recipes + svg/ + the regenerated
   `mobile/src/sanctuary/art/*.gen.ts` together.
