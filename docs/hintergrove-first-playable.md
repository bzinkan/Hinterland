# Hintergrove interactive first playable

- **Status:** Approved product scope for implementation; features are not yet implemented.
- **Approved:** October 27, 2026.
- **Audience:** Children roughly ages 9–12.
- **Build direction:** Fresh AWS application, Expo mobile host, and Unity habitat. See [ADR 0017](adr/0017-hintergrove-fresh-aws-unity.md).

## Product goal

Make one small woodland clearing enjoyable to explore immediately, then give a real outdoor discovery a clear, satisfying effect on that world. The child can touch, investigate, rearrange, and observe before going outside and recognize what changed afterward.

A new account starts with a composed, inviting clearing and three wildlife characters. Real discoveries add habitat features and illustrated behaviors. The initial world must be appealing before any unlocks.

## The five approved features

### 1. One small clearing with several responsive objects

Include a tree, log, vegetation, and a perch as a compact, cohesive composition. Several objects respond to touch through brief motion, a wildlife reaction, or optional sound. Responses are repeatable and do not require a real observation.

Use a bounded camera with pan and pinch zoom. Keep touch targets clear and distinguish object interaction from camera movement. Provide an obvious exit, sound controls, and reduced-motion behavior.

### 2. Three wildlife characters with distinct personalities and behaviors

| Character | Starting behavior examples | Interactive role |
| --- | --- | --- |
| Robin | Hops, looks around, searches the ground, and chirps | Leads the search challenge, offers one inspectable feather detail, and responds to perch placement |
| Squirrel | Investigates the log, moves between authored locations, and flicks its tail | Adds a second recognizable personality and a repeatable selection response |
| Butterfly | Flies a short route and rests on a leaf | Gains an illustrated flower-visitation behavior after the plant-patch unlock |

Choose a small, polished set of animations that makes each character distinct. Keep the butterfly's flower-visitation behavior reserved for the earned reveal so the payoff is visible.

These are characters in an illustrated game world. Their presence and behavior do not claim that the child observed those exact species or interactions outdoors.

### 3. One short search challenge

Use "Find the source of the chirp" as the first authored challenge. The robin's sound invites exploration; an equivalent visual clue makes the entire challenge completable with sound off.

Finding the robin triggers a brief response and lets the child inspect one feather detail through a simple discovery-lens view. One authored close-up/detail is sufficient for this slice; a general magnification system and detail catalog are later possibilities.

Let the child retry or leave at any time. Completion records a virtual play achievement and does not create an outdoor observation.

### 4. A few objects the child can arrange

Provide a small selection of objects and a small number of authored, valid placement spots. The perch is the first example: choose another valid spot, place it, and watch the robin use the new location.

Keep placement bounded and predictable. Support a simple select-object/select-spot alternative to dragging. Save the child's layout separately from observation-derived habitat state so it survives a normal return visit without granting discovery rewards.

Starter objects are available immediately. Arrangement cannot place an unearned plant patch, trigger its unlock, or release the reserved butterfly behavior.

### 5. One real outdoor observation with a strong visual reveal

Invite the child to find a real plant outside. A leaf, flower, or whole plant is a suitable subject. The child photographs it, chooses the broad reviewed plant category, and saves the observation; an exact species name is optional.

After the backend accepts the record as an eligible plant observation, unlock one vegetation/flower patch. Briefly frame the location, reveal the plants with coordinated motion and optional sound, and enable the new illustrated butterfly-and-flower behavior. Offer a reduced-motion reveal and a direct way to inspect the result.

Connect the change to the child's saved Journal record. The illustration is an educational example, not a claim that the photo shows that flower species, a butterfly, or a verified flower visit. A broad category selection is not a confirmed scientific identification.

## Example child journey

1. Open the clearing and notice movement, a chirp, and a visual clue.
2. Follow the clue, find the robin, and see it react.
3. Examine one feather detail using the discovery-lens view.
4. Move the perch to another valid spot and watch the robin respond.
5. Choose "Find a plant," go outside with appropriate adult supervision, and photograph a leaf, flower, or plant.
6. Save the real observation using the broad plant category.
7. After backend confirmation, return to the clearing, see the new patch appear, and notice the butterfly's new illustrated behavior.
8. Open the associated Journal record and understand how the finding contributed.

The child can explore the clearing without completing the outdoor task during the same session.

## Progress and data boundaries

| State | What can change it | What it represents |
| --- | --- | --- |
| Virtual play achievement | Completing the authored robin search | Something accomplished inside the game |
| Layout preference | Moving available objects among valid slots | The child's arrangement of their world |
| Real observation and Journal entry | Saving an actual outdoor finding | The child's recorded evidence and its identification confidence |
| Earned plant patch and associated illustrated behavior | Backend-confirmed eligibility from a real plant observation | A habitat contribution linked to that record |

