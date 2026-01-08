"""
Render screen images from prompt files using LLMs, then post-process and segment.
"""
import argparse
import base64
import json
import os
import re
import concurrent.futures
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from openai import OpenAI

# GEMINI_DEFAULT_MODEL = "gemini-2.5-flash-image"
GEMINI_DEFAULT_MODEL = "gemini-3-pro-image-preview"
OPENAI_DEFAULT_MODEL = "gpt-image-1.5"


REF_RE = re.compile(r"^\s*-\s*REFERENCE_IMAGE:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Job:
    ordinal: int
    screen_id: str
    prompt_path: str


def read_index(index_path: str, prompts_dir: str) -> List[Job]:
    """
    Expects story_specific_gen/prompts/index.json written by the prior generator:
      { "order": ["HUB-07", "HUB-01", ...] }

    And prompt files named like:
      story_specific_gen/prompts/001_HUB-07.md
    """
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    order = index.get("order", [])
    jobs: List[Job] = []
    for i, sid in enumerate(order, start=1):
        # Find the matching prompt file by prefix (001_) + sid
        expected_prefix = f"{i:03d}_{sid}"
        candidates = [
            fn for fn in os.listdir(prompts_dir)
            if fn.startswith(expected_prefix) and fn.lower().endswith(".md")
        ]
        if not candidates:
            raise FileNotFoundError(f"Missing prompt file for {sid} (expected prefix {expected_prefix} in {prompts_dir})")
        prompt_path = os.path.join(prompts_dir, candidates[0])
        jobs.append(Job(ordinal=i, screen_id=sid, prompt_path=prompt_path))

    return jobs


def load_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_arrangement_instructions(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if line.strip()
        not in {
            "Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.",
            "Only make a line sketch so an artist has a guideline. No shading, colors, etc.",
        }
    ).strip()


def extract_global_style(prompt_text: str) -> str:
    lines = prompt_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "GLOBAL STYLE":
            start = i
            break
    if start is None:
        return ""
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "SCREEN ART NOTES":
            return "\n".join(lines[start:j]).strip()
    return "\n".join(lines[start:]).strip()


def get_global_style(art_style_path: str) -> str:
    if not os.path.exists(art_style_path):
        return ""
    with open(art_style_path, "r", encoding="utf-8") as f:
        global_style = json.load(f)

    lines: List[str] = []
    px = global_style.get("pixel_art", {})
    signage = global_style.get("signage", {})
    hotspots_style = global_style.get("hotspots", {})
    if px or signage or hotspots_style:
        lines.append("GLOBAL STYLE")
        if px:
            lines.append("PIXEL ART")
            if "native_resolution_px" in px:
                lines.append(f"- Native resolution: {px.get('native_resolution_px')}")
            if "aspect_ratio" in px:
                lines.append(f"- Aspect ratio: {px.get('aspect_ratio')}")
            if "upscale_factor" in px:
                lines.append(f"- Upscale factor: {px.get('upscale_factor')}×")
            if "output_resolution_px" in px:
                lines.append(f"- Output resolution: {px.get('output_resolution_px')}")
            if "scaling_algorithm" in px:
                lines.append(f"- Scaling algorithm: {px.get('scaling_algorithm')}")

            rc = px.get("rendering_constraints", {})
            if rc:
                lines.append("- Rendering constraints:")
                if "anti_aliasing" in rc:
                    lines.append(f"  - Anti-aliasing: {rc.get('anti_aliasing')}")
                if "subpixel_rendering" in rc:
                    lines.append(f"  - Subpixel rendering: {rc.get('subpixel_rendering')}")
                if "blur_filters" in rc:
                    lines.append(f"  - Blur filters: {rc.get('blur_filters')}")
                if "line_discipline" in rc:
                    lines.append(f"  - Line discipline: {rc.get('line_discipline')}")

            pr = px.get("perspective_rules", {})
            if pr:
                lines.append("- Perspective rules:")
                if "viewpoint" in pr:
                    lines.append(f"  - Viewpoint: {pr.get('viewpoint')}")
                if "horizon_line" in pr:
                    lines.append(f"  - Horizon line: {pr.get('horizon_line')}")
                if "convergence" in pr:
                    lines.append(f"  - Convergence: {pr.get('convergence')}")
                if "walkable_area" in pr:
                    lines.append(f"  - Walkable area: {pr.get('walkable_area')}")

            ps = px.get("palette_and_shading", {})
            if ps:
                lines.append("- Palette and shading:")
                if "scene_palette_target_colors" in ps:
                    lines.append(f"  - Palette target: {ps.get('scene_palette_target_colors')}")
                if "material_shading_ramps" in ps:
                    lines.append(f"  - Shading ramps: {ps.get('material_shading_ramps')}")
                if "lighting_direction" in ps:
                    lines.append(f"  - Lighting direction: {ps.get('lighting_direction')}")
                if "dither_usage" in ps:
                    lines.append(f"  - Dither usage: {ps.get('dither_usage')}")
                if "gradient_policy" in ps:
                    lines.append(f"  - Gradient policy: {ps.get('gradient_policy')}")

            if "outlines" in px:
                lines.append(f"- Outlines: {px.get('outlines')}")

            tv = px.get("texture_vocabulary", {})
            if tv:
                lines.append("- Texture vocabulary:")
                if "asphalt" in tv:
                    lines.append(f"  - Asphalt: {tv.get('asphalt')}")
                if "metal" in tv:
                    lines.append(f"  - Metal: {tv.get('metal')}")
                if "vegetation" in tv:
                    lines.append(f"  - Vegetation: {tv.get('vegetation')}")
                if "micro_noise_policy" in tv:
                    lines.append(f"  - Micro-noise policy: {tv.get('micro_noise_policy')}")

        if signage:
            lines.append("SIGNAGE")
            if "text_style" in signage:
                lines.append(f"- Text style: {signage.get('text_style')}")
            if "tone" in signage:
                lines.append(f"- Tone: {signage.get('tone')}")
            if "humor_rule" in signage:
                lines.append(f"- Humor rule: {signage.get('humor_rule')}")

        if hotspots_style:
            lines.append("HOTSPOTS")
            if "visual_rule" in hotspots_style:
                lines.append(f"- Visual rule: {hotspots_style.get('visual_rule')}")
            if "placement_strategy" in hotspots_style:
                lines.append(f"- Placement strategy: {hotspots_style.get('placement_strategy')}")

        lines.append("")

    return "\n".join(lines).strip()


def derive_sample_path(prompt_path: str, screen_id: str, prompts_dir: str) -> str:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        return os.path.join(prompts_dir, f"{screen_id}-sample.png")
    prefix = match.group(1)
    return os.path.join(prompts_dir, f"{prefix}_{screen_id}-sample.png")


def derive_arrangement_path(prompt_path: str, screen_id: str) -> str:
    base = os.path.basename(prompt_path)
    match = re.match(r"(\d+)_" + re.escape(screen_id) + r"\.md$", base)
    if not match:
        return ""
    prefix = match.group(1)
    return os.path.join(os.path.dirname(prompt_path), f"{prefix}_{screen_id}-arrangement.md")

def load_screen_connections(path: str) -> Dict[str, List[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    connections: Dict[str, set] = {}
    for raw in data.get("screens", []):
        sid = raw.get("id")
        if not sid:
            continue
        connections.setdefault(sid, set())
        for c in raw.get("connections", []):
            tid = c.get("to")
            if not tid:
                continue
            connections[sid].add(tid)
            connections.setdefault(tid, set()).add(sid)

    return {k: sorted(list(v)) for k, v in connections.items()}


def load_screen_names(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    names: Dict[str, str] = {}
    for raw in data.get("screens", []):
        sid = raw.get("id")
        if not sid:
            continue
        names[sid] = raw.get("name", sid)
    return names


def extract_reference_images(prompt_text: str) -> List[str]:
    # Parse "REFERENCE_IMAGE" entries in prompt text.
    refs = []
    for m in REF_RE.finditer(prompt_text):
        p = m.group(1).strip()
        refs.append(p)
    # De-dup while preserving order
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def dedupe_paths(paths: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def open_image_files(paths: List[str]) -> List[Any]:
    """
    Returns open file handles. Caller must close them.
    """
    handles = []
    for p in paths:
        handles.append(open(p, "rb"))
    return handles


class NonRetryableError(RuntimeError):
    pass


def call_with_retries(fn, max_retries: int = 6, base_sleep: float = 1.0, log_fn=None):
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                if log_fn:
                    log_fn(f"Retry attempt {attempt}...")
                else:
                    print(f"Retry attempt {attempt}...")
            return fn()
        except Exception as e:
            if isinstance(e, NonRetryableError):
                raise
            last_err = e
            # Simple backoff for 429/5xx/timeouts (SDK raises exceptions; message varies)
            sleep_s = base_sleep * (2 ** attempt)
            if attempt >= max_retries:
                break
            msg = f"Request failed ({type(e).__name__}): {e}. Sleeping {sleep_s:.1f}s before retry..."
            if log_fn:
                log_fn(msg)
            else:
                print(msg)
            time.sleep(sleep_s)
    raise last_err


def generate_one_image_openai(
    client: OpenAI,
    *,
    model: str,
    prompt_text: str,
    out_path: str,
    size: str,
    quality: str,
    output_format: str,
    reference_paths: List[str],
    extra_style_ref: Optional[str] = None,
    log_fn=None,
) -> Dict[str, Any]:
    """
    If reference_paths (or extra_style_ref) exist, uses images.edit with input images.
    Otherwise uses images.generate.

    images.generate + images.edit are described in the Images API reference. :contentReference[oaicite:3]{index=3}
    """
    refs = list(reference_paths)
    if extra_style_ref:
        # Put style ref first, so it is always included and stable.
        if extra_style_ref not in refs:
            refs.insert(0, extra_style_ref)

    # Filter to existing files (keep order), cap to 8 to stay reasonable
    refs_existing = [p for p in refs if os.path.exists(p)]
    refs_existing = refs_existing[:8]

    def _do_call():
        mode = "images.edit" if refs_existing else "images.generate"
        if log_fn:
            log_fn(f"Sending request via {mode} ({len(refs_existing)} reference image(s))...")
        else:
            print(f"Sending request via {mode} ({len(refs_existing)} reference image(s))...")
        if refs_existing:
            handles = open_image_files(refs_existing)
            try:
                rsp = client.images.edit(
                    model=model,
                    image=handles,
                    prompt=prompt_text,
                    size=size,
                    quality=quality,
                    output_format=output_format,
                )
            finally:
                for h in handles:
                    try:
                        h.close()
                    except Exception:
                        pass
        else:
            rsp = client.images.generate(
                model=model,
                prompt=prompt_text,
                n=1,
                size=size,
                quality=quality,
                output_format=output_format,
                )

        if log_fn:
            log_fn("Response received, writing image...")
        else:
            print("Response received, writing image...")
        b64 = rsp.data[0].b64_json
        img_bytes = base64.b64decode(b64)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img_bytes)

        return {
            "model": model,
            "size": size,
            "quality": quality,
            "output_format": output_format,
            "used_reference_images": refs_existing,
        }

    return call_with_retries(_do_call, log_fn=log_fn)


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
                    return base64.b64decode(data)
                except Exception:
                    pass
        as_image = getattr(part, "as_image", None)
        if callable(as_image):
            try:
                img = as_image()
                if img:
                    import io
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
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
            try:
                return base64.b64decode(data)
            except Exception:
                continue
    raise RuntimeError("Gemini response did not include image bytes.")


def _extract_gemini_text(response: Any) -> str:
    texts: List[str] = []
    parts_direct = getattr(response, "parts", None) or []
    for part in parts_direct:
        text = getattr(part, "text", None)
        if text:
            texts.append(str(text))
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                texts.append(str(text))
    return "\n".join(texts).strip()


def generate_one_image_gemini(
    *,
    model: str,
    prompt_text: str,
    out_path: str,
    reference_paths: List[str],
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    log_fn=None,
) -> Dict[str, Any]:
    try:
        from google import genai
        from google.genai import types as genai_types
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(
            "Missing dependencies. Run: pip install google-genai pillow"
        ) from e

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) before running.")

    client = genai.Client(api_key=api_key)
    model_id = model if "/" in model else f"models/{model}"

    # Gemini SDK support for inline image references may vary; only include them if Part is available.
    parts: List[Any] = [prompt_text]
    refs_existing = [p for p in reference_paths if os.path.exists(p)]
    if refs_existing:
        for p in refs_existing[:8]:
            try:
                parts.append(Image.open(p))
            except Exception:
                print(f"Warning: unable to open reference image {p}; skipping.")

    def _do_call():
        if refs_existing:
            if log_fn:
                log_fn(f"Sending request via Gemini ({len(refs_existing)} reference image(s))...")
                for p in refs_existing:
                    log_fn(f"  {p}")
            else:
                print(f"Sending request via Gemini ({len(refs_existing)} reference image(s))...")
                for p in refs_existing:
                    print(f"  {p}")
        else:
            if log_fn:
                log_fn("Sending request via Gemini (0 reference image(s))...")
            else:
                print("Sending request via Gemini (0 reference image(s))...")
        config = genai_types.GenerateContentConfig(response_modalities=["IMAGE"])
        if aspect_ratio or resolution:
            config = genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=genai_types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size=resolution,
                ),
            )
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=parts,
                config=config,
            )
        except Exception as e:
            emsg = str(e)
            if "unexpected model name format" in emsg or "INVALID_ARGUMENT" in emsg:
                raise NonRetryableError(f"{e} (model={model_id})") from e
            raise
        if log_fn:
            log_fn("Response received, writing image...")
        else:
            print("Response received, writing image...")
        try:
            img_bytes = _extract_gemini_image_bytes(response)
        except RuntimeError as e:
            text = _extract_gemini_text(response)
            if text:
                raise NonRetryableError(
                    f"{e} Response text: {text} "
                    f"(model={model}; this SDK/model may not support image output)"
                ) from e
            raise NonRetryableError(
                f"{e} (model={model}; this SDK/model may not support image output)"
            ) from e
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        return {
            "model": model,
            "used_reference_images": refs_existing,
        }

    return call_with_retries(_do_call, log_fn=log_fn)


