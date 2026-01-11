"""
Generate character sprite sheets and derived assets (directions, masks, talking heads).
"""
import argparse
import base64
import concurrent.futures
import fnmatch
import json
import os
import numpy as np
import re
import sys
import random
import subprocess
import time
import io
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


BASELINE_PROMPT = """Create a **single baseline character sprite sheet** following the visual rules defined in `story_specific/character_style.json`.

**Purpose**
This image establishes the canonical look of the character before any narrative-specific or costume variations are applied.

**Art style**

* Pixel art
* Native resolution: 640x480
* Nearest-neighbor only; no anti-aliasing, no blur, no gradients
* Clean pixel clusters; zero single-pixel noise

**Character constraints**

* Adult human
* Realistic anatomy and body proportions
* No dramatic poses

**Lighting and shading**

* Upper-left light source
* Lower-right shadow
* Maximum of 3 tones per material
* Minimal highlights; no glossy effects
* No dithering on the character

**Sprite layout**
Generate a **3x3 grid** of evenly spaced sprites on a transparent background, aligned to a consistent ground line:

Row 1 (back views):

* Facing away left
* Facing directly away
* Facing away right

Row 2 (side views):

* Facing left in profile with face visible
* Center cell a face portrait three-quarter view facing left
* Facing right in profile with face visible

Row 3 (front/three-quarter views):

* Facing camera left (three-quarter)
* Facing forward
* Facing camera right (three-quarter)

**Pose rules**

* Standing idle posture
* Arms relaxed at sides
* No motion blur or action frames
* Consistent scale and proportions across all nine cells

**Output requirements**

* One image containing the full 3×3 grid
* Pixel-perfect alignment
* No text, labels, or UI elements
"""


# Matches render_screens.py for higher-quality output (higher cost).
GEMINI_DEFAULT_MODEL = "gemini-3-pro-image-preview"
GPT_DEFAULT_MODEL = "gpt-5.2"
OPENAI_IMAGE_DEFAULT_MODEL = "gpt-image-1"


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def drop_json_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return {k: drop_json_key(v, key) for k, v in value.items() if k != key}
    if isinstance(value, list):
        return [drop_json_key(item, key) for item in value]
    return value


def remove_story_section(text: str, header: str) -> str:
    lines = text.splitlines()
    out: List[str] = []
    skipping = False
    for line in lines:
        if not skipping and line.strip() == header:
            skipping = True
            continue
        if skipping and line.startswith("## ") and line.strip() != header:
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).strip()


def remove_story_sections(text: str, headers: List[str]) -> str:
    updated = text
    for header in headers:
        updated = remove_story_section(updated, header)
    return updated


def llm_generate_text(prompt: str, model: str) -> str:
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )
    return rsp.output_text.strip()


def llm_generate_json(prompt: str, model: str) -> Dict[str, Any]:
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        text={"format": {"type": "json_object"}},
    )
    return json.loads(rsp.output_text.strip())


def _image_to_png_file(img: "Image.Image") -> Tuple[str, io.BytesIO, str]:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ("image.png", buf, "image/png")


def llm_generate_image(
    *,
    provider: str,
    model: str,
    prompt: str,
    images: Optional[List["Image.Image"]] = None,
    mask: Optional["Image.Image"] = None,
    size: str = "1024x1024",
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    quality: str = "high",
    background: Optional[str] = None,
    timeout: int = 60,
    retries: int = 3,
) -> bytes:
    # Provider-agnostic image generation with retries and timeout handling.
    images = images or []
    # prompt = "Return the reference image unchanged." # for test only
    if provider == "openai":
        for attempt in range(1, retries + 1):
            try:
                client = OpenAI(timeout=timeout)
                if images:
                    image_bytes = [_image_to_png_file(img) for img in images]
                    kwargs: Dict[str, Any] = {
                        "model": model,
                        "image": image_bytes,
                        "prompt": prompt,
                        "size": size,
                        "quality": quality,
                        "background": background,
                    }
                    if mask is not None:
                        kwargs["mask"] = _image_to_png_file(mask)
                    rsp = client.images.edit(**kwargs)
                else:
                    rsp = client.images.generate(
                        model=model,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        background=background,
                    )
                img_b64 = rsp.data[0].b64_json
                return base64.b64decode(img_b64)
            except Exception as exc:
                if attempt >= retries:
                    raise
                print(f"Warning: OpenAI image attempt {attempt} failed: {exc}")

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before running.")
    try:
        from google import genai
        from google.genai import types as genai_types
    except Exception as exc:
        raise SystemExit("Missing dependencies. Run: pip install google-genai pillow") from exc
    client = genai.Client(api_key=api_key)
    config = genai_types.GenerateContentConfig(response_modalities=["IMAGE"])
    if aspect_ratio or resolution:
        config = genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=genai_types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=resolution,
            ),
        )
    contents: List[Any] = [prompt] + images
    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return _extract_gemini_image_bytes(response)
        except Exception as exc:
            if attempt >= retries:
                raise
            print(f"Warning: Gemini image attempt {attempt} failed: {exc}")


def find_character(characters: List[Dict[str, Any]], query: str) -> Dict[str, Any]:
    if not characters:
        raise SystemExit("Character list contains no characters.")
    query_norm = normalize(query)
    for c in characters:
        if normalize(c.get("id", "")) == query_norm:
            return c
    for c in characters:
        if normalize(c.get("name", "")) == query_norm:
            return c
    raise SystemExit(f"Character not found: {query}")


def match_character_in_scene(scene: Dict[str, Any], character: Dict[str, Any]) -> bool:
    scene_chars = scene.get("characters", [])
    if not scene_chars:
        return False
    char_id = normalize(character.get("id", ""))
    char_name = normalize(character.get("name", ""))
    for raw in scene_chars:
        if normalize(str(raw)) in {char_id, char_name}:
            return True
    return False


def gather_screens_from_character_locations(
    screens: List[Dict[str, Any]],
    character: Dict[str, Any],
) -> List[Dict[str, Any]]:
    locations = character.get("locations", []) or []
    wanted = {str(loc) for loc in locations if loc}
    return [s for s in screens if s.get("id") in wanted]


