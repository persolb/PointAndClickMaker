# Image Gen Pipeline

This repo generates point‑and‑click screens, hotspots, dialogue graphs, and character images.

## Minimal Story Inputs

Required files before you run anything:

- `story_specific/story.md`
- `story_specific/screens.json`
- `story_specific/dialog_style.md`
- `story_specific/character_style.json`
- `story_specific/art_style.json`

Generated outputs are written to `story_specific_gen/`.

## API Keys

Set these before running LLM tasks:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)

## Pipeline (Recommended)

```
python pipeline.py
```
--only_script to review what it produced, before continuing with durther generation
--yolo to have it just proceed without asking user's opinion

Pipeline runs, in order:

1) `python script_plan.py`
2) `python generate_prompts.py --input story_specific/screens.json --out story_specific_gen/prompts --images story_specific_gen/images`
3) `python plan_screen.py` (per screen)
4) `python render_screens.py` (per screen; runs `segment.py` afterward)
5) `python generate_character_image.py --character all`
6) `python update_page.py`


## Manual Order (if you need to run steps individually)

1) **Narrative scaffolding**

```
python script_plan.py
```

Generates `scenes.json`, `characters.json`, `hotspots.json`, and `dialogue/SCN_<sceneId>.json`.

Dialog is drafted with GPT 5.2 instant response, then formatted with high thinking

2) **Screen prompts**

```
python generate_prompts.py --input story_specific/screens.json --out story_specific_gen/prompts --images story_specific_gen/images
```

3) **Layout sketches** (optional but recommended)

```
python plan_screen.py --generate HUB-01
```

4) **Render screens**

```
python render_screens.py
```

5) **Segmentation masks** (runs automatically after render; can also run manually)

```
python segment.py HUB-01
```

6) **Character images** (optional)

```
python generate_character_image.py --character all
```

7) **Copy outputs to the game repo**

```
python update_page.py
```

## Notes

- `pipeline.py` runs `script_plan.py` → `generate_prompts.py` → `plan_screen.py` → `render_screens.py` → `generate_character_image.py --character all` → `update_page.py`.
- `render_screens.py` will skip existing final images unless `--redo` is used.
- `generate_character_image.py` skips characters with an existing `-overview.png`.
