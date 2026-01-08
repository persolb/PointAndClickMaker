# Repository Guidelines

## Project Structure & Module Organization
- `generate_prompts.py` builds prompt files from `story_specific/screens.json` into `story_specific_gen/prompts/`.
- `render_screens.py` renders images from prompt files into `story_specific_gen/images/`.
- `story_specific/screens.json` is the source-of-truth scene graph and global style.
- `story_specific_gen/prompts/` contains generated `*.md` prompt files plus `index.json`.
- `story_specific_gen/images/` is expected output for generated PNGs and a `manifest.json`.

## Build, Test, and Development Commands
- `python generate_prompts.py --input story_specific/screens.json --out story_specific_gen/prompts --images story_specific_gen/images` generates prompt files and `story_specific_gen/prompts/index.json`.
- `python render_screens.py --prompts_dir story_specific_gen/prompts --index story_specific_gen/prompts/index.json --images_dir story_specific_gen/images --skip_existing` renders images for each screen and writes `story_specific_gen/images/manifest.json`.
- Optional: `python render_screens.py --style_ref story_specific_gen/images/HUB-01.png` adds a global style reference once the file exists.

## Environment & Configuration
- Use Python 3 and a local venv in `.venv/` (e.g., `python -m venv .venv`).
- Install dependencies manually (no lockfile in repo): `pip install openai`.
- Set `OPENAI_API_KEY` in your shell before running `render_screens.py`.

## Coding Style & Naming Conventions
- Python code uses 4-space indentation and standard `snake_case` naming.
- Data is modeled with `dataclasses` (`Screen`, `Connection`, `Job`) and typed annotations.
- JSON keys follow lower_snake or kebab-case as already present in `story_specific/screens.json`.

## Testing Guidelines
- No automated tests are present. If adding tests, prefer `pytest` and locate them under `tests/` with names like `test_generate_prompts.py`.

## Commit & Pull Request Guidelines
- This directory is not a git repository, so no commit history is available to infer conventions.
- If you initialize git, keep commits small and scoped (e.g., `feat: add render option`), and include a short PR description plus sample output images when changing prompts or rendering behavior.
