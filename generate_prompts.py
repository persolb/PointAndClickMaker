"""
Generate per-screen prompt markdown files and an index ordering.
"""
import argparse
import json
import os
import heapq
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional


@dataclass(frozen=True)
class Connection:
    to: str
    direction: str
    transition: str
    continuity: List[str]


@dataclass
class Screen:
    id: str
    name: str
    category: str
    importance: int
    art: Dict
    elements: List[str]
    hotspots: List[Dict]
    connections: List[Connection]


def load_screens(path: str) -> Tuple[Dict[str, Screen], Dict]:
    # Load screens.json and normalize into Screen objects.
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    screens_by_id: Dict[str, Screen] = {}
    for raw in data["screens"]:
        conns: List[Connection] = []
        for c in raw.get("connections", []):
            conns.append(
                Connection(
                    to=c["to"],
                    direction=c.get("direction", ""),
                    transition=c.get("transition", ""),
                    continuity=c.get("continuity", []),
                )
            )
        screens_by_id[raw["id"]] = Screen(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            category=raw.get("category", "unknown"),
            importance=int(raw.get("importance", 0)),
            art=raw.get("art", {}),
            elements=raw.get("elements", []),
            hotspots=raw.get("hotspots", []),
            connections=conns,
        )

    return screens_by_id, {}


def validate_graph(screens: Dict[str, Screen]) -> List[str]:
    errors: List[str] = []
    for s in screens.values():
        for c in s.connections:
            if c.to not in screens:
                errors.append(f"{s.id} connects to missing screen id: {c.to}")
    return errors


def pick_start_screen(screens: Dict[str, Screen]) -> Screen:
    # Highest importance wins; tie-break by id for determinism.
    return sorted(screens.values(), key=lambda x: (-x.importance, x.id))[0]


def build_prompt(
    screen: Screen,
    global_style: Dict,
    screens: Dict[str, Screen],
    generated: Set[str],
    images_dir: str,
) -> str:
    # Assemble a complete prompt text for one screen.
    lines: List[str] = []
    lines.append(f"PIXEL ART BACKGROUND PROMPT")
    lines.append(f"Screen ID: {screen.id}")
    lines.append(f"Name: {screen.name}")
    lines.append(f"Category: {screen.category}")
    lines.append("")

    # Global style block (short, but consistent)
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

    # Screen-specific art notes
    lines.append("SCREEN ART NOTES")
    for k in ["shot_type", "camera", "lighting", "composition"]:
        v = screen.art.get(k)
        if v:
            lines.append(f"- {k}: {v}")
    lines.append("")

    # Required elements
    lines.append("REQUIRED ENVIRONMENT ELEMENTS")
    for e in screen.elements:
        lines.append(f"- {e}")
    lines.append("")

    # Hotspots (silhouette planning)
    if screen.hotspots:
        lines.append("INTENDED HOTSPOTS (FOR SILHOUETTE / VALUE PLANNING)")
        for h in screen.hotspots:
            name = h.get("name", "hotspot")
            note = h.get("readability")
            if note:
                lines.append(f"- {name} — {note}")
            else:
                lines.append(f"- {name}")
        lines.append("")

    # Connections and continuity
    lines.append("ADJACENT SCREENS AND TRANSITIONS")
    if not screen.connections:
        lines.append("- (none)")
    else:
        for c in screen.connections:
            neighbor = screens.get(c.to)
            nlabel = f"{c.to} — {neighbor.name}" if neighbor else c.to
            lines.append(f"- To {nlabel}")
            if c.direction:
                lines.append(f"  - Direction: {c.direction}")
            if c.transition:
                lines.append(f"  - Transition: {c.transition}")
            if c.continuity:
                lines.append(f"  - Continuity cues: {', '.join(c.continuity)}")

            # If neighbor already generated, add reference image path (if present)
            if c.to in generated:
                candidate = os.path.join(images_dir, f"{c.to}.png")
                lines.append(f"  - REFERENCE_IMAGE: {candidate}")
                #else:
                #    lines.append(f"  - REFERENCE_IMAGE: (expected at {candidate}, file not found)")
    lines.append("")

    # Guidance for continuity across seams
    lines.append("SEAM CONTINUITY GUIDANCE")
    lines.append("- Keep edge landmarks aligned with adjacent screens (fence line, doorway framing, signage family).")
    lines.append("- Preserve consistent material textures (reuse asphalt/fence/vegetation cluster vocabulary).")
    lines.append("")

    return "\n".join(lines)


def generate_order_outward_by_importance(screens: Dict[str, Screen]) -> List[str]:
    # Walk the graph outward using importance to pick the next screen.
    start = pick_start_screen(screens)

    generated: Set[str] = set()
    order: List[str] = []

    # Priority queue entries: (-importance, distance, id)
    # distance grows outward from the start via connections.
    pq: List[Tuple[int, int, str]] = []
    heapq.heappush(pq, (-start.importance, 0, start.id))

    best_distance: Dict[str, int] = {start.id: 0}

    while pq:
        neg_imp, dist, sid = heapq.heappop(pq)
        if sid in generated:
            continue

        generated.add(sid)
        order.append(sid)

        s = screens[sid]
        for c in s.connections:
            nid = c.to
            if nid not in screens or nid in generated:
                continue
            ndist = dist + 1
            prev = best_distance.get(nid)
            if prev is None or ndist < prev:
                best_distance[nid] = ndist
            # Candidate priority favors importance, then proximity to the already-built cluster.
            heapq.heappush(pq, (-screens[nid].importance, best_distance[nid], nid))

    # Append any disconnected screens (sorted by importance)
    disconnected = [s for s in screens.keys() if s not in generated]
    disconnected_sorted = sorted(disconnected, key=lambda x: (-screens[x].importance, x))
    order.extend(disconnected_sorted)

    return order


def main() -> None:
    # CLI entry point for prompt generation and index writing.
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join("story_specific", "screens.json"), help="Path to screens.json")
    ap.add_argument(
        "--art_style",
        default=os.path.join("story_specific", "art_style.json"),
        help="Path to art_style.json",
    )
    ap.add_argument(
        "--out",
        default=os.path.join("story_specific_gen", "prompts"),
        help="Output directory for prompt files",
    )
    ap.add_argument(
        "--images",
        default=os.path.join("story_specific_gen", "images"),
        help="Directory containing generated images named <ID>.png",
    )
    args = ap.parse_args()

    screens, _ = load_screens(args.input)
    if not os.path.exists(args.art_style):
        raise SystemExit(f"Missing art_style.json: {args.art_style}")
    with open(args.art_style, "r", encoding="utf-8") as f:
        global_style = json.load(f)
    errs = validate_graph(screens)
    if errs:
        print("Warning: screens.json has missing connections:")
        for err in errs:
            print(f"- {err}")

    os.makedirs(args.out, exist_ok=True)

    order = generate_order_outward_by_importance(screens)
    generated: Set[str] = set()

    index = {"order": order}

    for i, sid in enumerate(order, start=1):
        s = screens[sid]
        prompt = build_prompt(
            screen=s,
            global_style=global_style,
            screens=screens,
            generated=generated,
            images_dir=args.images,
        )
        fname = os.path.join(args.out, f"{i:03d}_{sid}.md")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(prompt)

        generated.add(sid)

    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    print(f"Wrote {len(order)} prompts to: {args.out}")


if __name__ == "__main__":
    main()