def main() -> None:
    # Render prompts into images, then post-process and run segmentation.
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--prompts_dir",
        default=os.path.join("story_specific_gen", "prompts"),
        help="Directory containing prompt .md files and index.json",
    )
    ap.add_argument(
        "--index",
        default=os.path.join("story_specific_gen", "prompts", "index.json"),
        help="Path to index.json produced by generator",
    )
    ap.add_argument(
        "--images_dir",
        default=os.path.join("story_specific_gen", "images"),
        help="Output directory for generated PNGs",
    )
    ap.add_argument(
        "--screens_json",
        default=os.path.join("story_specific", "screens.json"),
        help="Path to screens.json for adjacency references",
    )
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai"], help="Image provider")
    ap.add_argument("--model", default=None, help="Model name (provider-specific)")
    ap.add_argument("--aspect_ratio", default=None, help="Gemini image aspect ratio (e.g., 1:1, 4:5, 16:9)")
    ap.add_argument("--resolution", default=None, help="Gemini resolution (e.g., 1K, 2K, 4K)")
    ap.add_argument("--size", default="1536x1024", help="GPT Image sizes: 1024x1024, 1536x1024, 1024x1536, or auto")
    ap.add_argument("--quality", default="high", help="GPT Image quality: high, medium, low, or auto")
    ap.add_argument("--output_format", default="png", help="png, jpeg, or webp")
    ap.add_argument("--redo_all", action="store_true", help="Regenerate even if output images exist")
    ap.add_argument("--redo", action="store_true", help="Force regeneration for --generate")
    ap.add_argument(
        "--style_ref",
        default=None,
        help="Optional: a single image path to include as an extra reference for every screen after it exists "
             "(e.g., story_specific_gen/images/HUB-01.png)",
    )
    ap.add_argument("--n", type=int, default=1, help="Number of variants to generate per screen")
    ap.add_argument(
        "--raw_dir",
        default=os.path.join("story_specific_gen", "images", "raw"),
        help="Directory to store selected raw renders",
    )
    ap.add_argument("--open_variants", action="store_true", help="Open variant images for selection")
    ap.add_argument("--generate", default=None, help="Only generate a single screen id (e.g., HUB-01)")
    ap.add_argument("--auto_select", action="store_true", help="Auto-select best variant via LLM")
    ap.add_argument("--auto_select_model", default="gpt-5.2", help="Model for auto selection")
    ap.add_argument(
        "--art_style",
        default=os.path.join("story_specific", "art_style.json"),
        help="Path to art_style.json",
    )
    ap.add_argument("--debug", type=int, default=0, help="Debug step (1 writes prompt to debug.log and exits)")

    args = ap.parse_args()
    if args.generate and args.redo:
        args.redo_all = True

    model_name = args.model or (GEMINI_DEFAULT_MODEL if args.provider == "gemini" else OPENAI_DEFAULT_MODEL)

    openai_client = None
    if args.provider == "openai":
        openai_client = OpenAI()  # reads OPENAI_API_KEY per quickstart :contentReference[oaicite:4]{index=4}

    jobs = read_index(args.index, args.prompts_dir)
    if args.generate:
        jobs = [job for job in jobs if job.screen_id == args.generate]
        if not jobs:
            raise SystemExit(f"Screen id not found in index: {args.generate}")
    screen_connections: Optional[Dict[str, List[str]]] = None
    screen_name_map: Dict[str, str] = {}
    if args.screens_json and os.path.exists(args.screens_json):
        screen_connections = load_screen_connections(args.screens_json)
        screen_name_map = load_screen_names(args.screens_json)

    manifest: Dict[str, Any] = {
        "generated_at_unix": int(time.time()),
        "provider": args.provider,
        "model": model_name,
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
        "screens": []
    }

    # If style_ref is set but not present yet, we will only start using it once it exists.
    style_ref_path = args.style_ref

    single = len(jobs) == 1
    for idx, job in enumerate(jobs, start=1):
        out_path = os.path.join(args.images_dir, f"{job.screen_id}.png")
        if args.generate and args.redo:
            args.redo_all = True
        if not args.redo_all and os.path.exists(out_path):
            manifest["screens"].append({
                "screen_id": job.screen_id,
                "prompt_path": job.prompt_path,
                "output_path": out_path,
                "skipped": True
            })
            seg_script = os.path.join(os.path.dirname(__file__), "segment.py")
            seg_cmd = [
                sys.executable,
                seg_script,
                job.screen_id,
            ]
            print(f"  Segmenting {job.screen_id}...")
            seg_run = subprocess.run(
                seg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if seg_run.returncode != 0:
                print("Warning: segment.py failed:")
            if seg_run.stdout:
                for line in seg_run.stdout.splitlines():
                    print(f"    {line}")
            continue
        variants = max(1, args.n)
        variant_meta = []
        selected_variant = 1
        os.makedirs(args.raw_dir, exist_ok=True)

        arrangement_path = derive_arrangement_path(job.prompt_path, job.screen_id)
        sample_path = derive_sample_path(job.prompt_path, job.screen_id, args.prompts_dir)

        if arrangement_path and os.path.exists(arrangement_path):
            job.prompt_path = arrangement_path
        prompt_text = load_prompt(job.prompt_path)
        global_style_text = get_global_style(args.art_style)
        base_prompt_text = strip_arrangement_instructions(prompt_text)
        refs = extract_reference_images(prompt_text)

        if os.path.exists(sample_path):
            refs.insert(0, sample_path)
            prompt_text = (
                "CRITICAL INSTRUCTIONS - READ BEFORE PROCESSING\n\n"
                + "You are a highly disciplined pixel artist engine. Your primary goal is strict adherence to the TEXTUAL REQUIREMENTS list below.\n\n"
                + "1. THE \"NO FLOATING TEXT\" RULE (PRIORITY ZRO):\n"
                + "The most important constraint is that there is absolutely no UI text, floating labels, or meta-arrows in the final image. The provided sketch contains text labels as guidelines (e.g., \"TUBE BAY <-\", \"OPS CORRIDOR ->\", \"LOBBY RETURN\"). You must interpret these labels as instructions to create physical, in-world signage at those locations. If in doubt, leave the door/transition signage out.\n"
                + "    BAD: Rendering the text \"TUBE BAY <-\" floating in the air near a door. Or putting it on the ground where it wouldn't make sense.'\n"
                + "    GOOD: Creating a metal plaque bolted to the wall above the door that reads \"TUBE BAY\" in pixel text, or painting directional lines and text onto the floor asphalt.\n\n"
                + "2. INTERPRETING REFERENCES VS. SKETCH:\n"
                + "    The Sketch (image_0.png): Use this for the rough compositional layout ONLY (where the doors, the main machine, and the central door are placed relative to each other).\n"
                + "    The Style References (e.g., image_1.png): Use these ONLY for style, color palette, lighting atmosphere, and texture vocabularies (how metal, concrete, and doors look). Do NOT copy their architectural layout.\n\n"
                + "3. CREATIVE EXECUTION:\n"
                + "Do NOT follow the mock-up sketch exactly; be creative within the constraints. Where the text requirements below conflict with the sketch details (e.g., specific objects present, texture details), the TEXT must take precedence.\n\n"
                + "--------------------------------------------------\n"
                + "Now, generate the image based on these detailed specifications:\n\n"
                + "REQUIREMENTS:\n\n"
                + base_prompt_text
                + ("\n\n" + global_style_text if global_style_text else "")
                + "\n\nFINAL CHECK:\nBefore generating, ensure there is zero text in the image that isn't part of a physical object in the scene, not floating. Ensure that the correct transitions exist."
            )
        if screen_connections:
            for neighbor_id in screen_connections.get(job.screen_id, []):
                candidate = os.path.join(args.images_dir, f"{neighbor_id}.png")
                if os.path.exists(candidate):
                    refs.append(candidate)
        refs = dedupe_paths(refs)

        # Only use the global style reference if it exists on disk at call time.
        extra_style_ref = style_ref_path if (style_ref_path and os.path.exists(style_ref_path)) else None

        if args.debug == 1:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(prompt_text)
                handle.write("\n\nREFERENCE_IMAGES:\n")
                for idx, ref in enumerate(refs):
                    if idx == 0 and os.path.exists(sample_path) and ref == sample_path:
                        handle.write(f"- image_0.png - sketch - {ref}\n")
                        continue
                    label_idx = idx
                    name = os.path.splitext(os.path.basename(ref))[0]
                    room_name = screen_name_map.get(name, name)
                    handle.write(f"- image_{label_idx}.png - adjacent room - {room_name}\n")
                handle.write("\nEXTRA_STYLE_REF:\n")
                handle.write(f"{extra_style_ref}\n")
            print("Debug step 1 complete: wrote prompt and references to debug.log")
            return

        percent = int((idx - 1) / max(len(jobs), 1) * 100)
        header = f"{percent:02d}% [{job.ordinal:03d}] {job.screen_id} - {screen_name_map.get(job.screen_id, job.screen_id)}"
        if not single:
            print(header)
        def log_fn(message: str) -> None:
            print(f"  {message}")

        while True:
            def _run_variant(variant_idx: int):
                logs: List[str] = []
                def _log(msg: str) -> None:
                    logs.append(msg)

                raw_out_path = os.path.join(
                    args.images_dir, f"{job.screen_id}-raw-v{variant_idx}.png"
                )
                final_variant_path = os.path.join(
                    args.images_dir, f"{job.screen_id}-v{variant_idx}.png"
                )
                raw_variant_target = os.path.join(
                    args.raw_dir, f"{job.screen_id}-raw-v{variant_idx}.png"
                )

                if args.provider == "openai":
                    meta = generate_one_image_openai(
                        openai_client,
                        model=model_name,
                        prompt_text=prompt_text,
                        out_path=raw_out_path,
                        size=args.size,
                        quality=args.quality,
                        output_format=args.output_format,
                        reference_paths=refs,
                        extra_style_ref=extra_style_ref,
                        log_fn=_log,
                    )
                else:
                    refs_with_style = [extra_style_ref] + refs if extra_style_ref else refs
                    meta = generate_one_image_gemini(
                        model=model_name,
                        prompt_text=prompt_text,
                        out_path=raw_out_path,
                        reference_paths=refs_with_style,
                        aspect_ratio=args.aspect_ratio,
                        resolution=args.resolution,
                        log_fn=_log,
                    )

                snapper_script = os.path.join(os.path.dirname(__file__), "pixel_snapper.py")
                snapper_cmd = [
                    sys.executable,
                    snapper_script,
                    raw_out_path,
                    final_variant_path,
                    "64",
                ]
                snapper_run = subprocess.run(
                    snapper_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if snapper_run.returncode != 0:
                    raise RuntimeError(
                        "pixel_snapper failed:\n" + snapper_run.stdout
                    )
                os.replace(raw_out_path, raw_variant_target)
                return {
                    "variant": variant_idx,
                    "raw_output_path": raw_variant_target,
                    "output_path": final_variant_path,
                    "logs": logs,
                    **meta,
                }

            with concurrent.futures.ThreadPoolExecutor(max_workers=variants) as executor:
                futures = {executor.submit(_run_variant, i): i for i in range(1, variants + 1)}
                variant_meta = []
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    variant_meta.append(result)

            variant_meta.sort(key=lambda x: x["variant"])
            for v in variant_meta:
                for line in v.get("logs", []):
                    print(f"    {line}")

            if variants > 1:
                if args.auto_select:
                    auto_prompt = (
                        "Choose the image that best matches the intent of the prompt. "
                        "Return only the text 1, 2, or 3."
                    )
                    content = [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_text", "text": auto_prompt},
                    ]
                    for v in variant_meta:
                        with open(v["output_path"], "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("ascii")
                        content.append(
                            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}
                        )
                    print("  Requesting LLM auto-selection...")
                    selector = OpenAI()
                    rsp = selector.responses.create(
                        model=args.auto_select_model,
                        input=[{"role": "user", "content": content}],
                    )
                    choice = rsp.output_text.strip()
                    if choice in {"1", "2", "3"}:
                        selected_variant = int(choice)
                    else:
                        print(f"  Warning: auto-select failed: {choice}. Using 1.")
                        selected_variant = 1
                elif args.open_variants:
                    opener = shutil.which("xdg-open")
                    if opener:
                        for v in variant_meta:
                            subprocess.Popen([opener, v["output_path"]])
                    else:
                        print("  Warning: xdg-open not found; cannot auto-open variants.")
                if not args.auto_select:
                    print("Select a variant (0 to retry):")
                    for v in variant_meta:
                        print(f"  {v['variant']}: {v['output_path']}")
                    while True:
                        choice = input("Enter choice number: ").strip()
                        if choice.isdigit():
                            pick = int(choice)
                            if pick == 0:
                                for v in variant_meta:
                                    for path_key in ("output_path", "raw_output_path"):
                                        try:
                                            os.remove(v[path_key])
                                        except FileNotFoundError:
                                            pass
                                break
                            if 1 <= pick <= variants:
                                selected_variant = pick
                                break
                        print(f"Invalid choice. Enter 0 or a number between 1 and {variants}.")
                    if choice.isdigit() and int(choice) == 0:
                        continue
            break

        raw_target = os.path.join(args.raw_dir, f"{job.screen_id}-raw.png")
        manifest["screens"].append({
            "screen_id": job.screen_id,
            "prompt_path": job.prompt_path,
            "output_path": out_path,
            "raw_output_path": raw_target,
            "skipped": False,
            "variants": variant_meta,
            "selected_variant": selected_variant,
        })

        chosen = variant_meta[selected_variant - 1]
        if os.path.exists(out_path):
            for fname in os.listdir(args.images_dir):
                if not (fname.startswith(f"{job.screen_id}-mask-") or fname.startswith(f"{job.screen_id}-masks")):
                    continue
                try:
                    os.remove(os.path.join(args.images_dir, fname))
                except FileNotFoundError:
                    pass
        os.replace(chosen["output_path"], out_path)
        os.replace(chosen["raw_output_path"], raw_target)
        for v in variant_meta:
            if v["variant"] == selected_variant:
                continue
            for path_key in ("output_path", "raw_output_path"):
                try:
                    os.remove(v[path_key])
                except FileNotFoundError:
                    pass

        seg_script = os.path.join(os.path.dirname(__file__), "segment.py")
        seg_cmd = [
            sys.executable,
            seg_script,
            job.screen_id,
        ]
        print(f"  Segmenting {job.screen_id}...")
        seg_run = subprocess.run(
            seg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if seg_run.returncode != 0:
            print("Warning: segment.py failed:")
        if seg_run.stdout:
            for line in seg_run.stdout.splitlines():
                print(f"    {line}")

        print(f"  Wrote {out_path}")

    os.makedirs(args.images_dir, exist_ok=True)
    manifest_path = os.path.join(args.images_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
