#!/usr/bin/env python3
"""Pipeline orchestrator for prompt, planning, rendering, and segmentation."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from typing import List, Tuple

from openai import OpenAI


def run_cmd(args: List[str], timeout: int | None = None) -> None:
    # Run a subprocess with optional timeout and environment passthrough.
    print(f"  Running: {' '.join(args)}")
    env = os.environ.copy()
    if args and args[0] == "python":
        env["PYTHONUNBUFFERED"] = "1"
    try:
        subprocess.run(args, check=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"Command timed out after {exc.timeout}s: {' '.join(args)}") from exc


def load_index(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("order", [])


def load_screen_names(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {s.get("id"): s.get("name", s.get("id")) for s in data.get("screens", [])}


def find_prompt_file(prompts_dir: str, screen_id: str) -> str:
    candidates = []
    for name in os.listdir(prompts_dir):
        if not name.lower().endswith(".md"):
            continue
        if name.endswith(f"_{screen_id}.md"):
            candidates.append(name)
    if not candidates:
        raise SystemExit(f"No prompt file found for {screen_id} in {prompts_dir}")
    candidates.sort()
    return os.path.join(prompts_dir, candidates[0])


def derive_arrangement_path(prompt_path: str, screen_id: str) -> str:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        stem = os.path.splitext(base)[0]
        return os.path.join(os.path.dirname(prompt_path), f"{stem}-arrangement.md")
    prefix = match.group(1)
    return os.path.join(
        os.path.dirname(prompt_path), f"{prefix}_{screen_id}-arrangement.md"
    )


def derive_sample_path(prompt_path: str, screen_id: str) -> str:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        return os.path.join(os.path.dirname(prompt_path), f"{screen_id}-sample.png")
    prefix = match.group(1)
    return os.path.join(
        os.path.dirname(prompt_path), f"{prefix}_{screen_id}-sample.png"
    )


def extract_without_global_style(text: str) -> str:
    lines = text.splitlines()
    out = []
    skip = False
    for line in lines:
        if line.strip() == "GLOBAL STYLE":
            skip = True
            continue
        if skip and line.strip() == "SCREEN ART NOTES":
            skip = False
            out.append(line)
            continue
        if not skip:
            out.append(line)
    return "\n".join(out).strip()


def choose_arrangement_auto(prompt_body: str, sample_paths: List[str], model: str) -> int:
    auto_prompt = (
        "Choose the image that best matches the intent of the directions for this point and click game screen. "
        "Specific items may have moved. The most important thing is clarity to the player and artist "
        "(when they make the final image). Return only the text 1, 2, or 3."
    )
    content = [
        {"type": "input_text", "text": prompt_body},
        {"type": "input_text", "text": auto_prompt},
    ]
    for path in sample_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
    print("  Requesting LLM arrangement selection...")
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    choice = rsp.output_text.strip()
    if choice not in {"1", "2", "3"}:
        raise SystemExit(f"Auto selection failed: {choice}")
    return int(choice)


def run_plan(
    screen_id: str,
    prompts_dir: str,
    auto: bool,
    model: str,
    image_model: str,
    arrangements: int,
    open_gimp: bool,
    redo: bool,
) -> None:
    # Orchestrate plan_screen for a single screen.
    prompt_path = find_prompt_file(prompts_dir, screen_id)
    print(f"  Planning arrangement (n={arrangements})...")
    args = [
        "python",
        "plan_screen.py",
        "--generate",
        screen_id,
        "--model",
        model,
        "--image_model",
        image_model,
        "--n",
        str(arrangements),
    ]
    args.append("--yolo")
    if redo:
        args.append("--redo")
    if open_gimp:
        args.append("--open_gimp")
    run_cmd(args)


def run_render(screen_id: str, auto: bool, n: int, redo: bool) -> None:
    # Orchestrate render_screens for a single screen.
    args = ["python", "render_screens.py", "--generate", screen_id, "--n", str(n)]
    if redo:
        args.append("--redo")
    args.append("--auto_select")
    run_cmd(args)


def load_character_ids(characters_json: str) -> list[str]:
    with open(characters_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = []
    for entry in data.get("characters", []):
        if not isinstance(entry, dict):
            continue
        char_id = entry.get("id")
        if isinstance(char_id, str) and char_id.strip():
            ids.append(char_id.strip())
    return ids


def main() -> None:
    # End-to-end pipeline execution.
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", default=None, help="Only generate a single screen id")
    ap.add_argument("--redo_mode", default="all", choices=["all", "plan", "final"], help="Redo scope")
    ap.add_argument("--redo", action="store_true", help="Force re-render when --generate is used")
    ap.add_argument("--redo_plan", action="store_true", help="Force re-run plan_screen")
    ap.add_argument("--auto", action="store_true", help="Auto-select arrangements and renders")
    ap.add_argument("--auto_crit", action="store_true", help="Enable critique/refine cycle")
    ap.add_argument("--open_gimp", action="store_true", help="Open GIMP for plan_screen samples")
    ap.add_argument("--n", type=int, default=3, help="Variants for plan and render")
    ap.add_argument("--model", default="gpt-5.2", help="LLM model for text")
    ap.add_argument("--image_model", default="gpt-image-1.5", help="Image model for sketches")
    ap.add_argument("--prompts_dir", default=os.path.join("story_specific_gen", "prompts"))
    ap.add_argument("--only_script", action="store_true", help="Run only script_plan.py and exit")
    ap.add_argument("--yolo", action="store_true", help="Pass --yolo to script_plan.py")
    ap.add_argument("--improve_timeout", type=int, default=1800, help="Timeout (s) for improve_dialog.py")
    args = ap.parse_args()

    script_cmd = ["python", "script_plan.py"]
    if args.yolo:
        script_cmd.append("--yolo")
    run_cmd(script_cmd)
    if args.only_script:
        return

    run_cmd(
        [
            "python",
            "generate_prompts.py",
            "--input",
            os.path.join("story_specific", "screens.json"),
            "--out",
            args.prompts_dir,
            "--images",
            os.path.join("story_specific_gen", "images"),
        ]
    )

    if args.generate:
        screens = [args.generate]
    else:
        screens = load_index(os.path.join(args.prompts_dir, "index.json"))

    if args.yolo:
        args.n = 1

    name_map = load_screen_names(os.path.join("story_specific", "screens.json"))

    total = max(len(screens), 1)
    for idx, screen_id in enumerate(screens, start=1):
        screen_name = name_map.get(screen_id, screen_id)
        percent = int((idx - 1) / total * 100)
        print(f"\n== {screen_id} - {screen_name} == [{percent:02d}%]")
        if args.redo_mode in {"all", "plan"}:
            run_plan(
                screen_id,
                args.prompts_dir,
                args.auto,
                args.model,
                args.image_model,
                args.n,
                args.open_gimp,
                args.redo_plan,
            )
        if args.redo_mode in {"all", "final"}:
            run_render(screen_id, args.auto, args.n, args.redo)

    run_cmd(["python", "generate_character_image.py", "--character", "all"])
    character_ids = load_character_ids(os.path.join("story_specific_gen", "characters.json"))
    for char_id in character_ids:
        run_cmd(["python", "improve_dialog.py", "--c", char_id], timeout=args.improve_timeout)
    for screen_id in screens:
        run_cmd(["python", "generate_music.py", "--screen", screen_id])
    run_cmd(["python", "update_page.py"])


if __name__ == "__main__":
    main()
