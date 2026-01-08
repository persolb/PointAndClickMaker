#!/usr/bin/env python3
"""Generate layout planning thoughts for a screen prompt."""

from __future__ import annotations

import argparse
import base64
import os
import re

from openai import OpenAI


PROMPT_PREFIX = (
    "Consider how to lay out all the elements described on this screen. "
    "Everything needs to be included, with clear segregation between the various "
    "navigation hotspots and item hotspots. "
    "Make sure that every item is shown. "
    "Make sure that all navigation is shown: Each side of the screen should only have one 'exit'. "
    "If you want an extra exit, show it on screen (via a door/pipe/road/building/etc) instead of being at the edge."
)

CRITIQUE_PROMPT = (
    "Critically evaluate the attached image(s) from another LLM against the prompt. "
    "It is critical that a player be able to determine which areas to click to do "
    "certain things or go certain places. "
    "Any ambigutiy in the prompt (such as options on what something looks like) need to be removed; just pick one. "
    "This is for a point and click game."
)

FIX_PROMPT_PREFIX = (
    "Output a prompt that fixes these issues. The prompt must be clear that this is a planning sketch with object & path outlines, not final artwork. This final prompt is all that will be sent, so it needs to provide all relevant info. Provide nothing but the final prompt."
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


def extract_screen_art_notes(text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "SCREEN ART NOTES":
            start = i
            break
    if start is None:
        raise SystemExit("SCREEN ART NOTES section not found")
    return "\n".join(lines[start:]).strip()


def derive_output_path(prompt_path: str, screen_id: str) -> str:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        stem = os.path.splitext(base)[0]
        return os.path.join(os.path.dirname(prompt_path), f"{stem}_arrangement_thought.md")
    prefix = match.group(1)
    return os.path.join(
        os.path.dirname(prompt_path), f"{prefix}_{screen_id}_arrangement_thought.md"
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", required=True, help="Screen id (e.g., HUB-01)")
    ap.add_argument("--prompts_dir", default="prompts")
    ap.add_argument("--model", default="gpt-5.2")
    ap.add_argument("--image_model", default="gpt-image-1.5")
    ap.add_argument("--redo", action="store_true", help="Regenerate arrangement and samples")
    ap.add_argument("--auto", action="store_true", help="Auto-select best sample via LLM")
    ap.add_argument("--skip_crit", action="store_true", help="Skip critique/refine cycle")
    args = ap.parse_args()

    prompt_path = find_prompt_file(args.prompts_dir, args.generate)
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_text = f.read()

    out_path = derive_output_path(prompt_path, args.generate)
    sample_path = derive_sample_path(prompt_path, args.generate)
    if args.redo:
        if os.path.exists(out_path):
            os.remove(out_path)
        if os.path.exists(sample_path):
            os.remove(sample_path)
    if not os.path.exists(out_path):
        section = extract_screen_art_notes(prompt_text)
        full_prompt = PROMPT_PREFIX + "\n\n" + section
        client = OpenAI()
        print("  Requesting LLM arrangement thoughts...")
        response = client.responses.create(
            model=args.model,
            input=full_prompt,
        )
        output_text = response.output_text.strip()
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_text + "\n")
        print(f"  Wrote {out_path}")
    else:
        print(f"  Skipping existing arrangement: {out_path}")

    if not os.path.exists(sample_path):
        with open(out_path, "r", encoding="utf-8") as f:
            arrangement_text = f.read().strip()
        prompt_body = extract_without_global_style(prompt_text)
        image_prompt = (
            "This is a schematic layout sketch for a point-and-click adventure game screen. \n\n"
            + "Hand-drawn style, monochrome or limited color, instructional diagram, not polished art. \n\n"
            + "Composition should be readable and diagram-like, prioritizing layout and navigation clarity, not polish. No color, no shading beyond light cross-hatching. \n\n"
            + "Include handwritten labels with arrows pointing to exits. Clearly indicate all exits and hotspots. \n\n"
            + "Clarity and layout are more important than realism or detail. \n\n\n\n"
            + prompt_body
            + "\n\n"
            + arrangement_text
        )
        refine_prompt = (
            "Rewrite the following prompt to better achieve a very simple, readable layout sketch. "
            "Keep it short, imperative, and focused on clarity. Do not add new content. Return only the prompt, no leading/trailing commentary. The prompt must include descriptions of each shape, navigational prompt, and location.\n\n"
            +"Limit visual complexity: use large, simple silhouettes only.\n\n"
            + "No more than about 10 major shapes in the entire scene.\n\n"
            + "Textures and lighting should not be rendered.\n\n"
            + "\n\n\n\n"
            + image_prompt
        )
        client = OpenAI()
        base_dir = os.path.dirname(sample_path)
        base_name = os.path.splitext(os.path.basename(sample_path))[0]
        variants = 3

        def generate_variants(prompt: str) -> list[str]:
            print("  Requesting LLM sample sketches...")
            rsp = client.images.generate(
                model=args.image_model,
                prompt=prompt,
                n=variants,
                size="1536x1024",
                quality="high",
                output_format="png",
            )
            paths = []
            for i, item in enumerate(rsp.data, start=1):
                img_b64 = item.b64_json
                img_bytes = base64.b64decode(img_b64)
                vpath = os.path.join(base_dir, f"{base_name}-v{i}.png")
                with open(vpath, "wb") as f:
                    f.write(img_bytes)
                paths.append(vpath)
                print(f"  Wrote {vpath}")
            return paths

        def critique_and_refine(prompt_text: str, images: list[str]) -> str:
            content = [
                {"type": "input_text", "text": prompt_text},
                {"type": "input_text", "text": CRITIQUE_PROMPT},
            ]
            for path in images:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                content.append(
                    {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}
                )
            print("  Requesting LLM critique...")
            critique_rsp = client.responses.create(
                model=args.model,
                input=[{"role": "user", "content": content}],
            )
            critique_text = critique_rsp.output_text.strip()
            print("  Requesting LLM fixed prompt...")
            fix_rsp = client.responses.create(
                model=args.model,
                input=FIX_PROMPT_PREFIX + "\n\n" + critique_text,
            )
            return fix_rsp.output_text.strip()

        print("  Requesting LLM prompt refinement...")
        refine_rsp = client.responses.create(
            model=args.model,
            input=refine_prompt,
        )
        refined_prompt = refine_rsp.output_text.strip()
        variant_paths = generate_variants(refined_prompt)
        if not args.skip_crit:
            refined_prompt = critique_and_refine(refined_prompt, variant_paths)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(refined_prompt + "\n")
            print(f"  Wrote {out_path}")
            for vpath in variant_paths:
                try:
                    os.remove(vpath)
                except FileNotFoundError:
                    pass
            variant_paths = generate_variants(refined_prompt)

        if args.auto:
            auto_prompt = (
                "Choose the image that best matches the intent of the directions for this point and click game screen. "
                "Specific items may have moved. The most important thing is clarity to the player and artist "
                "(when they make the final image). Return only the text 1, 2, or 3."
            )
            content = [
                {"type": "input_text", "text": prompt_body},
                {"type": "input_text", "text": auto_prompt},
            ]
            for path in variant_paths:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                content.append(
                    {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}
                )
            print("  Requesting LLM auto-selection...")
            auto_rsp = client.responses.create(
                model=args.model,
                input=[{"role": "user", "content": content}],
            )
            choice = auto_rsp.output_text.strip()
            if choice not in {"1", "2", "3"}:
                raise SystemExit(f"Auto selection failed: {choice}")
            pick = int(choice)
            chosen = variant_paths[pick - 1]
            os.replace(chosen, sample_path)
            for vpath in variant_paths:
                if vpath == chosen:
                    continue
                try:
                    os.remove(vpath)
                except FileNotFoundError:
                    pass
            print(f"  Wrote {sample_path}")
        else:
            while True:
                print("    Select a sample variant to keep (0 to retry):")
                for i, vpath in enumerate(variant_paths, start=1):
                    print(f"    {i}: {vpath}")
                choice = input("    Enter choice number: ").strip()
                if choice.isdigit():
                    pick = int(choice)
                    if pick == 0:
                        for vpath in variant_paths:
                            try:
                                os.remove(vpath)
                            except FileNotFoundError:
                                pass
                        variant_paths = []
                        print("  Requesting LLM prompt refinement...")
                        refine_rsp = client.responses.create(
                            model=args.model,
                            input=refine_prompt,
                        )
                        refined_prompt = refine_rsp.output_text.strip()
                        variant_paths = generate_variants(refined_prompt)
                        if not args.skip_crit:
                            refined_prompt = critique_and_refine(refined_prompt, variant_paths)
                            with open(out_path, "w", encoding="utf-8") as f:
                                f.write(refined_prompt + "\n")
                            print(f"  Wrote {out_path}")
                            for vpath in variant_paths:
                                try:
                                    os.remove(vpath)
                                except FileNotFoundError:
                                    pass
                            variant_paths = generate_variants(refined_prompt)
                        continue
                    if 1 <= pick <= variants:
                        chosen = variant_paths[pick - 1]
                        os.replace(chosen, sample_path)
                        for vpath in variant_paths:
                            if vpath == chosen:
                                continue
                            try:
                                os.remove(vpath)
                            except FileNotFoundError:
                                pass
                        print(f"  Wrote {sample_path}")
                        break
                print(f"    Invalid choice. Enter 0 or a number between 1 and {variants}.")
    else:
        print(f"  Skipping existing sample: {sample_path}")


if __name__ == "__main__":
    main()