def gather_scenes_for_screens(
    scenes: List[Dict[str, Any]],
    screens: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    screen_ids = {s.get("id") for s in screens if s.get("id")}
    return [s for s in scenes if s.get("screenId") in screen_ids]


def gather_dialogue_for_character(dialogue_dir: str, character_id: str) -> List[Dict[str, Any]]:
    if not os.path.isdir(dialogue_dir):
        return []
    entries: List[Dict[str, Any]] = []
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
            nodes = graph.get("nodes", [])
            if not isinstance(nodes, list):
                continue
            lines: List[str] = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("speakerId") != character_id:
                    continue
                if node.get("type") == "player_choice":
                    for choice in node.get("choices", []) or []:
                        if isinstance(choice, dict) and choice.get("text"):
                            lines.append(str(choice["text"]))
                else:
                    text = node.get("text")
                    if text:
                        lines.append(str(text))
            if lines:
                entries.append({"sceneId": scene_id, "lines": lines})
    return entries


def build_gpt_prompt(
    *,
    character: Dict[str, Any],
    scenes: List[Dict[str, Any]],
    screens: List[Dict[str, Any]],
    story_md: str,
    character_style: Dict[str, Any],
    dialogue_lines: List[Dict[str, Any]],
) -> str:
    filtered_character = drop_json_key(character, "pose_sets")
    filtered_character = drop_json_key(filtered_character, "animation_direction")
    filtered_scenes = drop_json_key(scenes, "pose_sets")
    filtered_scenes = drop_json_key(filtered_scenes, "animation_direction")
    filtered_scenes = drop_json_key(filtered_scenes, "triggerLogic")
    filtered_scenes = drop_json_key(filtered_scenes, "possibleOutcomes")
    filtered_scenes = drop_json_key(filtered_scenes, "characters")
    filtered_screens = drop_json_key(screens, "pose_sets")
    filtered_screens = drop_json_key(filtered_screens, "animation_direction")
    filtered_screens = drop_json_key(filtered_screens, "connections")
    filtered_screens = drop_json_key(filtered_screens, "puzzle_tags")
    filtered_screens = drop_json_key(filtered_screens, "dependencies")
    filtered_screens = drop_json_key(filtered_screens, "interactables")
    filtered_screens = drop_json_key(filtered_screens, "importance")
    filtered_screens = drop_json_key(filtered_screens, "category")
    filtered_style = drop_json_key(character_style, "pose_sets")
    filtered_style = drop_json_key(filtered_style, "animation_direction")
    filtered_story = remove_story_sections(
        story_md,
        [
            "## 3. Narrative Rules (Story Physics)",
            "## 7. Act Structure",
            "## 9. Player Choice and Consequences",
            "## 12. Canon vs Flexibility",
            "## 14. Narrative Risks",
        ],
    )

    sections: List[str] = []
    sections.append(
        "Create a clear image prompt for generating the character below. "
        "Info about them and the scenes/environments are listed below. "
        "Do not return anything but the image prompt. No pre-text or addendum."
    )
    sections.append("")
    sections.append(BASELINE_PROMPT.strip())
    sections.append("")
    sections.append("CONTEXT")
    sections.append("")
    sections.append("CHARACTER (from story_specific_gen/characters.json)")
    sections.append(json.dumps(filtered_character, indent=2))
    sections.append("")
    sections.append("SCENES WITH CHARACTER (from story_specific_gen/scenes.json)")
    sections.append(json.dumps(filtered_scenes, indent=2))
    sections.append("")
    sections.append("SCREENS FOR THOSE SCENES (from screens.json)")
    sections.append(json.dumps(filtered_screens, indent=2))
    sections.append("")
    sections.append("DIALOGUE LINES (from story_specific_gen/dialogue/*.json)")
    sections.append(json.dumps(dialogue_lines, indent=2))
    sections.append("")
    sections.append("STORY (from story.md)")
    sections.append(filtered_story)
    sections.append("")
    sections.append(
        "PROMPT MUST INCLUDE THIS - START\n\n"
        "Create a **single baseline character sprite sheet** image following the visual rules defined in story_specific/character_style.json. "
        "Info about the character is below.\n\n"
        f"{json.dumps(filtered_style, indent=2)}\n\n"
        "PROMPT MUST INCLUDE THIS - END"
    )
    sections.append("")
    return "\n".join(sections).strip()


def _extract_gemini_image_bytes(response: Any) -> bytes:
    parts_direct = getattr(response, "parts", None) or []
    for part in parts_direct:
        inline = getattr(part, "inline_data", None)
        if inline:
            mime_type = getattr(inline, "mime_type", "")
            data = getattr(inline, "data", None)
            if mime_type.startswith("image/") and data is not None:
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
                try:
                    import base64
                    return base64.b64decode(data)
                except Exception:
                    pass

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if not inline:
                continue
            mime_type = getattr(inline, "mime_type", "")
            data = getattr(inline, "data", None)
            if not mime_type.startswith("image/") or data is None:
                continue
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if isinstance(data, str):
                try:
                    import base64
                    return base64.b64decode(data)
                except Exception:
                    pass
    raise RuntimeError("Gemini response did not include image bytes.")


def generate_image_gemini(
    *,
    model: str,
    prompt_text: str,
    out_path: str,
    reference_paths: List[str],
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
) -> None:
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise RuntimeError(
            "Missing dependencies. Run: pip install google-genai"
        ) from e

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) before running.")

    client = genai.Client(api_key=api_key)
    model_id = model if "/" in model else f"models/{model}"

    config = genai_types.GenerateContentConfig(
        response_modalities=["IMAGE"],
    )
    if aspect_ratio or resolution:
        config = genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=genai_types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size=resolution,
            ),
        )

    parts: List[Any] = [prompt_text]
    refs_existing = [p for p in reference_paths if os.path.exists(p)]
    if refs_existing:
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(
                "Missing dependencies. Run: pip install google-genai pillow"
            ) from e
        for p in refs_existing[:8]:
            try:
                parts.append(Image.open(p))
            except Exception:
                print(f"Warning: unable to open reference image {p}; skipping.")

    response = client.models.generate_content(
        model=model_id,
        contents=parts,
        config=config,
    )
    img_bytes = _extract_gemini_image_bytes(response)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(img_bytes)


