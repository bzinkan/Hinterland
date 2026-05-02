# The Sanctuary (open-world layer)

The Sanctuary is a stylized, persistent in-app habitat scene that grows as a kid logs real-world observations. Each first-find reveals something in it; accumulating counts deepen the scene over time. Its job is to give kids a reason to open the app between observations — indoors, on a rainy Tuesday — without contradicting the "go outside" thesis. The world feels sparse and unfinished without real observations, and there is no way to fill it except by going outside.

This is a Phase 2 feature. Phase 1 does two cheap preparation tasks (tag content, reserve the data-model prefix) so the Phase 2 build isn't blocked on retrofits.

Related reading: `dispatcher.md` (where `WorldHandler` will plug in), `data-model.md` (the `WORLD#` SK prefix added below), `expedition-authoring.md` (content tooling that the unlock map will reuse).

## Why it exists

Five user-visible loops exist or are planned:

| Loop | What it answers | Phase |
|---|---|---|
| Dex (collection) | "What have I seen?" | 1 |
| Expeditions (goals) | "What should I look for?" | 1 |
| Leaderboard (standing) | "How am I doing vs. friends?" | 1 |
| Territory (places) | "Where have I been?" | 2 |
| **Sanctuary (expression)** | **"What am I building?"** | **2** |

The Sanctuary is the loop that handles indoor sessions. The other four require either an outdoor moment, a social context, or a literal map — none of which work on the couch. Without the Sanctuary, the app gives a kid nothing to do between observations except review what they already have. The 9–12 demographic reaches instinctively for sandbox-and-collection mechanics (Animal Crossing, Pokémon, Stardew, Roblox sandboxes); this loop is the one that scratches that itch within the bounds of a citizen-science app.

## The metaphor

The Sanctuary is a small, stylized habitat scene — meadow, woodland edge, pond, sky — that the kid sees on a dedicated tab in the app. Logging a real-world species reveals a corresponding element in the matching habitat zone. A logged butterfly reveals a butterfly fluttering over the meadow; a logged frog adds a ripple to the pond.

The metaphor is *naturalist's diorama*, not *Pokémon roster* and not *Minecraft world*. Kids don't build, mine, or craft. They observe in the real world; the Sanctuary reflects what they've seen. The aesthetic should feel like a high-quality children's nature illustration brought gently to life — closer to a moving Beatrix Potter or Owl Moon than to a video game.

This metaphor was picked over two alternatives. *Living journal* — a scrapbook of illustrated entries — has a lower art cost and a more bookish feel, but lacks the emotional pull of a place. *Spirit guardians* — Studio-Ghibli-adjacent magical-realist counterparts to each species — has the highest emotional pull but the highest art cost and the riskiest tonal calibration for a science-framed product. Sanctuary sits in the middle on cost while preserving the educational connection between real-world species and their habitats: kids learn ecology by seeing what clusters in which zone.

"Sanctuary" is a working name; final naming should be user-tested with kids during Phase 2.

## Unlock structure

Two tiers of unlocks, both authored as content:

**Coarse unlocks** — keyed by `iconic_taxon`. Every observation lights up a generic element in the matching zone, so even an obscure beetle still moves the Sanctuary visibly. Seven zones cover Phase 1 biology: meadow (Insecta, herbaceous Plantae), woodland (Aves, Mammalia, woody Plantae), pond (Amphibia, Mollusca, Actinopterygii), sky (raptors and large flyers), soil (Fungi, Annelida), urban (street/edge Aves and Mammalia), and elsewhere (everything else). The map is JSON, validated and synced exactly like expeditions.

**Charismatic unlocks** — keyed by `taxon_id`. Hand-picked, 50–100 species at launch. Each has a unique illustrated element that replaces or augments the coarse fallback. Butterfly, buffalo, hawk, fox, frog, dragonfly, deer, raccoon, monarch, hummingbird, and so on. Authoring one charismatic unlock costs one illustration plus one JSON entry; the budget is intentionally bounded.

Long-tail species fall back to the coarse unlock. That's the entire reason the coarse layer exists.

## Layered deepening (the saturation question)

A naïve "fill the world by collecting N species" model collapses motivation as soon as the world fills up. The Sanctuary instead deepens at observation-count thresholds *within each taxon group*:

| Threshold | What happens in that zone |
|---|---|
| 1 obs in group | Zone unlocks; coarse element appears |
| 5 obs in group | Density increases (more flowers, more leaves, ambient motion) |
| 20 obs in group | Weather and time-of-day effects unlock |
| 50 obs in group | A signature detail appears (a fox path through the woodland, mist rising from the pond) |

