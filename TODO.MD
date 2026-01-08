# DONE?

## hotspot geometry
how is script_plan filling in hotspots.json with things like
"geometry": {
            "type": "rect",
            "x": 1080,
            "y": 410,
            "w": 140,
            "h": 210
          },
But it has no info. Need to tell the LLM to leave this blank.

## clean up folders
Move the below to a {project}_gen folder. The generation and any subsequent reference. For now, assume the {project} is "story_specific"
- scenes.json
- characters.json
- hotspots.json
- dialogue folder
- prompts folder
- images folder

# TODO



# Budget Track

## 2025-12-30

Started 8:28pm
 openai $206.51
 gemini $49
Doing screen arrangement/plan/render/characters
Done 9:45pm
 openai $223.03
 gemini (not updated but said 72)
Total: $39.52
(some music in parrallel)

## 2025-12-30
Started 11:21am - 223.85+72
Redoing screen arrangement/plan/render/characters
Done by 13:36pm - 240.56 + (not updated)

was doing music and coding at same time with OpenAI

## General

Add the time (like "[10% - 01:33:29 left]" to "Requesting LLM to generate dialogue for" as well.

Review how well the (screen)-sample.png matches the text originally sent to the LLM, and what arrangement popped out.
> I tried to take a bunch of text, summarize it into an arrangement markdown description, and then generate an image. Evaluate how well the image matches the need of the scene. 'debug' is all the original context. 'arrangement' is the art direction for generation. The png is the output.

> There is one minor point of abstraction worth noting regarding the text versus the visual representation: Car Trunk vs. Equipment Case: The text refers to this hotspot as "Car trunk / equipment case" and the narrative context implies an open vehicle trunk containing the case (e.g., "The car trunk is open. An equipment case sits inside..."). The image abstracts this by showing only the equipment case sitting directly on the road pavement, with no vehicle present.

>Fix:     "If a hotspot name describes a container-content relationship or a specific state (often separated by a slash, e.g., 'Desk drawer / important papers' or 'Wall / loose brick'), extract the primary interactable item as the hotspot name. Move the container or state description to the trailing location descriptor to ensure it is visualized properly."

## Next schemas that are needed for a point-and-click

### 1) `items.schema.json`

If the game has inventory at all, you’ll want a first-class item model rather than encoding items as ad‑hoc flags.

Include concepts like:

* `id`, `name`, `description`
* `iconAssetId` (or sprite reference)
* `tags` (KEY, TOOL, EVIDENCE, QUEST, CONSUMABLE, etc.)
* `examineText` / `examineSceneId`
* interaction affordances (optional, if you want): default verb, allowed verbs

Related: if you support “use item on hotspot,” you may also want a small `itemInteractions` section (or a separate schema, see below).

---

### 2) `state.schema.json` (or `worldState.schema.json`)

Right now you reference state keys everywhere (`conditions`, `effects`) but you don’t have a schema that *defines* them.

This schema is the “type system” for your game state:

* `boolFlags`: list of allowed boolean keys
* `counters`: numeric keys with min/max
* `enums`: keys with allowed values (e.g., `accessLevel`)
* default values
* migration/version field (optional but helpful)

Benefits:

* prevents typos across scenes/dialogue/hotspots
* lets you validate conditions/effects statically
* makes save/load safer

---

### 3) `assets.schema.json` (asset manifest)

Once you have multiple data files, you need a single authoritative place mapping IDs to actual files.

Include:

* backgrounds: `screenId -> backgroundImagePath`
* hotspot masks (if you use `mask` geometry)
* sprites, portraits, UI icons
* audio IDs
* animation IDs

This is how you catch broken references in CI before runtime.

---

## Later down the road

### 4) `quests.schema.json` (or `objectives.schema.json`)

Even if you don’t call them quests, you likely want:

* objective journal entries
* step progression
* completion triggers

Model:

* `id`, `title`, `description`
* `steps[]` with `conditions` and `completionEffects`
* `visibilityConditions`
* `factionLocks` (optional)

This also helps pacing and player guidance, even in a non-linear hub game.

---

### 5) `navigation.schema.json` (walkboxes, z-planes, anchors)

Hotspots handle clicking, but point-and-click also needs “where can I walk and stand.”

Typical per-screen navigation data:

* `walkablePolygons[]` (or a small bitmap mask reference)
* `walkToAnchors` (named points for “stand here to interact”)
* `cameraBounds` if you ever support scrolling rooms
* `zPlanes` / `occluders` so the character can walk behind foreground props

You can fold some of this into `hotspots.json`, but keeping it separate is cleaner.

---

### 6) `stats.schema.json` (skills, reputation, checks)

You already support `dc` and check types in dialogue. To make checks consistent across the game, define:

* skill list (e.g., `science`, `deception`, `engineering`)
* how values are computed (base + bonuses)
* reputation model per faction:

  * range (e.g., -100..100)
  * named tiers (“Hostile”, “Neutral”, “Trusted”)
  * thresholds

This gives you predictable tuning and avoids hand-wavy numbers.

---

### 7) `cutscenes.schema.json` (or `sequences.schema.json`)

For non-interactive moments:

* camera holds, fades
* character entrance/exit
* scripted animations
* timed dialogue without choices

You can implement these as specialized scenes, but a dedicated schema is often easier for tooling.

---

### 8) `localization.schema.json`

If you expect rewrites or multiple languages:

* store `locKey` everywhere, and keep strings in `strings.json`
* validate that every `locKey` exists
* support formatting tokens

Even for English-only, this helps large rewrites.

---

### 9) `savegame.schema.json`

Even if saves are opaque blobs, a schema helps if you want to support:

* forward-compatible saves
* version migrations
* debug inspection tools

At minimum:

* `version`
* `worldState`
* inventory
* visited screens / discovered evidence
* quest/objective progress