def generate_image_with_provider(
    *,
    provider: str,
    model: str,
    prompt_text: str,
    out_path: str,
    reference_paths: Optional[List[str]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    openai_size: str = "1024x1024",
    openai_quality: str = "high",
    openai_background: Optional[str] = None,
) -> None:
    images: List["Image.Image"] = []
    if reference_paths:
        try:
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(
                "Missing dependencies. Run: pip install google-genai pillow"
            ) from e
        for p in reference_paths[:8]:
            if not os.path.exists(p):
                continue
            try:
                images.append(Image.open(p))
            except Exception:
                print(f"Warning: unable to open reference image {p}; skipping.")
    img_bytes = llm_generate_image(
        provider=provider,
        model=model,
        prompt=prompt_text,
        images=images,
        size=openai_size,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        quality=openai_quality,
        background=openai_background,
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(img_bytes)


def create_talking_head(
    image_path: str,
    direction: str,
    provider: str,
    model: str,
    openai_size: str,
) -> str:
    # Generate head mask and talking-head frames for one facing direction.
    import numpy as np
    from PIL import Image

    if not os.path.exists(image_path):
        raise SystemExit(f"Image not found: {image_path}")

    prompt = (
        "Generate a segmentation mask image based on the provided image. "
        "This head area (including hair, face, any headwear or headgear, and neck above the collar) "
        "should be filled entirely with solid white. "
        "Include in the white mask any object that is physically attached to, worn on, mounted to, "
        "or supported by the head. If the object would move with the head when the body stays still, "
        "it must be white. "
        "The rest of the body/etc should be very dark grey (#222222). "
        "The background should be black (#000000). "
        "There should be no other colors or shades of gray in the final image. "
        "**CRITICAL**: The mask image must be in the exact location as the head on the original image.\n"
        f"Direction: {direction}"
    )
    base, _ext = os.path.splitext(image_path)
    out_path = f"{base}-head_mask.png"
    try:
        original = Image.open(image_path).convert("RGBA")
        orig_w, orig_h = original.size

        if os.path.exists(out_path):
            print(f"- Skipped head mask generation (exists): {out_path}")
            mask_img = Image.open(out_path).convert("L")
            mask_arr = np.array(mask_img)
        else:
            for attempt in range(1, 4):
                time.sleep(1)
                # input("Press Enter to continue...")
                base_img = Image.open(image_path).convert("RGBA")
                base_img_for_llm = base_img
                pad_left = 0
                pad_top = 0
                pad_size = None
                debug_to_llm = os.path.join(
                    os.path.dirname(image_path),
                    f"{os.path.basename(image_path)}debug-raw-to-llm.png",
                )
                base_img_for_llm.save(debug_to_llm)
                if base_img_for_llm.size[0] != base_img_for_llm.size[1]:
                    w, h = base_img_for_llm.size
                    pad_size = max(w, h)
                    square = Image.new("RGBA", (pad_size, pad_size), (0, 0, 0, 0))
                    if w >= h:
                        pad_top = (pad_size - h) // 2
                        square.paste(base_img_for_llm, (0, pad_top))
                    else:
                        pad_left = (pad_size - w) // 2
                        square.paste(base_img_for_llm, (pad_left, 0))
                    base_img_for_llm = square
                    base_img_for_llm.save(debug_to_llm)
                model_name = "gpt-image-1.5" if provider == "openai" else model
                mask_bytes = llm_generate_image(
                    provider=provider,
                    model=model_name,
                    prompt=prompt,
                    images=[base_img_for_llm],
                    size=openai_size,
                    quality="high",
                )
                mask_img = Image.open(io.BytesIO(mask_bytes)).convert("L")
                if mask_img.size != base_img_for_llm.size:
                    mask_img = mask_img.resize(base_img_for_llm.size, Image.Resampling.NEAREST)
                debug_raw = os.path.join(os.path.dirname(image_path), "debug-raw-llm.png")
                mask_img.save(debug_raw)
                if pad_size is not None:
                    if mask_img.size != (pad_size, pad_size):
                        mask_img = mask_img.resize((pad_size, pad_size), Image.Resampling.NEAREST)
                    mask_img = mask_img.crop((pad_left, pad_top, pad_left + orig_w, pad_top + orig_h))
                elif mask_img.size != original.size:
                    mask_img = mask_img.resize(original.size, Image.Resampling.NEAREST)
                mask_arr = np.array(mask_img)
                mask_arr = np.where(mask_arr >= 128, 255, 0).astype("uint8")
                white_ratio = (mask_arr == 255).mean()
                if white_ratio > 0.10:
                    print("head mask error - too much masked")
                    if attempt < 3:
                        continue
                if white_ratio < 0.015:
                    print("head mask error - too much unmasked")
                    if attempt < 3:
                        continue
                # Close minor gaps: grow 2px then shrink 1px.
                mask_bool = mask_arr == 255
                for _ in range(2):
                    padded = np.pad(mask_bool, ((1, 1), (1, 1)), mode="constant", constant_values=False)
                    neighbors = (
                        padded[0:-2, 0:-2] | padded[0:-2, 1:-1] | padded[0:-2, 2:]
                        | padded[1:-1, 0:-2] | padded[1:-1, 1:-1] | padded[1:-1, 2:]
                        | padded[2:, 0:-2] | padded[2:, 1:-1] | padded[2:, 2:]
                    )
                    mask_bool = neighbors
                padded = np.pad(mask_bool, ((1, 1), (1, 1)), mode="constant", constant_values=True)
                neighbors = (
                    padded[0:-2, 0:-2] & padded[0:-2, 1:-1] & padded[0:-2, 2:]
                    & padded[1:-1, 0:-2] & padded[1:-1, 1:-1] & padded[1:-1, 2:]
                    & padded[2:, 0:-2] & padded[2:, 1:-1] & padded[2:, 2:]
                )
                mask_bool = neighbors
                mask_arr = np.where(mask_bool, 255, 0).astype("uint8")
                try:
                    base_arr = np.array(original.convert("RGBA"))
                    head_arr = base_arr.copy()
                    head_arr[mask_arr == 0] = [0, 0, 0, 0]
                    non_head_arr = base_arr.copy()
                    non_head_arr[mask_arr == 255] = [0, 0, 0, 0]
                    head_img = Image.fromarray(head_arr, mode="RGBA")
                    non_head_img = Image.fromarray(non_head_arr, mode="RGBA")
                    check_prompt = (
                        "Confirm that the head and non-head is correctly segmented. \n"
                        "Segmentation is considered acceptable if: \n"
                        "- The face is fully contained within the head segment. \n"
                        "- The body from neck down is fully contained within the non-head segment. \n"
                        "- Small overlaps, cutoffs, or misassigned accessory parts near the head–neck boundary are allowed. \n"
                        "Return just YES or NO."
                    )
                    client = OpenAI()
                    content = [
                        {"type": "input_text", "text": check_prompt},
                    ]
                    for img in (original, head_img, non_head_img):
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        content.append(
                            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}
                        )
                    rsp = client.responses.create(
                        model="gpt-5.2",
                        input=[{"role": "user", "content": content}],
                    )
                    if rsp.output_text.strip().upper() != "YES":
                        print("head mask error - failed LLM validation")
                        debug_base = os.path.join(
                            os.path.dirname(image_path),
                            f"debug-{os.path.basename(base)}-failed",
                        )
                        head_img.save(f"{debug_base}-head.png")
                        non_head_img.save(f"{debug_base}-body.png")
                        Image.fromarray(mask_arr, mode="L").save(f"{debug_base}-mask.png")
                        if attempt < 3:
                            continue
                except Exception as exc:
                    print(f"Warning: head mask validation failed: {exc}")
                mask_img = Image.fromarray(mask_arr, mode="L")
                break

        if not os.path.exists(out_path):
            mask_img.save(out_path)
        ys, xs = np.where(mask_arr >= 200)
        if len(xs) == 0 or len(ys) == 0:
            bbox = None
        else:
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        print(f"- Head mask bbox: {bbox}")
    except Exception as exc:
        print(f"Debug: provider={provider} model={model}")
        print(f"Debug: prompt_type={type(prompt)} prompt_len={len(prompt) if isinstance(prompt, str) else 'n/a'}")
        try:
            print(f"Debug: image_path={image_path} size={Image.open(image_path).size}")
        except Exception as inner:
            print(f"Debug: failed to open image_path: {inner}")
        raise SystemExit(f"Failed to process head mask for {image_path}: {exc}") from exc

    if bbox:
        x0, y0, x1, y1 = bbox
        neutral = Image.open(image_path).convert("RGBA")
        mask_neutral = Image.open(out_path).convert("L")
        neutral_arr = np.array(neutral)
        mask_arr = np.array(mask_neutral) >= 200
        neutral_arr[~mask_arr] = [0, 0, 0, 0]
        neutral_img = Image.fromarray(neutral_arr, mode="RGBA").crop((x0, y0, x1, y1))
        neutral_path = f"{base}-head_talk0.png"
        neutral_img.save(neutral_path)

    talk_variations = [
        "The head should be slightly tilted slightly up, with the mouth slightly open.",
        "The head should be slightly up, with the mouth fully open.",
        "The mouth should be slightly open.",
        "The head should be slightly tilted down, with the mouth slightly open.",
        "The mouth should be slightly open.",
    ]
    def _render_talk(idx: int, variation: str) -> None:
        talk_prompt = (
            "Generate the next frame of this head and face (identified by the pre-segmented head) talking. There are five frames total. In this one: "
            + variation
            + " Generate only the head (including hair, face, and neck above the collar) on a magenta background. "
            + "The body will not be moving, so the neck should be at the same x/y location. "
            + "Do not change any eye/nose/hair shape or color. This needs to be the same character."
        )
        head_ref = neutral_path if bbox else image_path
        if provider == "openai":
            images = [Image.open(image_path), Image.open(head_ref), Image.open(out_path)]
            mask_img = None
        else:
            images = [Image.open(image_path), Image.open(head_ref)]
            mask_img = None
        talk_bytes = llm_generate_image(
            provider=provider,
            model=model,
            prompt=talk_prompt,
            images=images,
            mask=mask_img,
            size=openai_size,
            quality="high",
        )
        talk_path = f"{base}-head_talk{idx}.png"
        with open(talk_path, "wb") as f:
            f.write(talk_bytes)

    def _postprocess_talk_frame(talk_path: str) -> None:
        if not bbox or not os.path.exists(talk_path):
            return
        x0, y0, x1, y1 = bbox
        target_w = max(1, x1 - x0)
        target_h = max(1, y1 - y0)
        img = Image.open(talk_path).convert("RGBA")
        arr = np.array(img)
        rgb = arr[..., :3].astype(np.uint8)
        magenta_ratio = _bg_mask_magenta(rgb).mean()
        if magenta_ratio >= 0.10:
            bg = _bg_mask_magenta(rgb)
        else:
            bg = _bg_mask_rgb(rgb)
        arr[bg] = [0, 0, 0, 0]
        fg = np.any(arr[..., :3] != 0, axis=2) & (arr[..., 3] > 0)
        ys, xs = np.where(fg)
        if len(xs) == 0 or len(ys) == 0:
            return
        cx0, cy0, cx1, cy1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        arr = magenta_edge_filter(arr)
        cropped = Image.fromarray(arr, mode="RGBA").crop((cx0, cy0, cx1, cy1))
        cropped = cropped.resize((target_w, target_h), Image.Resampling.NEAREST)
        cropped.save(talk_path)
        snap_image(talk_path, width=target_w, height=target_h, k_colors=64)

    def _validate_talk_frame(talk_path: str) -> bool:
        if not os.path.exists(neutral_path):
            return True
        debug_path = None
        if bbox and os.path.exists(talk_path):
            try:
                base_img = Image.open(image_path).convert("RGBA")
                head_mask = Image.open(out_path).convert("L")
                base_arr = np.array(base_img)
                mask_arr = (np.array(head_mask) >= 200)
                base_arr[mask_arr] = [0, 0, 0, 0]
                base_img = Image.fromarray(base_arr, mode="RGBA")
                talk_img = Image.open(talk_path).convert("RGBA")
                x0, y0, x1, y1 = bbox
                base_img.paste(talk_img, (x0, y0), talk_img)
                debug_path = f"{base}-talkdebug.png"
                base_img.save(debug_path)
            except Exception as exc:
                print(f"Warning: failed to write talk debug image for {talk_path}: {exc}")
        prompt = (
            "You are checking animation frames. Compare the two images and decide if they look like "
            "valid talking frames of the same face. Return only YES or NO."
        )
        with open(neutral_path, "rb") as f:
            base_b64 = base64.b64encode(f.read()).decode("ascii")
        with open(talk_path, "rb") as f:
            frame_b64 = base64.b64encode(f.read()).decode("ascii")
        content = [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": f"data:image/png;base64,{base_b64}"},
            {"type": "input_image", "image_url": f"data:image/png;base64,{frame_b64}"},
        ]
        client = OpenAI()
        rsp = client.responses.create(
            model="gpt-5.2",
            input=[{"role": "user", "content": content}],
        )
        if rsp.output_text.strip().upper() != "YES":
            return False
        if not debug_path or not os.path.exists(debug_path):
            return True
        try:
            with open(image_path, "rb") as f:
                base_b64 = base64.b64encode(f.read()).decode("ascii")
            with open(debug_path, "rb") as f:
                debug_b64 = base64.b64encode(f.read()).decode("ascii")
            debug_content = [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/png;base64,{base_b64}"},
                {"type": "input_image", "image_url": f"data:image/png;base64,{debug_b64}"},
            ]
            rsp = client.responses.create(
                model="gpt-5.2",
                input=[{"role": "user", "content": debug_content}],
            )
            return rsp.output_text.strip().upper() == "YES"
        except Exception as exc:
            print(f"Warning: failed to validate talk debug image: {exc}")
            return True

    def _run_talk_variant(idx: int, variation: str) -> None:
        talk_path = f"{base}-head_talk{idx}.png"
        if os.path.exists(talk_path):
            return
        for attempt in range(1, 7):
            _render_talk(idx, variation)
            _postprocess_talk_frame(talk_path)
            if _validate_talk_frame(talk_path):
                return
            print(f"- regenerating {talk_path}")

    tasks = []
    for idx, variation in enumerate(talk_variations, start=1):
        talk_path = f"{base}-head_talk{idx}.png"
        if os.path.exists(talk_path):
            continue
        tasks.append((idx, variation))

    if tasks:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(5, len(tasks))) as executor:
            futures = [executor.submit(_run_talk_variant, idx, variation) for idx, variation in tasks]
            for future in as_completed(futures):
                future.result()
    return out_path