- Virtual taps, challenge completions, and rearrangement never create real observations, confirmed species records, or observation-derived unlocks.
- The backend owns observation acceptance and permanent habitat progression. Unity displays the authorized state and reports presentation interactions to the host.
- One qualifying plant observation unlocks the patch once. Repeated submissions, retries, and later qualifying observations do not duplicate the patch.
- If a contributing record is rejected as invalid or deleted, recompute eligibility from remaining valid observations. Layout and virtual play achievements do not preserve an unsupported observation unlock.
- Unknown real findings can still save to the Journal and receive an appropriate general acknowledgement without an invented species identification or plant-patch entitlement.
- An unsuccessful outdoor search can receive encouragement or an alternative clue without a fabricated observation or permanent habitat unlock.
- Capture and queue observations durably offline. Show earned habitat changes after server confirmation; a queued photo alone does not grant progress.
- Photo review is separate from observation saving and category selection. Pending photos remain adult-review-only; the child's Journal uses a neutral pending tile. Approved photos are private to the child and authorized managing adult.
- Keep all stored preferences, play achievements, observations, cached scene state, and delayed callbacks isolated by child account.
- Keep this first slice free of public photo sharing and child free-text entry.

## Implementation assumptions

These quantities are starting estimates for a prototype, not additional approved content commitments:

- Approximately 3–5 responsive objects in the one clearing.
- Approximately 2–3 arrangeable objects with a few fixed valid spots.
- One authored robin detail rather than a full discovery-lens platform.
- Portrait phone framing with Android as the first standalone device target and an early integration check for both Android and iOS.

Prefer a few convincing interactions and cohesive, properly licensed art over a large inventory. Confirm the final counts and device performance budgets during the visual and native-integration spikes.

## Suggested build order

1. **Standalone interaction prototype:** In a clean Unity project, use synthetic fixtures to prove the clearing, three distinct characters, touch responses, robin search, one detail view, and bounded object placement. Record a real-device video; label fixtures as development data.
2. **Native integration spike:** Verify full-screen Unity entry/return within the new Expo host, camera recovery, repeated visits, background/resume, and account replacement before expanding art.
3. **Visual and interaction benchmark:** Replace placeholders with cohesive licensed assets, review the actual first-use scene on a phone, and tune sound, reduced motion, and touch behavior.
4. **Real discovery slice:** Add the new AWS-backed adult/child session, private photo, durable observation, Journal, and server-authoritative plant-patch path. Persist layout and the one play achievement separately.
5. **Small supervised playtest:** Check whether children explore without prompting, notice the habitat change, distinguish game illustrations from actual findings, and choose another outdoor investigation.

The first implementation assignment can use local fixtures and a local API. This specification does not require cloud provisioning or paid asset purchases before the interaction and integration prototypes are reviewed.

## Completion checks

- [ ] A new account opens an attractive clearing with all three characters before earning an unlock.
- [ ] Several objects respond to touch, and each animal has visibly distinct behavior.
- [ ] The robin challenge works from start to finish with sound disabled.
- [ ] The child can inspect the one robin detail and return to the clearing.
- [ ] An available object can move only to valid spots; the perch change produces a visible robin response.
- [ ] Layout and virtual challenge completion survive leaving and reopening the habitat for the same child.
- [ ] Virtual play alone cannot create a Journal observation or unlock the plant patch.
- [ ] One real eligible plant observation produces one patch and the reserved illustrated behavior after backend confirmation.
- [ ] Submission retries and revisiting the habitat do not duplicate progress or automatically replay the first-time celebration.
- [ ] The habitat change links to the real Journal record without making unsupported species or interaction claims.
- [ ] Offline capture survives restart and synchronizes safely when connectivity returns.
- [ ] Switching child accounts clears the prior child's scene, layout, achievements, media access, and delayed messages.
- [ ] Sound off, reduced motion, simple placement controls, and predictable exit/back navigation work.
- [ ] A supervised tester can explain what they did in the game, what they found outdoors, and why the habitat changed.
- [ ] A short device recording and a concise playtest report identify what children explored independently and any remaining blockers.

## Later scope

Additional habitats, a large species catalog, more investigations, a full discovery-lens system, a behavior album, seasonal variants, and broader customization follow evidence from this slice.

Free roaming, multiplayer, public visiting, chat, trading, construction economies, feeding obligations, streaks, and countdown-driven progress are outside this first playable.