Charismatic first-finds layer on top of these, regardless of count. A kid who logs 200 plants but no birds has a lush, deepening meadow and an empty woodland — the Sanctuary tells them what kind of naturalist they're becoming.

This means the world keeps rewarding observations for years without an explicit "level cap." There is no end state; there is just a continuously richer scene.

## Dispatcher integration

A new `WorldHandler` plugs into `HANDLERS` after `DexHandler` (it reads `ctx.results["dex"].state["is_first_find"]`) and after `RarityHandler`. Two new reward types:

| Type | Weight | When |
|---|---|---|
| `world_unlock` | 60 | First time an element appears in the Sanctuary (zone unlock or charismatic species) |
| `world_evolution` | 30 | A zone crosses a deepening threshold (5/20/50 observations in the group) |

`world_unlock` sits at the same weight tier as a high `rarity_tier` reward — special but not unique-in-the-world. `world_evolution` sits with `expedition_complete` and `territory_claimed` — a milestone, not a one-time reveal.

The handler reads the unlock map (cached at cold start), determines what (if anything) the observation triggers, writes a `WORLD#` row, and emits the reward. It owns nothing on the `MEMBER#` row — the Sanctuary is per-user and not part of the leaderboard. No iNat or other external calls.

## Data model addition

Add to the key-schema table in `data-model.md`:

| Entity | PK | SK |
|---|---|---|
| Sanctuary element | `USER#<userId>` | `WORLD#<zoneId>#<elementId>` |
| Sanctuary zone state | `USER#<userId>` | `WORLD#<zoneId>#STATE` |

Element rows are written once per first-find. Zone-state rows hold the current depth tier (1/5/20/50) and the count of observations contributing to it; updated by `WorldHandler` via `UpdateItem ADD count :one`, with a conditional bump of the depth tier when count crosses a threshold. Both writes are idempotent — same observation twice produces no duplicate rows or counter drift.

Reading a kid's Sanctuary is one query: `Query PK=USER#<id>, SK begins_with WORLD#`.

## Relationship to Territory

Sanctuary and Territory are kept entirely separate in Phase 2. Territory is the real-world OSM map showing places the kid has claimed; the Sanctuary is the stylized habitat scene reflecting what they've found there. They share no rows, no rendering, and no surface in the app navigation.

A future Phase 4 could fold Territory into the Sanctuary as connective tissue — "the Sanctuary is a stylized rendering *of* your claimed territories" — but that's design work for later, and the architecture supports either path. Keeping them separate now avoids coupling two ambitious features on the same release schedule.

## Phase 1 preparation (cheap, do now)

Two items land in Phase 1 to make the Phase 2 build cleaner:

**Tag expedition content with `iconic_taxon` hints.** When authoring the five tier-1 expeditions in Week 10, ensure every step has either a `taxon_id` or `iconic_taxon` match (most already will). The coarse-unlock map derives directly from the same `iconic_taxon` vocabulary — no additional content authoring is needed once the Phase 1 expeditions are in.

**Reserve the `WORLD#` SK prefix.** Add the row to the key-schema table in `data-model.md` now, even though no code writes it in Phase 1. The migration-strategy section of that doc is explicit that key prefixes are forever; reserving now costs nothing and prevents a retrofit later.

That's the entire Phase 1 cost: two doc updates and one content-authoring discipline rule. No code, no schema migration, no scope impact on the 12-week plan.

## What's not in scope

Tracking here so they don't creep in:

- **Building / customization** — Phase 3 at earliest. No moving elements, no terraforming, no custom names. The Sanctuary reflects observations; it isn't built by the kid.
- **Multiplayer / visiting friends' Sanctuaries** — Phase 4 at earliest, possibly never. The Sanctuary is intentionally personal.
- **Trading or gifting** — explicitly never. There is no economy. Every element is earned by observing in the real world.
- **Idle / time-based progression** — explicitly never. The Sanctuary cannot grow without real observations. A flower that "grows over time" would teach the wrong lesson.

## Risk

The biggest risk is that the Sanctuary becomes more emotionally engaging than the outdoor capture loop, and kids start opening the app for the Sanctuary instead of for nature. The mitigations are structural: every unlock requires a real observation, no time-based progression exists, and the deepening curve rewards diversity and persistence over grinding. As long as those structural constraints hold, indoor session time is a feature (return engagement), not a bug.

A second, lesser risk is art budget. Fifty to a hundred charismatic illustrations plus seven richly deepening zones is a real Phase 2 deliverable. The contingency is to launch Phase 2 with only coarse unlocks (no charismatic species elements, just zones that deepen) and ship the charismatic layer in a Phase 2.5 content drop. The data model and dispatcher contracts do not change between those releases.