def generate_prompt_gpt(
    *,
    model: str,
    prompt_text: str,
) -> str:
    return llm_generate_text(prompt_text, model)


def _cluster_line_positions(indices: List[int]) -> List[int]:
    if not indices:
        return []
    clusters = []
    current = [indices[0]]
    for idx in indices[1:]:
        if idx == current[-1] + 1:
            current.append(idx)
        else:
            clusters.append(current)
            current = [idx]
    clusters.append(current)
    return [int(sum(cluster) / len(cluster)) for cluster in clusters]


def _pick_four_lines(lines: List[int], size: int) -> List[int]:
    if len(lines) >= 4:
        lines = sorted(lines)
        targets = [0, size / 3, 2 * size / 3, size - 1]
        selected = []
        remaining = lines[:]
        for target in targets:
            closest = min(remaining, key=lambda x: abs(x - target))
            selected.append(closest)
            remaining.remove(closest)
        return sorted(selected)
    return []


def _detect_grid_lines(mask: "Image.Image", axis: str, min_ratio: float = 1.0) -> List[int]:
    import numpy as np

    arr = np.array(mask, dtype="uint8")
    if axis == "x":
        sums = arr.sum(axis=0)
        threshold = int(arr.shape[0] * min_ratio)
    else:
        sums = arr.sum(axis=1)
        threshold = int(arr.shape[1] * min_ratio)
    candidates = [i for i, value in enumerate(sums) if value >= threshold]
    clustered = _cluster_line_positions(candidates)
    return clustered


def _bg_mask_rgb(arr_rgb: "np.ndarray") -> "np.ndarray":
    import numpy as np

    r = arr_rgb[..., 0].astype(np.int16)
    g = arr_rgb[..., 1].astype(np.int16)
    b = arr_rgb[..., 2].astype(np.int16)

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    chroma = maxc - minc
    bright = maxc

    whiteish = (bright >= 230) & (chroma <= 25)
    lightgray = (bright >= 0xB0) & (chroma <= 20)
    magentaish = (
        (r >= 140)
        & (b >= 140)
        & (g <= 140)
        & ((r + b) - 2 * g >= 120)
        & (chroma >= 40)
    )
    dark = bright <= 0x33

    return whiteish | lightgray | magentaish | dark


def _bg_mask_magenta(arr_rgb: "np.ndarray") -> "np.ndarray":
    import numpy as np

    r = arr_rgb[..., 0].astype(np.int16)
    g = arr_rgb[..., 1].astype(np.int16)
    b = arr_rgb[..., 2].astype(np.int16)

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    chroma = maxc - minc
    magentaish = (
        (r >= 140)
        & (b >= 140)
        & (g <= 140)
        & ((r + b) - 2 * g >= 120)
        & (chroma >= 40)
    )
    return magentaish


def identify_lines(img: "Image.Image") -> "np.ndarray":
    import numpy as np

    arr = np.array(img.convert("RGBA"), dtype=np.uint8)
    r = arr[..., 0]
    g = arr[..., 1]
    b = arr[..., 2]
    a = arr[..., 3]
    dark = (r <= 0x30) & (g <= 0x30) & (b <= 0x30) & (a > 0)
    opaque = a > 0
    row_denom = opaque.sum(axis=1)
    col_denom = opaque.sum(axis=0)
    row_ratio = np.divide(
        dark.sum(axis=1),
        row_denom,
        out=np.zeros_like(row_denom, dtype=float),
        where=row_denom > 0,
    )
    col_ratio = np.divide(
        dark.sum(axis=0),
        col_denom,
        out=np.zeros_like(col_denom, dtype=float),
        where=col_denom > 0,
    )

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    chroma = maxc - minc
    row_bright_min = np.where(opaque, maxc, 255).min(axis=1)
    row_bright_max = np.where(opaque, maxc, 0).max(axis=1)
    row_chroma_min = np.where(opaque, chroma, 255).min(axis=1)
    row_chroma_max = np.where(opaque, chroma, 0).max(axis=1)
    row_bright_range = row_bright_max - row_bright_min
    row_chroma_range = row_chroma_max - row_chroma_min

    col_bright_min = np.where(opaque, maxc, 255).min(axis=0)
    col_bright_max = np.where(opaque, maxc, 0).max(axis=0)
    col_chroma_min = np.where(opaque, chroma, 255).min(axis=0)
    col_chroma_max = np.where(opaque, chroma, 0).max(axis=0)
    col_bright_range = col_bright_max - col_bright_min
    col_chroma_range = col_chroma_max - col_chroma_min

    diff_row = np.abs(arr[:, 1:, :] - arr[:, :-1, :]).sum(axis=2)
    valid_row = opaque[:, 1:] & opaque[:, :-1]
    row_diff_sum = (diff_row * valid_row).sum(axis=1)
    row_diff_cnt = valid_row.sum(axis=1)
    row_diff_mean = np.divide(
        row_diff_sum,
        row_diff_cnt,
        out=np.zeros_like(row_diff_sum, dtype=float),
        where=row_diff_cnt > 0,
    )

    diff_col = np.abs(arr[1:, :, :] - arr[:-1, :, :]).sum(axis=2)
    valid_col = opaque[1:, :] & opaque[:-1, :]
    col_diff_sum = (diff_col * valid_col).sum(axis=0)
    col_diff_cnt = valid_col.sum(axis=0)
    col_diff_mean = np.divide(
        col_diff_sum,
        col_diff_cnt,
        out=np.zeros_like(col_diff_sum, dtype=float),
        where=col_diff_cnt > 0,
    )

    bright_row = (row_ratio > 0.10) & (row_bright_range <= 40) & (row_chroma_range <= 50)
    bright_col = (col_ratio > 0.10) & (col_bright_range <= 40) & (col_chroma_range <= 50)
    row_lines = bright_row | (row_diff_mean <= 30)
    col_lines = bright_col | (col_diff_mean <= 30)
    mask = np.zeros(dark.shape, dtype=bool)
    mask[row_lines, :] = True
    mask[:, col_lines] = True
    return mask


