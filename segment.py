#!/usr/bin/env python3
"""Generate hotspot segmentation masks for a screen image."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
from typing import List, Dict, Any, Tuple

from PIL import Image
from google import genai
from google.genai import types
import numpy as np


def parse_json(json_output: str) -> str:
    lines = json_output.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "```json":
            json_output = "\n".join(lines[i + 1:])
            json_output = json_output.split("```")[0]
            break
    return json_output.strip()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "hotspot"


def load_screen(screen_id: str, path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for screen in data.get("screens", []):
        if screen.get("id") == screen_id:
            return screen
    raise SystemExit(f"Screen id not found: {screen_id}")


def load_screen_names(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    names: Dict[str, str] = {}
    for screen in data.get("screens", []):
        sid = screen.get("id")
        if not sid:
            continue
        names[sid] = screen.get("name", sid)
    return names


def decode_mask(item: Dict[str, Any], im: Image.Image) -> Image.Image | None:
    box = item.get("box_2d")
    if not box or len(box) != 4:
        return None

    y0 = int(box[0] / 1000 * im.size[1])
    x0 = int(box[1] / 1000 * im.size[0])
    y1 = int(box[2] / 1000 * im.size[1])
    x1 = int(box[3] / 1000 * im.size[0])

    if y0 >= y1 or x0 >= x1:
        return None

    png_str = item.get("mask", "")
    if not png_str.startswith("data:image/png;base64,"):
        return None

    png_str = png_str.removeprefix("data:image/png;base64,")
    mask_data = base64.b64decode(png_str)
    mask = Image.open(io.BytesIO(mask_data)).convert("L")
    mask = mask.resize((x1 - x0, y1 - y0), Image.Resampling.BILINEAR)

    full_mask = Image.new("L", im.size, 0)
    full_mask.paste(mask, (x0, y0))
    return full_mask


def build_prompt(hotspot_name: str) -> str:
    return (
        "Give the segmentation masks for the following item in the image:\n"
        f"- {hotspot_name}\n\n"
        "Output a JSON list of segmentation masks where each entry contains "
        "the 2D bounding box in the key \"box_2d\", the segmentation mask "
        "in key \"mask\", and the text label in the key \"label\". "
        "Use descriptive labels."
    )


def build_nav_prompt(direction: str, transition: str, target_id: str, target_name: str) -> str:
    direction_text = direction or "unknown direction"
    transition_text = transition or "unknown transition"
    return (
        "Identify the clickable navigation hotspot for leaving this screen. "
        "Infer the most likely click area (e.g., door, hallway opening, or screen edge) "
        "based on the direction, transition and name. "
        f"Target screen: {target_id} ({target_name}). "
        f"The direction of the area is {direction_text}. It is via {transition_text}.\n\n"
        "You should highlight not just the sign, but the actual correct doorway."
        "Output a JSON list of segmentation masks where each entry contains "
        "the 2D bounding box in the key \"box_2d\", the segmentation mask "
        "in key \"mask\", and the text label in the key \"label\"."
    )


def build_parallax_prompt() -> str:
    return (
        "For a side-scroller: segment this image into red-ground plane that moves with player, "
        "blue-far background which doesn't move, and green-mid-background which moves at half speed. "
        "If the whole image is in a connected room, it should all be red. "
    )


def generate_masks(client: genai.Client, image: Image.Image, prompt: str, model: str) -> List[Dict[str, Any]]:
    # Ask Gemini for JSON-encoded segmentation masks and decode them.
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0)
    )
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image],
        config=config,
    )
    try:
        items = json.loads(parse_json(response.text))
    except json.JSONDecodeError as e:
        print(f"Warning: failed to parse JSON masks: {e}")
        print("Raw response:")
        print(response.text)
        return []
    if not isinstance(items, list):
        raise ValueError("Model response was not a JSON list")
    return items


def extract_image_bytes(response: Any) -> bytes:
    parts_direct = getattr(response, "parts", None) or []
    for part in parts_direct:
        inline = getattr(part, "inline_data", None)
        if inline:
            data = getattr(inline, "data", None)
            if data is None:
                continue
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            try:
                return base64.b64decode(data)
            except Exception:
                continue
        as_image = getattr(part, "as_image", None)
        if callable(as_image):
            try:
                img = as_image()
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
            except Exception:
                pass
    text = getattr(response, "text", None)
    if isinstance(text, str) and "base64," in text:
        try:
            b64 = text.split("base64,", 1)[1].strip()
            return base64.b64decode(b64)
        except Exception:
            pass
    raise ValueError("No image bytes found in response.")


def generate_parallax_mask(client: genai.Client, image: Image.Image, prompt: str, model: str) -> Image.Image:
    # Generate a parallax RGB mask (red/green/blue bands).
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(image_size="1K"),
    )
    stream = client.models.generate_content_stream(
        model=model,
        contents=[prompt, image],
        config=config,
    )
    for chunk in stream:
        cand = getattr(chunk, "candidates", None)
        if not cand or not cand[0].content or not cand[0].content.parts:
            continue
        part = cand[0].content.parts[0]
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            img_bytes = inline.data
            return Image.open(io.BytesIO(img_bytes)).convert("RGB")
    raise ValueError("No parallax image found in stream.")


def quantize_parallax_mask(mask: Image.Image) -> Image.Image:
    arr = np.array(mask.convert("RGB"), dtype=np.uint8)
    idx = arr.argmax(axis=2)
    out = np.zeros_like(arr)
    out[idx == 0] = [255, 0, 0]
    out[idx == 1] = [0, 255, 0]
    out[idx == 2] = [0, 0, 255]
    return Image.fromarray(out, mode="RGB")


def build_parallax_fill_prompt() -> str:
    return (
        "Fill in the missing background areas of this scene. "
        "The transparent/empty regions should be plausibly completed to match the "
        "existing background style and lighting. Do not add new objects. "
        "Do not include anything closer than the background."
        "Do not decide what to draw based on the size/shape of the missing area. The point is to draw what is behind the cutout part."
    )


def build_mid_fill_prompt() -> str:
    return (
        "Fill in the missing mid-background areas of this scene. "
        "The transparent/empty regions should be plausibly completed to match the "
        "existing midground style and lighting. Do not add new objects."
        "Do not decide what to draw based on the size/shape of the missing area. The point is to draw what is behind the cutout part."
    )


def generate_parallax_fill(client: genai.Client, image: Image.Image, prompt: str, model: str) -> Image.Image:
    # Fill masked regions using Gemini's inpainting-like response.
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(image_size="1K"),
    )
    stream = client.models.generate_content_stream(
        model=model,
        contents=[prompt, image],
        config=config,
    )
    for chunk in stream:
        cand = getattr(chunk, "candidates", None)
        if not cand or not cand[0].content or not cand[0].content.parts:
            continue
        part = cand[0].content.parts[0]
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            img_bytes = inline.data
            return Image.open(io.BytesIO(img_bytes)).convert("RGB")
    raise ValueError("No fill image found in stream.")


def extract_boxes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    boxes = []
    for item in items:
        box = item.get("box_2d")
        label = item.get("label")
        if not box or len(box) != 4:
            continue
        boxes.append({"label": label, "box_2d": box})
    return boxes


def check_overlaps(mask_entries: List[Dict[str, Any]]) -> None:
    # Report overlap between hotspot and navigation masks.
    for i in range(len(mask_entries)):
        for j in range(i + 1, len(mask_entries)):
            a = mask_entries[i]
            b = mask_entries[j]
            overlap = np.logical_and(a["mask"] > 0, b["mask"] > 0)
            if overlap.any():
                print(f"Warning: masks overlap: {a['name']} + {b['name']}")


def check_overlaps_from_manifest(manifest_path: str, screen_id: str, out_dir: str) -> None:
    entries = []
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("masks", []):
            if item.get("type") != "hotspot":
                continue
            path = item.get("file")
            name = item.get("name", "hotspot")
            if not path or not os.path.exists(path):
                print(f"Warning: mask file missing for overlap check: {path}")
                continue
            mask = Image.open(path).convert("L")
            entries.append({"name": f"hotspot:{name}", "mask": np.array(mask)})
    else:
        print(f"Warning: mask manifest not found for overlap check: {manifest_path}")

    for fname in os.listdir(out_dir):
        if not fname.startswith(f"{screen_id}-mask-to-") or not fname.endswith(".png"):
            continue
        path = os.path.join(out_dir, fname)
        try:
            mask = Image.open(path).convert("L")
        except Exception:
            print(f"Warning: failed to read nav mask for overlap check: {path}")
            continue
        entries.append({"name": f"nav:{fname}", "mask": np.array(mask)})

    if entries:
        check_overlaps(entries)


def main() -> None:
    # Create or update segmentation masks for a single screen.
    ap = argparse.ArgumentParser()
    ap.add_argument("screen_id", help="Screen id (e.g., HUB-01)")
    ap.add_argument("--screens_json", default=os.path.join("story_specific", "screens.json"))
    ap.add_argument("--image", default=None, help="Override image path")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--parallax_model", default="gemini-3-pro-image-preview")
    ap.add_argument("--parallax", action="store_true", help="Generate parallax mask")
    ap.add_argument("--debug", action="store_true", help="Save intermediate parallax outputs")
    ap.add_argument(
        "--out_dir",
        default=os.path.join("story_specific_gen", "images"),
        help="Output directory",
    )
    ap.add_argument("--ask", action="store_true", help="Prompt before each hotspot")
    ap.add_argument("--redo", action="store_true", help="Regenerate masks even if they already exist")
    args = ap.parse_args()

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before running.")

    screen = load_screen(args.screen_id, args.screens_json)
    screen_names = load_screen_names(args.screens_json)
    hotspots = screen.get("hotspots", [])
    if not hotspots:
        raise SystemExit(f"No hotspots found for {args.screen_id}")

    image_path = args.image or os.path.join(
        "story_specific_gen", "images", f"{args.screen_id}.png"
    )
    if not os.path.isfile(image_path):
        raise SystemExit(f"Image not found: {image_path}")

    os.makedirs(args.out_dir, exist_ok=True)
    im = Image.open(image_path)

    client = genai.Client(api_key=api_key)

    combined_mask = Image.new("L", im.size, 0)
    any_masks = False
    any_saved_masks = False
    mask_manifest: Dict[str, Any] = {"screen_id": args.screen_id, "masks": []}
    hotspot_masks_for_overlap: List[Dict[str, Any]] = []

    for idx, hotspot in enumerate(hotspots, start=1):
        name = hotspot.get("name", f"hotspot_{idx}")
        slug = slugify(name)
        hotspot_path = os.path.join(args.out_dir, f"{args.screen_id}-mask-{slug}.png")
        if os.path.exists(hotspot_path) and not args.redo:
            print(f"Skipping existing mask: {hotspot_path}")
            continue
        if args.ask:
            choice = input(f"Generate mask for hotspot '{name}'? [y/N] ").strip().lower()
            if choice not in {"y", "yes"}:
                continue

        prompt = build_prompt(name)
        items = generate_masks(client, im, prompt, args.model)
        mask_manifest["masks"].append({
            "type": "hotspot",
            "name": name,
            "slug": slug,
            "file": hotspot_path,
            "boxes": extract_boxes(items),
        })

        hotspot_mask = Image.new("L", im.size, 0)
        for item in items:
            mask = decode_mask(item, im)
            if mask is None:
                continue
            mask_array = np.array(mask)
            hotspot_mask = Image.fromarray(
                np.maximum(np.array(hotspot_mask), mask_array).astype("uint8")
            )

        if hotspot_mask.getbbox() is None:
            print(f"No mask generated for {name}")
            continue

        any_masks = True
        combined_mask = Image.fromarray(
            np.maximum(np.array(combined_mask), np.array(hotspot_mask)).astype("uint8")
        )
        hotspot_masks_for_overlap.append({"name": name, "mask": np.array(hotspot_mask)})

        hotspot_mask.save(hotspot_path)
        print(f"Saved {hotspot_path}")
        any_saved_masks = True

    connections = screen.get("connections", [])
    for conn in connections:
        to_id = conn.get("to")
        if not to_id:
            continue
        direction = conn.get("direction", "")
        transition = conn.get("transition", "")
        target_name = screen_names.get(to_id, to_id)
        nav_path = os.path.join(args.out_dir, f"{args.screen_id}-mask-to-{to_id}.png")
        if os.path.exists(nav_path) and not args.redo:
            print(f"Skipping existing mask: {nav_path}")
            continue
        if args.ask:
            label = f"{to_id} ({direction})" if direction else to_id
            choice = input(f"Generate nav mask to '{label}'? [y/N] ").strip().lower()
            if choice not in {"y", "yes"}:
                continue
        prompt = build_nav_prompt(direction, transition, to_id, target_name)
        items = generate_masks(client, im, prompt, args.model)
        nav_mask = Image.new("L", im.size, 0)
        for item in items:
            mask = decode_mask(item, im)
            if mask is None:
                continue
            nav_mask = Image.fromarray(
                np.maximum(np.array(nav_mask), np.array(mask)).astype("uint8")
            )
        if nav_mask.getbbox() is None:
            print(f"No nav mask generated for {to_id}")
            continue
        nav_arr = np.array(nav_mask)
        nav_arr = np.where(nav_arr > 0, 255, 0).astype("uint8")
        Image.fromarray(nav_arr, mode="L").save(nav_path)
        print(f"Saved {nav_path}")
        any_saved_masks = True

    if hotspot_masks_for_overlap:
        check_overlaps(hotspot_masks_for_overlap)

    if not args.ask and args.parallax:
        out_path = os.path.join(args.out_dir, f"{args.screen_id}-mask-parallax.png")
        if os.path.exists(out_path) and not args.redo:
            print(f"Skipping existing mask: {out_path}")
        else:
            prompt = build_parallax_prompt()
            parallax_raw = generate_parallax_mask(client, im, prompt, args.parallax_model)
            parallax_raw = parallax_raw.resize(im.size, Image.Resampling.NEAREST)
            if args.debug:
                raw_path = os.path.join(args.out_dir, f"{args.screen_id}-mask-parallax-raw.png")
                parallax_raw.save(raw_path)
                print(f"Saved {raw_path}")

            parallax_mask = quantize_parallax_mask(parallax_raw)
            if args.debug:
                quant_path = os.path.join(args.out_dir, f"{args.screen_id}-mask-parallax-quantized.png")
                parallax_mask.save(quant_path)
                print(f"Saved {quant_path}")

            mask_arr = np.array(parallax_mask)
            base_arr = np.array(im.convert("RGBA"))
            is_blue = np.all(mask_arr == [0, 0, 255], axis=2)
            is_red = np.all(mask_arr == [255, 0, 0], axis=2)
            magenta = np.array([255, 0, 255, 255], dtype=np.uint8)
            bg_masked = base_arr.copy()
            bg_masked[~is_blue] = magenta
            bg_masked_img = Image.fromarray(bg_masked, mode="RGBA")
            if args.debug:
                bg_masked_path = os.path.join(args.out_dir, f"{args.screen_id}-background-masked.png")
                bg_masked_img.save(bg_masked_path)
                print(f"Saved {bg_masked_path}")

            fill_prompt = build_parallax_fill_prompt()
            fill_img = generate_parallax_fill(client, bg_masked_img, fill_prompt, args.parallax_model)
            fill_path = os.path.join(args.out_dir, f"{args.screen_id}-background.png")
            fill_img = fill_img.resize(im.size, Image.Resampling.NEAREST)
            fill_img.save(fill_path)
            print(f"Saved {fill_path}")

            mid_masked = base_arr.copy()
            mid_masked[np.logical_or(is_red, is_blue)] = magenta
            mid_masked_img = Image.fromarray(mid_masked, mode="RGBA")
            if args.debug:
                mid_masked_path = os.path.join(args.out_dir, f"{args.screen_id}-middle-masked.png")
                mid_masked_img.save(mid_masked_path)
                print(f"Saved {mid_masked_path}")

            mid_prompt = build_mid_fill_prompt()
            mid_img = generate_parallax_fill(client, mid_masked_img, mid_prompt, args.parallax_model)
            mid_img = mid_img.resize(im.size, Image.Resampling.NEAREST).convert("RGBA")
            mid_arr = np.array(mid_img)
            mid_arr[is_blue] = [0, 0, 0, 0]
            mid_img = Image.fromarray(mid_arr, mode="RGBA")
            mid_path = os.path.join(args.out_dir, f"{args.screen_id}-middle.png")
            mid_img.save(mid_path)
            print(f"Saved {mid_path}")

            fg_arr = base_arr.copy()
            fg_arr[np.logical_or(is_blue, np.all(mask_arr == [0, 255, 0], axis=2))] = [0, 0, 0, 0]
            fg_img = Image.fromarray(fg_arr, mode="RGBA")
            fg_path = os.path.join(args.out_dir, f"{args.screen_id}-foreground.png")
            fg_img.save(fg_path)
            print(f"Saved {fg_path}")

    manifest_path = os.path.join(args.out_dir, f"{args.screen_id}-masks.json")
    if any_masks:
        if os.path.exists(manifest_path) and not args.redo:
            print(f"Skipping existing mask manifest: {manifest_path}")
        else:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(mask_manifest, f, indent=2)
            print(f"Saved {manifest_path}")

    if any_saved_masks:
        check_overlaps_from_manifest(manifest_path, args.screen_id, args.out_dir)
    else:
        print("No new masks saved; skipping overlap check.")


if __name__ == "__main__":
    main()
