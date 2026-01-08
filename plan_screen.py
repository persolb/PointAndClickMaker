#!/usr/bin/env python3
"""Create arrangement prompt and sample sketch for a screen."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import shutil
import subprocess

from openai import OpenAI


FORMATTER_PROMPT = (
    "You are a formatting assistant. Convert a “PIXEL ART BACKGROUND PROMPT” text blob into a compact Markdown brief.\n\n"
    "INPUT\n"
    "You will receive raw text between the markers BEGIN INPUT and END INPUT.\n\n"
    "OUTPUT\n"
    "Return ONLY Markdown, with EXACTLY these sections in this order:\n\n"
    "## SCREEN ART NOTES\n"
    "- shot_type: <value>\n"
    "- camera: <value>\n"
    "- composition: <value>\n"
    "- important details: <value>\n\n"
    "## HOTSPOTS\n"
    "- <hotspot 1>\n"
    "- <hotspot 2>\n"
    "...\n\n"
    "## SCREENS TRANSITION POINTS\n"
    "- <Screen name 1>\n"
    "  - Direction: <direction>\n"
    "  - Transition: <transition>\n"
    "- <Screen name 2>\n"
    "  - Direction: <direction>\n"
    "  - Transition: <transition>\n"
    "...\n\n"
    "EXTRACTION RULES\n\n"
    "A) SCREEN ART NOTES\n"
    "- Locate the “SCREEN ART NOTES” section.\n"
    "- Extract ONLY these keys if present: shot_type, camera, composition.\n"
    "- Omit any other keys (example: lighting).\n"
    "- Preserve the value text as written.\n"
    "- Include details that are critical due to being explicitly called in the context.\n\n"
    "B) HOTSPOTS\n"
    "- Locate the “INTENDED HOTSPOTS (FOR SILHOUETTE / VALUE PLANNING)” section.\n"
    "- For each hotspot bullet:\n"
    "     - Take the name only, not desciptors after an em dash.\n"
    "     - If the name contains options, choose the most distinct once and use only that.\n"
    "     - If there is a location required (ex:to the right of something), include it afterward: - <hotspot 1> - <location description>\n"
    "     - Summarize the name, if unclear.\n\n"
    "C) SCREENS TRANSITION POINTS\n"
    "- Locate the “ADJACENT SCREENS AND TRANSITIONS” (or similar) section.\n"
    "- For each destination item:\n"
    "  1) Screen name:\n"
    "     - <Screen name> should be a simple text description of the destination\n"
    "     - From the header line like “- To KLE-02 — Bedroom”, keep ONLY the part AFTER “—”.\n"
    "     - Strip the “To …” ID portion completely.\n"
    "  2) Keep ONLY these subfields:\n"
    "     - Direction\n"
    "     - Transition\n"
    "     - Ignore “Continuity cues” and any other subfields.\n"
    "  3) Direction normalization (apply in this order):\n"
    "     - The should at most be one transition on each edge (left, right, forward/up, down/back)\n"
    "     - If there is more than one, some of them to be rephrased to be on screen. For example, a door or a building shown on the screen.\n"
    "          - Each screen has exactly four possible navigation edges: left, right, forward/up, back/down.\n"
    "          - Onscreen items may be navigated to.\n"
    "          - Each edge may be assigned to zero or one destination.\n"
    "          - If more than one destination maps to the same edge, only one may remain an edge transition; all others must be converted to on-screen hotspots. Proritize roads/paths for the edge, with doors/buildings on screen.\n"
    "          - For every transition, the assistant must explicitly map the raw Direction text to one canonical edge before output. 'left', 'right', 'forward' or 'up', 'back' or 'down', or onscreen\n\n"
    "  4) Transition:\n"
    "     - Normally, copy the Transition text verbatim (just the value), with no extra rewriting.\n\n"
    "     - If the direction included some critical detail which got filterd (like 'manhole', or 'door', or 'ladder') add it to the transition description"
    "  5) The final Markdown must not contain the same canonical edge label more than once across all transitions.\n\n"
    "FORMATTING RULES\n"
    "- No preface, no commentary, no extra sections.\n"
    "- If a required section is missing in the input, still output the section header, but leave it empty (no bullets) under that header.\n\n"
    "BEGIN INPUT\n"
)


ARRANGEMENT_PREFIX = (
    "Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.\n\n"
    "Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).\n\n"
    "Don't list the same exit or hotspot twice.\n\n"
    "Only make a line sketch so an artist has a guideline. No shading, colors, etc.\n\n"
    "# PROMPT\n"
)


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


def derive_paths(prompt_path: str, screen_id: str) -> tuple[str, str]:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        stem = os.path.splitext(base)[0]
        arr = os.path.join(os.path.dirname(prompt_path), f"{stem}-arrangement.md")
        sample = os.path.join(os.path.dirname(prompt_path), f"{stem}-sample.png")
        return arr, sample
    prefix = match.group(1)
    arr = os.path.join(os.path.dirname(prompt_path), f"{prefix}_{screen_id}-arrangement.md")
    sample = os.path.join(os.path.dirname(prompt_path), f"{prefix}_{screen_id}-sample.png")
    return arr, sample


def load_connected_sample_paths(screen_id: str, screens_path: str, prompts_dir: str) -> list[str]:
    try:
        with open(screens_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []

    connections = []
    for screen in data.get("screens", []):
        if screen.get("id") == screen_id:
            connections = screen.get("connections", [])
            break

    sample_paths = []
    for conn in connections:
        to_id = conn.get("to")
        if not to_id:
            continue
        try:
            prompt_path = find_prompt_file(prompts_dir, to_id)
        except SystemExit:
            continue
        _, sample_path = derive_paths(prompt_path, to_id)
        if os.path.exists(sample_path):
            sample_paths.append(sample_path)
    return sample_paths


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def collect_scene_context(scenes_path: str, screen_id: str) -> tuple[list[dict], set[str]]:
    # Load scenes tied to the screen and collect involved character IDs.
    if not os.path.exists(scenes_path):
        return [], set()
    data = load_json(scenes_path)
    scenes = data.get("scenes") if isinstance(data, dict) else data
    if not isinstance(scenes, list):
        return [], set()
    filtered = []
    character_ids: set[str] = set()
    for scene in scenes:
        if not isinstance(scene, dict) or scene.get("screenId") != screen_id:
            continue
        for entry in scene.get("characters", []) or []:
            if isinstance(entry, dict):
                char_id = entry.get("characterId")
                if char_id:
                    character_ids.add(str(char_id))
        scene = {
            k: v
            for k, v in scene.items()
            if k not in {"triggerLogic", "characters", "possibleOutcomes"}
        }
        filtered.append(scene)
    return filtered, character_ids


def collect_dialogue_context(
    dialogue_dir: str,
    scene_ids: set[str],
    character_ids: set[str],
) -> list[dict]:
    # Pull dialogue graphs for scenes on this screen, trimming noisy fields.
    if not os.path.isdir(dialogue_dir):
        return []
    entries = []
    for name in sorted(os.listdir(dialogue_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(dialogue_dir, name)
        try:
            payload = load_json(path)
        except Exception:
            continue
        graphs = payload.get("dialogueGraphs", [])
        if not isinstance(graphs, list):
            continue
        for graph in graphs:
            if not isinstance(graph, dict):
                continue
            scene_id = graph.get("sceneId")
            if not (scene_id and scene_id in scene_ids):
                continue
            cleaned = {
                k: v for k, v in graph.items() if k not in {"id", "startNodeId"}
            }
            nodes = cleaned.get("nodes", [])
            if isinstance(nodes, list):
                cleaned_nodes = []
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    speaker_id = node.get("speakerId")
                    if speaker_id in character_ids:
                        continue
                    filtered_node = {
                        k: v
                        for k, v in node.items()
                        if k
                        not in {
                            "id",
                            "type",
                            "speakerId",
                            "checks",
                            "onPassNext",
                            "onFailNext",
                            "next",
                            "effects",
                            "choices",
                        }
                    }
                    cleaned_nodes.append(filtered_node)
                cleaned["nodes"] = cleaned_nodes
            entries.append(cleaned)
    return entries


def choose_variant_auto(prompt_text: str, sample_paths: list[str], model: str) -> int:
    # Ask the LLM to pick the best sketch among generated variants.
    if not sample_paths:
        return 1
    count = len(sample_paths)
    auto_prompt = (
        "Choose the image that best matches the intent of the directions for this point and click game screen. "
        "Specific items may have moved. The most important thing is clarity to the player and artist "
        f"(when they make the final image). Return only the text 1 through {count}."
    )
    content = [
        {"type": "input_text", "text": prompt_text},
        {"type": "input_text", "text": auto_prompt},
    ]
    for path in sample_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
    )
    choice = rsp.output_text.strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= count:
            return idx
    raise SystemExit(f"Auto selection failed: {choice}")


def main() -> None:

    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", required=True, help="Screen id (e.g., HUB-01)")
    ap.add_argument("--prompts_dir", default=os.path.join("story_specific_gen", "prompts"))
    ap.add_argument("--screens_json", default=os.path.join("story_specific", "screens.json"))
    ap.add_argument("--scenes_json", default=os.path.join("story_specific_gen", "scenes.json"))
    ap.add_argument("--dialogue_dir", default=os.path.join("story_specific_gen", "dialogue"))
    ap.add_argument("--model", default="gpt-5.2")
    ap.add_argument("--image_model", default="gpt-image-1.5")
    ap.add_argument("--n", type=int, default=1, help="Number of samples to generate")
    ap.add_argument("--open_gimp", action="store_true", help="Open sample variants in GIMP")
    ap.add_argument("--redo", action="store_true", help="Regenerate arrangement and samples")
    ap.add_argument("--debug", type=int, default=0, help="Debug step (1 writes formatter prompt to debug.log and exits)")
    ap.add_argument("--yolo", action="store_true", help="Auto-select best variant without prompting")
    args = ap.parse_args()

    prompt_path = find_prompt_file(args.prompts_dir, args.generate)
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()

    connected_samples = load_connected_sample_paths(
        args.generate, args.screens_json, args.prompts_dir
    )
    arrangement_path, sample_path = derive_paths(prompt_path, args.generate)
    scenes_for_screen, scene_character_ids = collect_scene_context(
        args.scenes_json, args.generate
    )
    scene_ids = {s.get("id") for s in scenes_for_screen if isinstance(s, dict) and s.get("id")}
    dialogue_graphs = collect_dialogue_context(
        args.dialogue_dir, scene_ids, scene_character_ids
    )
    if scenes_for_screen:
        prompt_text += "\n\n-- SCENE CONTEXT --\n"
        prompt_text += json.dumps(scenes_for_screen, indent=2, ensure_ascii=True)
    if dialogue_graphs:
        prompt_text += "\n\n-- DIALOGUE CONTEXT --\n"
        prompt_text += json.dumps(dialogue_graphs, indent=2, ensure_ascii=True)
    variants = max(1, args.n)

    if os.path.exists(arrangement_path) and os.path.exists(sample_path):
        print(f"  Skipping existing arrangement and sample for {args.generate}")
        return

    if args.redo:
        base_dir = os.path.dirname(arrangement_path)
        arr_base = os.path.splitext(os.path.basename(arrangement_path))[0]
        samp_base = os.path.splitext(os.path.basename(sample_path))[0]
        for name in os.listdir(base_dir):
            if name.startswith(arr_base) and name.endswith(".md"):
                try:
                    os.remove(os.path.join(base_dir, name))
                except FileNotFoundError:
                    pass
            if name.startswith(samp_base) and name.endswith(".png"):
                try:
                    os.remove(os.path.join(base_dir, name))
                except FileNotFoundError:
                    pass

    while True:
        arrangement_variants = []
        sample_variants = []

        if os.path.exists(arrangement_path):
            with open(arrangement_path, "r", encoding="utf-8") as f:
                arrangement_texts = [f.read()] * variants
            print("  Using existing arrangement for samples.")
        else:
            formatter_input = FORMATTER_PROMPT + prompt_text + "\nEND INPUT"
            if args.debug == 1:
                with open("debug.log", "w", encoding="utf-8") as f:
                    f.write(formatter_input)
                print("Debug step 1 complete: wrote formatter prompt to debug.log")
                return

            def _format_one(idx: int) -> str:
                print(f"  Requesting LLM formatter ({idx}/{variants})...")
                content = [{"type": "input_text", "text": formatter_input}]
                if connected_samples:
                    content.append({
                        "type": "input_text",
                        "text": "REFERENCE IMAGES (adjacent screens for continuity):",
                    })
                    for path in connected_samples:
                        with open(path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("ascii")
                        content.append({
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{b64}",
                        })
                client = OpenAI()
                response = client.responses.create(
                    model=args.model,
                    input=[{"role": "user", "content": content}],
                    reasoning={"effort": "high"},
                )
                markdown = response.output_text.strip()
                return ARRANGEMENT_PREFIX + "\n" + markdown + "\n"

            with concurrent.futures.ThreadPoolExecutor(max_workers=variants) as executor:
                futures = {executor.submit(_format_one, i): i for i in range(1, variants + 1)}
                arrangement_texts = [None] * variants
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    arrangement_texts[idx - 1] = future.result()

        for i, arrangement_text in enumerate(arrangement_texts, start=1):
            arr_path = arrangement_path.replace(
                "-arrangement.md", f"-arrangement-{i}.md"
            )
            with open(arr_path, "w", encoding="utf-8") as f:
                f.write(arrangement_text)
            arrangement_variants.append(arr_path)
            print(f"    Wrote {arr_path}")

        def _render_one(idx: int, arrangement_text: str) -> tuple[int, str]:
            print(f"  Requesting LLM sketch ({idx}/{variants})...")
            client = OpenAI()
            rsp = client.images.generate(
                model=args.image_model,
                prompt=arrangement_text,
                n=1,
                size="1536x1024",
                quality="high",
                output_format="png",
            )
            img_b64 = rsp.data[0].b64_json
            img_bytes = base64.b64decode(img_b64)
            sample_path_i = sample_path.replace("-sample.png", f"-sample-{idx}.png")
            with open(sample_path_i, "wb") as f:
                f.write(img_bytes)
            return idx, sample_path_i

        with concurrent.futures.ThreadPoolExecutor(max_workers=variants) as executor:
            futures = {
                executor.submit(_render_one, i, arrangement_texts[i - 1]): i
                for i in range(1, variants + 1)
            }
            results = [None] * variants
            for future in concurrent.futures.as_completed(futures):
                idx, path = future.result()
                results[idx - 1] = path

        for path in results:
            sample_variants.append(path)
            print(f"    Wrote {path}")

        if variants == 1:
            chosen_arr = arrangement_variants[0]
            chosen_sample = sample_variants[0]
            os.replace(chosen_arr, arrangement_path)
            os.replace(chosen_sample, sample_path)
            print(f"  Finalized plan")
            print(f"    Wrote {arrangement_path}")
            print(f"    Wrote {sample_path}")
            break

        if not args.yolo:
            first_arrangement = arrangement_variants[0]
            try:
                with open(first_arrangement, "r", encoding="utf-8") as f:
                    text = f.read()
                blocks = []
                for header in ("## HOTSPOTS", "## SCREENS TRANSITION POINTS"):
                    if header in text:
                        start = text.index(header)
                        tail = text[start:]
                        end = len(tail)
                        for marker in ("## HOTSPOTS", "## SCREENS TRANSITION POINTS"):
                            if marker == header:
                                continue
                            pos = tail.find(marker)
                            if pos != -1:
                                end = min(end, pos)
                        blocks.append(tail[:end].rstrip())
                if blocks:
                    print("  Context for selection:")
                    for block in blocks:
                        for line in block.splitlines():
                            print(f"    {line}")
                print("  Select a variant to keep (0 to retry):")
                for i, path in enumerate(sample_variants, start=1):
                    print(f"    {i}: {path}")
            except FileNotFoundError:
                pass

        if args.open_gimp and not args.yolo:
            gimp = shutil.which("gimp")
            if gimp:
                for path in sample_variants:
                    subprocess.Popen([gimp, path])
            else:
                print("  Warning: gimp not found; cannot open samples.")
        if args.yolo:
            choice = str(choose_variant_auto(prompt_text, sample_variants, args.model))
        else:
            os.system('printf "\\a"')
            choice = input("  Enter choice number: ").strip()
        if choice.isdigit():
            pick = int(choice)
            if pick == 0:
                for path in arrangement_variants + sample_variants:
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                continue
            if 1 <= pick <= variants:
                chosen_arr = arrangement_variants[pick - 1]
                chosen_sample = sample_variants[pick - 1]
                os.replace(chosen_arr, arrangement_path)
                os.replace(chosen_sample, sample_path)
                for path in arrangement_variants:
                    if path == chosen_arr:
                        continue
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                for path in sample_variants:
                    if path == chosen_sample:
                        continue
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass
                print(f"  Wrote {arrangement_path}")
                print(f"  Wrote {sample_path}")
                break
        print(f"Invalid choice. Enter 0 or a number between 1 and {variants}.")


if __name__ == "__main__":
    main()