def wipe_border_margin(mask: "np.ndarray", margin: int = 5) -> "np.ndarray":
    out = mask.copy()
    out[:margin, :] = 0
    out[-margin:, :] = 0
    out[:, :margin] = 0
    out[:, -margin:] = 0
    return out


def remove_isolated_fg(mask: "np.ndarray") -> "np.ndarray":
    import numpy as np

    fg = mask.astype(np.uint8)
    padded = np.pad(fg, ((1, 1), (1, 1)), mode="constant", constant_values=0)
    neighbors = (
        padded[0:-2, 0:-2] + padded[0:-2, 1:-1] + padded[0:-2, 2:]
        + padded[1:-1, 0:-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
        + padded[2:, 0:-2] + padded[2:, 1:-1] + padded[2:, 2:]
    )
    isolated = (fg == 1) & (neighbors <= 1)
    fg[isolated] = 0
    return fg


def UNUSED_center_sprite_wrap(
    img: "Image.Image",
    *,
    bg_rgb=(255, 0, 255),
    tol=20,
    use_alpha_if_present=True,
    save_mask_path: Optional[str] = None,
) -> "Image.Image":
    import numpy as np
    from PIL import Image

    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    if use_alpha_if_present and np.any(arr[..., 3] < 255):
        fg = arr[..., 3] > 0
    else:
        arr_rgb = arr[..., :3].astype(np.uint8)
        bg = _bg_mask_rgb(arr_rgb)
        fg = wipe_border_margin((~bg).astype("uint8"))
        fg = remove_isolated_fg(fg).astype(bool)
    if save_mask_path:
        mask_img = Image.fromarray((fg.astype("uint8") * 255), mode="L")
        mask_img.save(save_mask_path)

    dx, dy = compute_wrap_shift(fg)
    if dx == 0 and dy == 0:
        return img
    out = apply_wrap_shift(arr, dx, dy)
    return Image.fromarray(out, mode="RGBA")


def compute_wrap_shift(fg_mask: "np.ndarray") -> Tuple[int, int]:
    import numpy as np

    ys, xs = np.where(fg_mask)
    if len(xs) == 0 or len(ys) == 0:
        return 0, 0
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    h, w = fg_mask.shape[:2]
    tx = w // 2
    ty = h // 2
    dx = tx - cx
    dy = ty - cy
    return dx, dy


def apply_wrap_shift(arr: "np.ndarray", dx: int, dy: int) -> "np.ndarray":
    import numpy as np

    return np.roll(arr, shift=(dy, dx), axis=(0, 1))


def format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d} left"


def render_progress(done: int, total: int, started: float) -> None:
    width = 30
    if total <= 0:
        total = 1
    filled = int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    eta = 0.0
    if done > 0:
        avg = (time.time() - started) / done
        eta = avg * (total - done)
    line = f"\r- Progress [{bar}] {done}/{total} {format_eta(eta)}"
    sys.stdout.write(line)
    sys.stdout.flush()


class ProgressTracker:
    def __init__(self, total: int, initial_done: int = 0) -> None:
        self.total = total
        self.done = min(total, max(0, initial_done))
        self.started = time.time()
        render_progress(self.done, self.total, self.started)

    def advance(self, count: int = 1) -> None:
        self.done = min(self.total, self.done + count)
        render_progress(self.done, self.total, self.started)

    def finish(self) -> None:
        render_progress(self.total, self.total, self.started)
        sys.stdout.write("\n")


def split_sprite_sheet(
    sheet_path: str,
    debug_mode: int = 0,
    progress: Optional["ProgressTracker"] = None,
) -> None:
    # Split a 3x3 sprite sheet into individual crops and masks.
    from PIL import Image
    import numpy as np

    if not os.path.exists(sheet_path):
        return
    img = Image.open(sheet_path).convert("RGB")
    w, h = img.size
    arr_rgb = np.array(img, dtype="uint8")
    magenta_ratio = _bg_mask_magenta(arr_rgb).mean()
    if magenta_ratio >= 0.10:
        bg = _bg_mask_magenta(arr_rgb)
    else:
        bg = _bg_mask_rgb(arr_rgb)
    fg_raw = (~bg).astype("uint8")
    fg = remove_isolated_fg(fg_raw)
    fg_for_lines = wipe_border_margin(fg_raw)
    if progress:
        progress.advance(1)
    col_ratio = fg_for_lines.mean(axis=0)
    row_ratio = fg_for_lines.mean(axis=1)
    fg_for_lines[:, col_ratio >= 0.95] = 0
    fg_for_lines[row_ratio >= 0.95, :] = 0
    bg_col_ratio = 1.0 - col_ratio
    bg_row_ratio = 1.0 - row_ratio
    fg_col = col_ratio >= 0.6
    fg_row = row_ratio >= 0.6
    bg_col = bg_col_ratio >= 0.6
    bg_row = bg_row_ratio >= 0.6
    for idx, is_fg in enumerate(fg_col):
        if not is_fg:
            continue
        left_bg = idx > 0 and bg_col[idx - 1]
        right_bg = idx + 1 < len(bg_col) and bg_col[idx + 1]
        if left_bg or right_bg:
            fg_for_lines[:, idx] = 0
    for idx, is_fg in enumerate(fg_row):
        if not is_fg:
            continue
        up_bg = idx > 0 and bg_row[idx - 1]
        down_bg = idx + 1 < len(bg_row) and bg_row[idx + 1]
        if up_bg or down_bg:
            fg_for_lines[idx, :] = 0
    fg_for_lines = remove_isolated_fg(fg_for_lines)

    bg_mask = (~fg_for_lines.astype(bool)).astype("uint8")
    x_lines = _detect_grid_lines(Image.fromarray(bg_mask), "x", 1.0)
    y_lines = _detect_grid_lines(Image.fromarray(bg_mask), "y", 1.0)
    if debug_mode == 3:
        base, _ext = os.path.splitext(sheet_path)
        mask_path = f"{base}-overview-mask.png"
        Image.fromarray((fg_for_lines * 255).astype("uint8"), mode="L").save(mask_path)
        print(f"Saved {mask_path}")
        print(f"vert_lines: {x_lines}")
        print(f"horz_lines: {y_lines}")
        return
    x_lines = _pick_four_lines(x_lines, w)
    y_lines = _pick_four_lines(y_lines, h)

    if not x_lines:
        x_lines = [0, w // 3, 2 * w // 3, w - 1]
    if not y_lines:
        y_lines = [0, h // 3, 2 * h // 3, h - 1]

    pad = max(2, min(6, min(w, h) // 200))
    base, _ext = os.path.splitext(sheet_path)
    for r in range(3):
        for c in range(3):
            x0 = max(0, x_lines[c] + pad)
            x1 = min(w, x_lines[c + 1] - pad)
            y0 = max(0, y_lines[r] + pad)
            y1 = min(h, y_lines[r + 1] - pad)
            if x1 <= x0 or y1 <= y0:
                x0 = int(c * w / 3)
                x1 = int((c + 1) * w / 3)
                y0 = int(r * h / 3)
                y1 = int((r + 1) * h / 3)
                trim_x = max(1, int((x1 - x0) * 0.02))
                trim_y = max(1, int((y1 - y0) * 0.02))
                x0 += trim_x
                x1 -= trim_x
                y0 += trim_y
                y1 -= trim_y
            crop = img.crop((x0, y0, x1, y1))
            fg_crop = fg[y0:y1, x0:x1]
            dx, dy = compute_wrap_shift(fg_crop)
            crop_arr = np.array(crop.convert("RGBA"))
            crop_arr = apply_wrap_shift(crop_arr, dx, dy)
            crop_arr = magenta_edge_filter(crop_arr)
            crop = Image.fromarray(crop_arr, mode="RGBA")
            fg_crop = apply_wrap_shift(fg_crop, dx, dy)
            fg_filled = fill_internal_voids(fg_crop, close_radius=10)
            crop_rgb = np.array(crop.convert("RGB"), dtype="uint8")
            magenta_bg = _bg_mask_magenta(crop_rgb)
            fg_filled = fg_filled & (~magenta_bg)
            mask_img = Image.fromarray((fg_filled.astype("uint8") * 255), mode="L")
            out_path = f"{base}__r{r + 1}_c{c + 1}.png"
            crop.save(out_path)
            mask_path = f"{base}__r{r + 1}_c{c + 1}-mask.png"
            mask_img.save(mask_path)
    if progress:
        progress.advance(1)


def sprite_crops_exist(sheet_path: str) -> bool:
    base, _ext = os.path.splitext(sheet_path)
    for r in range(1, 4):
        for c in range(1, 4):
            if not os.path.exists(f"{base}__r{r}_c{c}.png"):
                return False
    return True


def classified_images_exist(sheet_path: str) -> bool:
    base, _ext = os.path.splitext(sheet_path)
    base_root = base.replace("-overview", "")
    directions = [
        "portrait",
        "front",
        "front_left",
        "left",
        "back_left",
        "back",
        "back_right",
        "right",
        "front_right",
        "unknown",
    ]
    dir_path = os.path.dirname(base_root) or "."
    for direction in directions:
        if os.path.exists(f"{base_root}-{direction}.png"):
            return True
        pattern = f"{base_root}-{direction}-*.png"
        if any(fnmatch.fnmatch(name, pattern) for name in os.listdir(dir_path)):
            return True
    return False


def delete_unknown_sprites(sheet_path: str) -> None:
    base, _ext = os.path.splitext(sheet_path)
    base_root = base.replace("-overview", "")
    dir_path = os.path.dirname(base_root) or "."
    patterns = [f"{base_root}-unknown*.png", f"{base_root}-unknown*-mask.png"]
    for name in os.listdir(dir_path):
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                try:
                    os.remove(os.path.join(dir_path, name))
                except FileNotFoundError:
                    pass
                break

def choose_best_variant(prompt_text: str, variant_paths: list[str], model: str) -> int:
    if not variant_paths:
        return 1
    count = len(variant_paths)
    auto_prompt = (
        "Choose the image that best matches the intent of the prompt. "
        f"Return only the text 1 through {count}."
    )
    labels = "\n".join(
        f"Image {idx}: {os.path.basename(path)}"
        for idx, path in enumerate(variant_paths, start=1)
    )
    content = [
        {"type": "input_text", "text": prompt_text},
        {"type": "input_text", "text": auto_prompt},
        {"type": "input_text", "text": f"Image order:\n{labels}"},
    ]
    for path in variant_paths:
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
    print(f"Warning: auto-select failed: {choice}. Using 1.")
    return 1


def classify_facing_once(image_path: str, model: str, row: int) -> str:
    options = [
        "portrait",
        "front",
        "front_left",
        "front_right",
        "back",
        "back_left",
        "back_right",
        "left",
        "right",
    ]
    random.shuffle(options)
    row_hint = ""
    if row == 1:
        row_hint = "This image is probably back_left, back, or back_right."
    elif row == 2:
        row_hint = "This image is probably portrait, left, or right."
    elif row == 3:
        row_hint = "This image is probably front_right, front_left, or front."
    prompt = (
        "You are a visual classifier. Identify the character facing direction from the viewer's "
        "point of view. Return JSON only with key 'facing'. Choose the best valid option from: "
        + ", ".join(options)
        + ".\n"
        + "\nportrait is the only one which doesn't show the whole body\n"
        + "front_left is a quarter turn facing screen left\n"
        + "front_right is a quarter turn facing screen right\n"
        + row_hint
    )
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    content = [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
    ]
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_object"}},
        reasoning={"effort": "high"},
    )
    try:
        payload = json.loads(rsp.output_text.strip())
    except json.JSONDecodeError:
        return "unknown"
    facing = payload.get("facing")
    if isinstance(facing, str) and facing in options:
        return facing
    return "unknown"


def classify_facing(image_path: str, model: str, row: int) -> str:
    for _ in range(3):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(classify_facing_once, image_path, model, row),
                executor.submit(classify_facing_once, image_path, model, row),
                executor.submit(classify_facing_once, image_path, model, row),
            ]
            results = [f.result() for f in futures]
        if results[0] == results[1] == results[2] and results[0] != "unknown":
            return results[0]
    print(f"- pose of {image_path} image unknown")
    return "unknown"


def rename_with_direction(
    sheet_path: str,
    model: str,
    progress: Optional["ProgressTracker"] = None,
) -> None:
    # Classify facing directions, rename crops, and generate direction masks.
    import numpy as np
    from PIL import Image

    base, _ext = os.path.splitext(sheet_path)
    base_root = base.replace("-overview", "")
    for r in range(1, 4):
        for c in range(1, 4):
            crop_path = f"{base}__r{r}_c{c}.png"
            if not os.path.exists(crop_path):
                continue
            facing = classify_facing(crop_path, model, r)
            dest = f"{base_root}-{facing}.png"
            if os.path.exists(dest):
                idx = 2
                while True:
                    candidate = f"{base_root}-{facing}-{idx}.png"
                    if not os.path.exists(candidate):
                        dest = candidate
                        break
                    idx += 1
            os.replace(crop_path, dest)
            mask_path = f"{base}__r{r}_c{c}-mask.png"
            if os.path.exists(mask_path):
                dest_mask = f"{os.path.splitext(dest)[0]}-mask.png"
                os.replace(mask_path, dest_mask)
                try:
                    mask_img = Image.open(dest_mask).convert("L")
                    mask_arr = (np.array(mask_img) > 0).astype("uint8")
                    mask_arr = shrink_mask(mask_arr, radius=1)
                    alpha = (mask_arr * 255).astype("uint8")
                    sprite = Image.open(dest).convert("RGBA")
                    sprite_arr = np.array(sprite)
                    sprite_arr[..., 3] = alpha
                    Image.fromarray(sprite_arr, mode="RGBA").save(dest)
                    mask_img = Image.fromarray(alpha, mode="L")
                    mask_img.save(dest_mask)
                except Exception as exc:
                    print(f"Warning: failed to apply mask to {dest}: {exc}")
                snap_image(dest_mask, width=100, height=300, k_colors=2)
            snap_image(dest, width=100, height=300, k_colors=64)
            # run identify_lines on dest, then remove any lines from dest and dest_mask
            try:
                sprite = Image.open(dest).convert("RGBA")
                line_mask = identify_lines(sprite)
                sprite_arr = np.array(sprite)
                sprite_arr[line_mask] = [0, 0, 0, 0]
                Image.fromarray(sprite_arr, mode="RGBA").save(dest)
                if os.path.exists(dest_mask):
                    mask_img = Image.open(dest_mask).convert("L")
                    mask_arr = np.array(mask_img)
                    mask_arr[line_mask] = 0
                    Image.fromarray(mask_arr, mode="L").save(dest_mask)
            except Exception as exc:
                print(f"Warning: failed to remove lines for {dest}: {exc}")
            try:
                sprite = Image.open(dest).convert("RGBA")
                fg_mask = Image.open(dest_mask).convert("L") if os.path.exists(dest_mask) else None
                if fg_mask is not None:
                    fg_arr = (np.array(fg_mask) > 0).astype("uint8")
                    dx, dy = compute_wrap_shift(fg_arr)
                    if dx or dy:
                        sprite_arr = apply_wrap_shift(np.array(sprite), dx, dy)
                        mask_arr = apply_wrap_shift(fg_arr, dx, dy)
                        sprite_arr[mask_arr == 0] = [255, 0, 255, 0]
                        sprite = Image.fromarray(sprite_arr, mode="RGBA")
                        sprite.save(dest)
                        Image.fromarray((mask_arr * 255).astype("uint8"), mode="L").save(dest_mask)
            except Exception as exc:
                print(f"Warning: failed to recenter {dest}: {exc}")
            if os.path.exists(dest_mask):
                add_border(dest, dest_mask, border=2)
            if progress:
                progress.advance(1)
    resolve_unknowns_and_duplicates(base_root, model)


def snap_image(path: str, *, width: int, height: int, k_colors: int) -> None:
    snapper = os.path.join(os.path.dirname(__file__), "pixel_snapper.py")
    tmp_path = path + ".tmp.png"
    cmd = [
        sys.executable,
        snapper,
        path,
        tmp_path,
        str(k_colors),
        "--width",
        str(width),
        "--height",
        str(height),
        "--method",
        "naive",
    ]
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if run.returncode != 0:
        print(f"Warning: pixel_snapper failed for {path}:\n{run.stdout}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
        return
    os.replace(tmp_path, path)


def resolve_unknowns_and_duplicates(base_root: str, model: str) -> None:
    import glob
    import re as _re

    def list_images() -> list[str]:
        return sorted(
            p for p in glob.glob(f"{base_root}-*.png") if not p.endswith("-mask.png")
        )

    def parse_direction(path: str) -> str:
        name = os.path.basename(path)
        stem = os.path.splitext(name)[0]
        prefix = os.path.basename(base_root) + "-"
        if not stem.startswith(prefix):
            return ""
        tail = stem[len(prefix) :]
        tail = _re.sub(r"-\d+$", "", tail)
        return tail

    def rename_pair(src_img: str, dest_img: str) -> None:
        src_mask = os.path.splitext(src_img)[0] + "-mask.png"
        dest_mask = os.path.splitext(dest_img)[0] + "-mask.png"
        os.replace(src_img, dest_img)
        if os.path.exists(src_mask):
            os.replace(src_mask, dest_mask)

    def delete_unknown_pairs(paths: list[str]) -> None:
        for src_img in paths:
            src_mask = os.path.splitext(src_img)[0] + "-mask.png"
            try:
                os.remove(src_img)
            except FileNotFoundError:
                pass
            try:
                os.remove(src_mask)
            except FileNotFoundError:
                pass

    max_rounds = 3
    for _ in range(max_rounds):
        images = list_images()
        by_dir: dict[str, list[str]] = {}
        for path in images:
            direction = parse_direction(path)
            by_dir.setdefault(direction, []).append(path)

        duplicates = [paths for paths in by_dir.values() if len(paths) > 1]
        unknowns = by_dir.get("unknown", [])
        if not duplicates and not unknowns:
            break

        if duplicates:
            all_dupes = [p for group in duplicates for p in group]
            for idx, src in enumerate(sorted(all_dupes), start=1):
                dest = f"{base_root}-unknown-{idx}.png"
                rename_pair(src, dest)
            images = list_images()
            unknowns = [p for p in images if parse_direction(p) == "unknown"]
            delete_unknown_pairs(unknowns)
            break

        if unknowns:
            delete_unknown_pairs(unknowns)
            break


def fill_internal_voids(mask: "np.ndarray", close_radius: int = 1) -> "np.ndarray":
    import numpy as np

    try:
        from scipy import ndimage as ndi
    except ImportError as exc:
        raise RuntimeError("Needs scipy. Install with: pip install scipy") from exc

    m = mask > 0
    if close_radius > 0:
        structure = ndi.generate_binary_structure(2, 2)
        m = ndi.binary_closing(m, structure=structure, iterations=close_radius)
    filled = ndi.binary_fill_holes(m)
    return filled


def shrink_mask(mask: "np.ndarray", radius: int = 1) -> "np.ndarray":
    import numpy as np

    if radius <= 0:
        return mask
    out = mask.astype(np.uint8)
    for _ in range(radius):
        padded = np.pad(out, ((1, 1), (1, 1)), mode="constant", constant_values=0)
        neighbors = (
            padded[0:-2, 0:-2] + padded[0:-2, 1:-1] + padded[0:-2, 2:]
            + padded[1:-1, 0:-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:]
            + padded[2:, 0:-2] + padded[2:, 1:-1] + padded[2:, 2:]
        )
        out = ((neighbors == 9) & (out == 1)).astype(np.uint8)
    return out


def magenta_edge_filter(arr: "np.ndarray", iterations: int = 3) -> "np.ndarray":
    for _ in range(iterations):
        alpha = arr[..., 3] == 0
        padded = np.pad(alpha, ((1, 1), (1, 1)), mode="constant", constant_values=False)
        neighbors = (
            padded[0:-2, 0:-2] | padded[0:-2, 1:-1] | padded[0:-2, 2:]
            | padded[1:-1, 0:-2] | padded[1:-1, 1:-1] | padded[1:-1, 2:]
            | padded[2:, 0:-2] | padded[2:, 1:-1] | padded[2:, 2:]
        )
        r = arr[..., 0]
        g = arr[..., 1]
        b = arr[..., 2]
        magentaish = (r > 110) & (b > 110) & (g < 40)
        wipe = magentaish & neighbors
        if not wipe.any():
            break
        arr[wipe] = [0, 0, 0, 0]
    return arr


def add_border(image_path: str, mask_path: str, border: int = 5) -> None:
    from PIL import Image
    import numpy as np

    try:
        from scipy import ndimage as ndi
    except ImportError as exc:
        print(f"Warning: border skipped (needs scipy): {exc}")
        return

    if not os.path.exists(mask_path):
        return

    img = Image.open(image_path).convert("RGBA")
    mask = Image.open(mask_path).convert("L")
    mask_arr = np.array(mask) > 0
    if not mask_arr.any():
        return

    dilated = ndi.binary_dilation(mask_arr, iterations=border)
    outline = dilated & (~mask_arr)
    if not outline.any():
        return

    arr = np.array(img)
    color = np.array([11, 11, 11, 255], dtype=np.uint8)
    arr[outline] = color
    Image.fromarray(arr, mode="RGBA").save(image_path)


def generate_character_assets(
    *,
    character: Dict[str, Any],
    scenes_data: Dict[str, Any],
    screens_data: Dict[str, Any],
    story_md: str,
    character_style: Dict[str, Any],
    args: argparse.Namespace,
) -> None:
    # Build prompt context and render the sprite sheet for one character.
    screens = gather_screens_from_character_locations(
        screens_data.get("screens", []),
        character,
    )
    scenes = gather_scenes_for_screens(scenes_data.get("scenes", []), screens)
    dialogue_lines = gather_dialogue_for_character(args.dialogue_dir, character.get("id", ""))

    char_id = character.get("id") or character.get("name") or "character"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(char_id)).strip("_")
    if not safe_name:
        safe_name = "character"

    total_steps = 31
    progress: Optional[ProgressTracker] = None

    # Generate character overview sprite sheet
    out_path = os.path.join(args.out_dir, f"{safe_name}-overview.png")
    overview_exists = os.path.exists(out_path)
    if overview_exists:
        print(f"- Skipping overview gen for for {safe_name}")
    else:
        gpt_prompt = build_gpt_prompt(
            character=character,
            scenes=scenes,
            screens=screens,
            story_md=story_md,
            character_style=character_style,
            dialogue_lines=dialogue_lines,
        )

        if args.debug == 1:
            with open("debug.log", "w", encoding="utf-8") as f:
                f.write(gpt_prompt)
            print("Debug step 1 complete: wrote GPT-5.2 prompt to debug.log")
            sys.exit(0)

        char_name = character.get("name") or character.get("id") or "Character"
        print(f"{safe_name} - creating baseline character image prompt.")
        progress = ProgressTracker(total_steps, initial_done=0)
        prompt = generate_prompt_gpt(model=args.prompt_model, prompt_text=gpt_prompt)
        progress.advance(10)
        prompt = (
            "A sample sheet has been attached for layout reference only. **Change the character.**\n\n"
            + prompt
        )
        if args.debug == 2:
            with open("debug.log", "w", encoding="utf-8") as f:
                f.write(prompt)
            print("Debug step 2 complete: wrote GPT-5.2 output prompt to debug.log")
            sys.exit(0)

        def _render_one(idx: int) -> str:
            variant_path = out_path.replace("-overview.png", f"-overview-{idx}.png")
            model_name = "gpt-image-1.5" if args.image_provider == "openai" else args.model
            generate_image_with_provider(
                provider=args.image_provider,
                model=model_name,
                prompt_text=prompt,
                out_path=variant_path,
                reference_paths=reference_paths,
                aspect_ratio=args.aspect_ratio,
                resolution=args.resolution,
                openai_size=args.openai_size,
                openai_quality=args.openai_quality,
                openai_background="opaque" if args.image_provider == "openai" else None,
            )
            return variant_path

        print("- Sending to LLM for image generation.")
        reference_paths = [os.path.join(os.path.dirname(__file__), "pose_sheet_example.png")]
        variants = max(1, args.n)
        print(f"- Generating {variants} variants...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=variants) as executor:
            futures = {executor.submit(_render_one, i): i for i in range(1, variants + 1)}
            variant_paths = [None] * variants
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                variant_paths[idx - 1] = future.result()

        if variants == 1:
            selected = 1
        else:
            print("- Requesting LLM auto-selection...")
            selected = choose_best_variant(prompt, variant_paths, args.prompt_model)
        selected_path = variant_paths[selected - 1]
        os.replace(selected_path, out_path)
        for path in variant_paths:
            if path == selected_path:
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        print(f"Wrote: {out_path}")
    if progress:
        progress.advance(10)

    delete_unknown_sprites(out_path)
    if classified_images_exist(out_path):
        print(f"- Skipping segmentation; labeled crops exist for {safe_name}")
    else:
        if progress is None:
            progress = ProgressTracker(total_steps, initial_done=20)
        split_sprite_sheet(out_path, debug_mode=args.debug, progress=progress)
        if args.debug == 3:
            return

    required_dirs = [
        "portrait",
        "front",
        "front_left",
        "left",
        "back_left",
        "back",
        "back_right",
        "right",
        "front_right",
    ]
    all_dirs_created = all(
        os.path.exists(os.path.join(args.out_dir, f"{safe_name}-{direction}.png"))
        for direction in required_dirs
    )

    if all_dirs_created:
        print(f"- Skipping direction classification for {safe_name}")
    else:
        rename_with_direction(out_path, args.prompt_model, progress=progress)
        all_dirs_created = all(
            os.path.exists(os.path.join(args.out_dir, f"{safe_name}-{direction}.png"))
            for direction in required_dirs
        )
    if not all_dirs_created:
        print("ERROR: Missing direction. (TODO - have this auto regenerate the missing one)")



    talk_dirs = [
        "front",
        "front_left",
        "left",
        "right",
        "front_right",
    ]
    for direction in talk_dirs:
        print(f"- Processing talk direction: {direction}")
        dir_path = os.path.join(args.out_dir, f"{safe_name}-{direction}.png")
        if not os.path.exists(dir_path):
            print(f"- talking animation can't be made for missing {direction}.png")
            continue
        talk_paths = [
            os.path.join(args.out_dir, f"{safe_name}-{direction}-head_talk{idx}.png")
            for idx in range(0, 6)
        ]
        all_talks_found = all(os.path.exists(path) for path in talk_paths)

        if all_talks_found:
            print(f"- Skipping talk {direction} for {safe_name}")
        else:
            try:
                create_talking_head(
                    dir_path,
                    direction=direction,
                    provider=args.image_provider,
                    model=args.model,
                    openai_size=args.openai_size,
                )
            except Exception as exc:
                print(f"Warning: head mask failed for {dir_path}: {exc}")
    if progress:
        progress.finish()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--character",
        required=True,
        help="Character id or name (use 'all' or 'a' for all characters)",
    )
    ap.add_argument("--characters_json", default=os.path.join("story_specific_gen", "characters.json"))
    ap.add_argument("--scenes_json", default=os.path.join("story_specific_gen", "scenes.json"))
    ap.add_argument("--screens_json", default=os.path.join("story_specific", "screens.json"))
    ap.add_argument("--story_md", default=os.path.join("story_specific", "story.md"))
    ap.add_argument("--dialogue_dir", default=os.path.join("story_specific_gen", "dialogue"))
    ap.add_argument("--character_style_json", default=os.path.join("story_specific", "character_style.json"))
    ap.add_argument("--out_dir", default=os.path.join("story_specific_gen", "images", "characters"))
    ap.add_argument("--model", default=GEMINI_DEFAULT_MODEL)
    ap.add_argument("--prompt_model", default=GPT_DEFAULT_MODEL)
    ap.add_argument("--image_provider", choices=["gemini", "openai"], default="openai")
    ap.add_argument("--openai_size", default="1024x1024", help="OpenAI image size (e.g., 1024x1024)")
    ap.add_argument("--openai_quality", default="high", help="OpenAI image quality (e.g., high)")
    ap.add_argument("--aspect_ratio", default="1:1", help="Gemini image aspect ratio (e.g., 4:3)")
    ap.add_argument("--resolution", default="1K", help="Gemini resolution (e.g., 1K, 2K, 4K)")
    ap.add_argument("--n", type=int, default=3, help="Number of variants to generate per character")
    ap.add_argument("--debug", type=int, default=0, help="Debug step (e.g., 1 writes prompt to debug.log and exits)")
    args = ap.parse_args()

    if args.image_provider == "openai" and args.model == GEMINI_DEFAULT_MODEL:
        args.model = OPENAI_IMAGE_DEFAULT_MODEL

    for path in [
        args.characters_json,
        args.scenes_json,
        args.screens_json,
        args.story_md,
        args.character_style_json,
    ]:
        if not os.path.exists(path):
            raise SystemExit(f"Missing required file: {path}")

    characters_data = load_json(args.characters_json)
    scenes_data = load_json(args.scenes_json)
    screens_data = load_json(args.screens_json)
    story_md = load_text(args.story_md)
    character_style = load_json(args.character_style_json)

    characters_list = characters_data.get("characters", [])
    character_arg = args.character.strip().lower()
    if character_arg in ("all", "a"):
        if not characters_list:
            raise SystemExit("Character list contains no characters.")
        for character in characters_list:
            if not isinstance(character, dict):
                continue
            generate_character_assets(
                character=character,
                scenes_data=scenes_data,
                screens_data=screens_data,
                story_md=story_md,
                character_style=character_style,
                args=args,
            )
    else:
        character = find_character(characters_list, args.character)
        generate_character_assets(
            character=character,
            scenes_data=scenes_data,
            screens_data=screens_data,
            story_md=story_md,
            character_style=character_style,
            args=args,
        )


if __name__ == "__main__":
    main()
