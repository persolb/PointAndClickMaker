#!/usr/bin/env python3
"""Pixel snapper: quantize palette, infer grid, resample to crisp pixels."""

from __future__ import annotations

import argparse
import io
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image


class PixelSnapperError(Exception):
    pass


@dataclass
class Config:
    k_colors: int = 64
    k_seed: int = 42
    input_path: str = "samples/2/skeleton.png"
    output_path: str = "samples/2/skeleton_fixed_clean2.png"
    target_width: int = 640
    target_height: int = 480
    method: str = "naive"
    grid_mode: str = "target"
    quantize_method: str = "diverse"
    keep_colors: Optional[List[Tuple[int, int, int]]] = None
    outliers: int = 0
    outliers_far: int = 0
    outliers_min_pct: float = 0.05
    outliers_min_count: int = 50
    palette_max_candidates: int = 4096
    palette_bin_bits: int = 5
    palette_weight_power: float = 0.35
    max_kmeans_iterations: int = 15
    peak_threshold_multiplier: float = 0.2
    peak_distance_filter: int = 4
    walker_search_window_ratio: float = 0.35
    walker_min_search_window: float = 2.0
    walker_strength_threshold: float = 0.5
    min_cuts_per_axis: int = 4
    fallback_target_segments: int = 64
    max_step_ratio: float = 1.8


def validate_image_dimensions(width: int, height: int) -> None:
    if width == 0 or height == 0:
        raise PixelSnapperError("Image dimensions cannot be zero")
    if width > 10000 or height > 10000:
        raise PixelSnapperError("Image dimensions too large (max 10000x10000)")


def _fit_to_target(width: int, height: int, target_w: int, target_h: int) -> Tuple[int, int]:
    if target_w <= 0 or target_h <= 0:
        return width, height
    if width == 0 or height == 0:
        return width, height
    scale = max(target_w / float(width), target_h / float(height))
    if not math.isfinite(scale) or scale <= 0.0:
        return width, height
    new_w = max(int(round(width * scale)), 1)
    new_h = max(int(round(height * scale)), 1)
    return new_w, new_h


def _dist_sq(p: Sequence[float], c: Sequence[float]) -> float:
    dr = p[0] - c[0]
    dg = p[1] - c[1]
    db = p[2] - c[2]
    return dr * dr + dg * dg + db * db


def _weighted_index_sample(rng: random.Random, weights: List[float]) -> int:
    total = sum(weights)
    if total <= 0.0:
        return rng.randrange(len(weights))
    r = rng.random() * total
    upto = 0.0
    for i, w in enumerate(weights):
        upto += w
        if r <= upto:
            return i
    return len(weights) - 1


def _build_palette_diverse(rgba: Image.Image, config: Config) -> List[Tuple[int, int, int]]:
    pixels = list(rgba.getdata())
    counts: Dict[Tuple[int, int, int], int] = {}
    for r, g, b, a in pixels:
        if a == 0:
            continue
        key = (r, g, b)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return []

    items = list(counts.items())
    if len(items) > config.palette_max_candidates:
        shift = max(8 - config.palette_bin_bits, 0)
        bcounts: Dict[Tuple[int, int, int], int] = {}
        for (r, g, b), c in items:
            if shift > 0:
                rb = r >> shift
                gb = g >> shift
                bb = b >> shift
                r = min((rb << shift) + (1 << (shift - 1)), 255)
                g = min((gb << shift) + (1 << (shift - 1)), 255)
                b = min((bb << shift) + (1 << (shift - 1)), 255)
            key = (r, g, b)
            bcounts[key] = bcounts.get(key, 0) + c
        items = list(bcounts.items())

    items.sort(key=lambda kv: (-kv[1], kv[0]))
    k = min(config.k_colors, len(items))
    if k <= 0:
        return []

    chosen: List[Tuple[int, int, int]] = []
    keep = list(config.keep_colors or [])
    for c in keep:
        if c not in chosen:
            chosen.append(c)

    max_count = max(c for _, c in items)

    def score(color: Tuple[int, int, int], count: int, chosen_colors: List[Tuple[int, int, int]]) -> float:
        if not chosen_colors:
            return float("inf")
        min_d = None
        for other in chosen_colors:
            d = _dist_sq(color, other)
            if min_d is None or d < min_d:
                min_d = d
        weight = (count / max_count) ** config.palette_weight_power if max_count > 0 else 1.0
        return (min_d or 0.0) * weight

    if not chosen:
        chosen.append(items[0][0])

    remaining = {color: count for color, count in items if color not in chosen}
    while len(chosen) < k and remaining:
        best_color = None
        best_score = -1.0
        for color, count in remaining.items():
            s = score(color, count, chosen)
            if s > best_score:
                best_score = s
                best_color = color
        if best_color is None:
            break
        chosen.append(best_color)
        remaining.pop(best_color, None)

    if len(chosen) < k:
        for color, _ in items:
            if color not in chosen:
                chosen.append(color)
            if len(chosen) >= k:
                break

    return chosen[:k]


def quantize_image(img: Image.Image, config: Config) -> Image.Image:
    # Reduce to a fixed palette, preserving explicit keep colors when provided.
    if config.k_colors <= 0:
        raise PixelSnapperError("Number of colors must be greater than 0")

    rgba = img.convert("RGBA")
    if config.quantize_method == "diverse":
        alpha = rgba.split()[3]
        rgb = rgba.convert("RGB")
        palette_colors = _build_palette_diverse(rgba, config)
        if not palette_colors:
            return rgba.copy()
        merged = palette_colors[: config.k_colors]
        pal_img = Image.new("P", (1, 1))
        flat: List[int] = []
        for c in merged:
            flat.extend([c[0], c[1], c[2]])
        flat.extend([0] * (768 - len(flat)))
        pal_img.putpalette(flat)
        quantized = rgb.quantize(palette=pal_img, dither=Image.NONE)
        out = quantized.convert("RGBA")
        out.putalpha(alpha)
        return out

    if config.quantize_method == "fast":
        alpha = rgba.split()[3]
        rgb = rgba.convert("RGB")
        method = Image.MEDIANCUT if hasattr(Image, "MEDIANCUT") else 0
        quantized = rgb.quantize(colors=config.k_colors, method=method, dither=Image.NONE)
        palette = quantized.getpalette()[: config.k_colors * 3]
        palette_colors = [
            (palette[i], palette[i + 1], palette[i + 2])
            for i in range(0, len(palette), 3)
        ]
        if config.keep_colors:
            merged: List[Tuple[int, int, int]] = []
            for c in config.keep_colors:
                if c not in merged:
                    merged.append(c)
            for c in palette_colors:
                if c not in merged:
                    merged.append(c)
            palette_colors = merged
        if palette_colors:
            merged = palette_colors[: config.k_colors]
            pal_img = Image.new("P", (1, 1))
            flat: List[int] = []
            for c in merged:
                flat.extend([c[0], c[1], c[2]])
            flat.extend([0] * (768 - len(flat)))
            pal_img.putpalette(flat)
            quantized = rgb.quantize(palette=pal_img, dither=Image.NONE)
        out = quantized.convert("RGBA")
        out.putalpha(alpha)
        return out

    pixels = list(rgba.getdata())
    opaque_pixels: List[List[float]] = [
        [float(r), float(g), float(b)] for (r, g, b, a) in pixels if a != 0
    ]
    n_pixels = len(opaque_pixels)
    if n_pixels == 0:
        return rgba.copy()

    rng = random.Random(config.k_seed)
    k = min(config.k_colors, n_pixels)

    centroids: List[List[float]] = []
    first_idx = rng.randrange(n_pixels)
    centroids.append(opaque_pixels[first_idx])
    distances = [float("inf")] * n_pixels

    for _ in range(1, k):
        last_c = centroids[-1]
        sum_sq_dist = 0.0
        for i, p in enumerate(opaque_pixels):
            d_sq = _dist_sq(p, last_c)
            if d_sq < distances[i]:
                distances[i] = d_sq
            sum_sq_dist += distances[i]

        if sum_sq_dist <= 0.0:
            idx = rng.randrange(n_pixels)
            centroids.append(opaque_pixels[idx])
        else:
            idx = _weighted_index_sample(rng, distances)
            centroids.append(opaque_pixels[idx])

    prev_centroids = [c[:] for c in centroids]
    for iteration in range(config.max_kmeans_iterations):
        sums = [[0.0, 0.0, 0.0] for _ in range(k)]
        counts = [0] * k

        for p in opaque_pixels:
            min_dist = float("inf")
            best_k = 0
            for i, c in enumerate(centroids):
                d = _dist_sq(p, c)
                if d < min_dist:
                    min_dist = d
                    best_k = i
            sums[best_k][0] += p[0]
            sums[best_k][1] += p[1]
            sums[best_k][2] += p[2]
            counts[best_k] += 1

        for i in range(k):
            if counts[i] > 0:
                fcount = float(counts[i])
                centroids[i] = [
                    sums[i][0] / fcount,
                    sums[i][1] / fcount,
                    sums[i][2] / fcount,
                ]

        if iteration > 0:
            max_movement = 0.0
            for new_c, old_c in zip(centroids, prev_centroids):
                movement = _dist_sq(new_c, old_c)
                if movement > max_movement:
                    max_movement = movement
            if max_movement < 0.01:
                break
        prev_centroids = [c[:] for c in centroids]

    new_pixels: List[Tuple[int, int, int, int]] = []
    for (r, g, b, a) in pixels:
        if a == 0:
            new_pixels.append((r, g, b, a))
            continue
        p = [float(r), float(g), float(b)]
        min_dist = float("inf")
        best_c = (r, g, b)
        for c in centroids:
            d = _dist_sq(p, c)
            if d < min_dist:
                min_dist = d
                best_c = (int(round(c[0])), int(round(c[1])), int(round(c[2])))
        new_pixels.append((best_c[0], best_c[1], best_c[2], a))

    out = Image.new("RGBA", rgba.size)
    out.putdata(new_pixels)
    return out


def report_outliers(img: Image.Image, count: int) -> List[Tuple[int, int, int]]:
    rgba = img.convert("RGBA")
    pixels = list(rgba.getdata())
    counts: Dict[Tuple[int, int, int], int] = {}
    total = 0
    for r, g, b, a in pixels:
        if a == 0:
            continue
        total += 1
        key = (r, g, b)
        counts[key] = counts.get(key, 0) + 1
    if total == 0:
        print("No opaque pixels found.")
        return []
    sorted_items = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
    n = max(min(count, len(sorted_items)), 0)
    print(f"Least-frequent {n} colors (of {len(sorted_items)} unique):")
    colors: List[Tuple[int, int, int]] = []
    for color, c in sorted_items[:n]:
        pct = (c / total) * 100.0
        print(f"  #{color[0]:02x}{color[1]:02x}{color[2]:02x}  count={c}  {pct:.4f}%")
        colors.append(color)
    return colors


def report_far_outliers(
    img: Image.Image, count: int, min_pct: float, min_count: int
) -> List[Tuple[int, int, int]]:
    rgba = img.convert("RGBA")
    pixels = list(rgba.getdata())
    counts: Dict[Tuple[int, int, int], int] = {}
    total = 0
    for r, g, b, a in pixels:
        if a == 0:
            continue
        total += 1
        key = (r, g, b)
        counts[key] = counts.get(key, 0) + 1
    if total == 0:
        print("No opaque pixels found.")
        return []

    filtered = []
    for color, c in counts.items():
        pct = (c / total) * 100.0
        if c >= min_count and pct >= min_pct:
            filtered.append((color, c, pct))

    if len(filtered) < 2:
        print("Not enough colors meeting thresholds to compute far outliers.")
        return []

    def dist_sq(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
        dr = a[0] - b[0]
        dg = a[1] - b[1]
        db = a[2] - b[2]
        return float(dr * dr + dg * dg + db * db)

    scored = []
    colors_only = [c for c, _, _ in filtered]
    for color, c, pct in filtered:
        min_d = None
        for other in colors_only:
            if other == color:
                continue
            d = dist_sq(color, other)
            if min_d is None or d < min_d:
                min_d = d
        if min_d is None:
            continue
        scored.append((min_d, color, c, pct))

    scored.sort(key=lambda x: (-x[0], -x[2]))
    n = max(min(count, len(scored)), 0)
    print(f"Farthest {n} colors by nearest-neighbor distance (min_pct={min_pct}, min_count={min_count}):")
    colors: List[Tuple[int, int, int]] = []
    for dist2, color, c, pct in scored[:n]:
        print(
            f"  #{color[0]:02x}{color[1]:02x}{color[2]:02x}  "
            f"count={c}  {pct:.4f}%  nearest_dist={math.sqrt(dist2):.2f}"
        )
        colors.append(color)
    return colors


def compute_profiles(img: Image.Image) -> Tuple[List[float], List[float]]:
    w, h = img.size
    if w < 3 or h < 3:
        raise PixelSnapperError("Image too small (minimum 3x3)")

    col_proj = [0.0] * w
    row_proj = [0.0] * h
    px = img.load()

    def gray(x: int, y: int) -> float:
        r, g, b, a = px[x, y]
        if a == 0:
            return 0.0
        return 0.299 * r + 0.587 * g + 0.114 * b

    for y in range(h):
        for x in range(1, w - 1):
            left = gray(x - 1, y)
            right = gray(x + 1, y)
            grad = abs(right - left)
            col_proj[x] += grad

    for x in range(w):
        for y in range(1, h - 1):
            top = gray(x, y - 1)
            bottom = gray(x, y + 1)
            grad = abs(bottom - top)
            row_proj[y] += grad

    return col_proj, row_proj


def estimate_step_size(profile: List[float], config: Config) -> Optional[float]:
    if not profile:
        return None

    max_val = max(profile)
    if max_val == 0.0:
        return None
    threshold = max_val * config.peak_threshold_multiplier

    peaks: List[int] = []
    for i in range(1, len(profile) - 1):
        if profile[i] > threshold and profile[i] > profile[i - 1] and profile[i] > profile[i + 1]:
            peaks.append(i)

    if len(peaks) < 2:
        return None

    clean_peaks = [peaks[0]]
    for p in peaks[1:]:
        if p - clean_peaks[-1] > (config.peak_distance_filter - 1):
            clean_peaks.append(p)

    if len(clean_peaks) < 2:
        return None

    diffs = [float(b - a) for a, b in zip(clean_peaks, clean_peaks[1:])]
    diffs.sort()
    return diffs[len(diffs) // 2]


def resolve_step_sizes(
    step_x_opt: Optional[float],
    step_y_opt: Optional[float],
    width: int,
    height: int,
    config: Config,
) -> Tuple[float, float]:
    if step_x_opt is not None and step_y_opt is not None:
        sx, sy = step_x_opt, step_y_opt
        ratio = sx / sy if sx > sy else sy / sx
        if ratio > config.max_step_ratio:
            smaller = min(sx, sy)
            return smaller, smaller
        avg = (sx + sy) / 2.0
        return avg, avg
    if step_x_opt is not None:
        return step_x_opt, step_x_opt
    if step_y_opt is not None:
        return step_y_opt, step_y_opt
    fallback_step = max(min(width, height) / float(config.fallback_target_segments), 1.0)
    return fallback_step, fallback_step


def walk(profile: List[float], step_size: float, limit: int, config: Config) -> List[int]:
    if not profile:
        raise PixelSnapperError("Cannot walk on empty profile")

    cuts = [0]
    current_pos = 0.0
    search_window = max(step_size * config.walker_search_window_ratio, config.walker_min_search_window)
    mean_val = sum(profile) / float(len(profile))

    while current_pos < float(limit):
        target = current_pos + step_size
        if target >= float(limit):
            cuts.append(limit)
            break

        start_search = max(int(target - search_window), int(current_pos + 1.0))
        end_search = min(int(target + search_window), limit)
        if end_search <= start_search:
            current_pos = target
            continue

        max_val = -1.0
        max_idx = start_search
        for i in range(start_search, end_search):
            if profile[i] > max_val:
                max_val = profile[i]
                max_idx = i

        if max_val > mean_val * config.walker_strength_threshold:
            cuts.append(max_idx)
            current_pos = float(max_idx)
        else:
            cuts.append(int(target))
            current_pos = target

    return cuts


def sanitize_cuts(cuts: List[int], limit: int) -> List[int]:
    if limit == 0:
        return [0]

    has_zero = False
    has_limit = False
    for i, value in enumerate(cuts):
        if value == 0:
            has_zero = True
        if value >= limit:
            cuts[i] = limit
        if cuts[i] == limit:
            has_limit = True

    if not has_zero:
        cuts.append(0)
    if not has_limit:
        cuts.append(limit)

    cuts = sorted(set(cuts))
    return cuts


def snap_uniform_cuts(
    profile: List[float],
    limit: int,
    target_step: float,
    config: Config,
    min_required: int,
) -> List[int]:
    # Pick grid cuts along one axis based on a profile signal.
    if limit == 0:
        return [0]
    if limit == 1:
        return [0, 1]

    if target_step > 0.0 and math.isfinite(target_step):
        desired_cells = int(round(limit / target_step))
    else:
        desired_cells = 0
    desired_cells = max(desired_cells, max(min_required - 1, 1))
    desired_cells = min(desired_cells, limit)

    cell_width = limit / float(desired_cells)
    search_window = max(cell_width * config.walker_search_window_ratio, config.walker_min_search_window)
    mean_val = sum(profile) / float(len(profile)) if profile else 0.0

    cuts = [0]
    for idx in range(1, desired_cells):
        target = cell_width * idx
        prev = cuts[-1]
        if prev + 1 >= limit:
            break
        start = int(math.floor(target - search_window))
        start = max(start, prev + 1, 0)
        end = int(math.ceil(target + search_window))
        end = min(end, limit - 1)
        if end < start:
            start = prev + 1
            end = start
        best_idx = min(start, len(profile) - 1)
        best_val = -1.0
        for i in range(start, min(end, len(profile) - 1) + 1):
            v = profile[i] if i < len(profile) else 0.0
            if v > best_val:
                best_val = v
                best_idx = i
        strength_threshold = mean_val * config.walker_strength_threshold
        if best_val < strength_threshold:
            fallback_idx = int(round(target))
            if fallback_idx <= prev:
                fallback_idx = prev + 1
            if fallback_idx >= limit:
                fallback_idx = max(limit - 1, prev + 1)
            best_idx = fallback_idx
        cuts.append(best_idx)

    if cuts[-1] != limit:
        cuts.append(limit)
    return sanitize_cuts(cuts, limit)


def snap_uniform_cuts_fixed(
    profile: List[float],
    limit: int,
    desired_cells: int,
    config: Config,
) -> List[int]:
    # Pick grid cuts for a fixed number of cells.
    if limit == 0:
        return [0]
    if limit == 1:
        return [0, 1]

    desired_cells = max(desired_cells, 1)
    desired_cells = min(desired_cells, limit)

    cell_width = limit / float(desired_cells)
    search_window = max(cell_width * config.walker_search_window_ratio, config.walker_min_search_window)
    mean_val = sum(profile) / float(len(profile)) if profile else 0.0

    cuts = [0]
    for idx in range(1, desired_cells):
        target = cell_width * idx
        prev = cuts[-1]
        if prev + 1 >= limit:
            break
        start = int(math.floor(target - search_window))
        start = max(start, prev + 1, 0)
        end = int(math.ceil(target + search_window))
        end = min(end, limit - 1)
        if end < start:
            start = prev + 1
            end = start
        best_idx = min(start, len(profile) - 1)
        best_val = -1.0
        for i in range(start, min(end, len(profile) - 1) + 1):
            v = profile[i] if i < len(profile) else 0.0
            if v > best_val:
                best_val = v
                best_idx = i
        strength_threshold = mean_val * config.walker_strength_threshold
        if best_val < strength_threshold:
            fallback_idx = int(round(target))
            if fallback_idx <= prev:
                fallback_idx = prev + 1
            if fallback_idx >= limit:
                fallback_idx = max(limit - 1, prev + 1)
            best_idx = fallback_idx
        cuts.append(best_idx)

    if cuts[-1] != limit:
        cuts.append(limit)
    return sanitize_cuts(cuts, limit)


def stabilize_cuts(
    profile: List[float],
    cuts: List[int],
    limit: int,
    sibling_cuts: List[int],
    sibling_limit: int,
    config: Config,
) -> List[int]:
    if limit == 0:
        return [0]

    cuts = sanitize_cuts(cuts, limit)
    min_required = max(config.min_cuts_per_axis, 2)
    min_required = min(min_required, limit + 1)

    axis_cells = max(len(cuts) - 1, 0)
    sibling_cells = max(len(sibling_cuts) - 1, 0)
    sibling_has_grid = sibling_limit > 0 and sibling_cells >= max(min_required - 1, 0) and sibling_cells > 0
    steps_skewed = False
    if sibling_has_grid and axis_cells > 0:
        axis_step = limit / float(axis_cells)
        sibling_step = sibling_limit / float(sibling_cells)
        step_ratio = axis_step / sibling_step
        if step_ratio > config.max_step_ratio or step_ratio < 1.0 / config.max_step_ratio:
            steps_skewed = True

    has_enough = len(cuts) >= min_required
    if has_enough and not steps_skewed:
        return cuts

    if sibling_has_grid:
        target_step = sibling_limit / float(sibling_cells)
    elif config.fallback_target_segments > 1:
        target_step = limit / float(config.fallback_target_segments)
    elif axis_cells > 0:
        target_step = limit / float(axis_cells)
    else:
        target_step = float(limit)
    if not math.isfinite(target_step) or target_step <= 0.0:
        target_step = 1.0

    return snap_uniform_cuts(profile, limit, target_step, config, min_required)


def stabilize_both_axes(
    profile_x: List[float],
    profile_y: List[float],
    raw_col_cuts: List[int],
    raw_row_cuts: List[int],
    width: int,
    height: int,
    config: Config,
) -> Tuple[List[int], List[int]]:
    col_cuts_pass1 = stabilize_cuts(profile_x, raw_col_cuts[:], width, raw_row_cuts, height, config)
    row_cuts_pass1 = stabilize_cuts(profile_y, raw_row_cuts[:], height, raw_col_cuts, width, config)

    col_cells = max(len(col_cuts_pass1) - 1, 1)
    row_cells = max(len(row_cuts_pass1) - 1, 1)
    col_step = width / float(col_cells)
    row_step = height / float(row_cells)
    step_ratio = col_step / row_step if col_step > row_step else row_step / col_step

    if step_ratio > config.max_step_ratio:
        target_step = min(col_step, row_step)
        if col_step > target_step * 1.2:
            final_col_cuts = snap_uniform_cuts(
                profile_x, width, target_step, config, config.min_cuts_per_axis
            )
        else:
            final_col_cuts = col_cuts_pass1

        if row_step > target_step * 1.2:
            final_row_cuts = snap_uniform_cuts(
                profile_y, height, target_step, config, config.min_cuts_per_axis
            )
        else:
            final_row_cuts = row_cuts_pass1
        return final_col_cuts, final_row_cuts

    return col_cuts_pass1, row_cuts_pass1


def resample(img: Image.Image, cols: List[int], rows: List[int]) -> Image.Image:
    if len(cols) < 2 or len(rows) < 2:
        raise PixelSnapperError("Insufficient grid cuts for resampling")

    out_w = max(len(cols) - 1, 1)
    out_h = max(len(rows) - 1, 1)
    out = Image.new("RGBA", (out_w, out_h))
    src = img.load()
    out_px = out.load()

    for y_i, (ys, ye) in enumerate(zip(rows[:-1], rows[1:])):
        for x_i, (xs, xe) in enumerate(zip(cols[:-1], cols[1:])):
            if xe <= xs or ye <= ys:
                continue
            counts: Dict[Tuple[int, int, int, int], int] = {}
            for y in range(ys, ye):
                for x in range(xs, xe):
                    if x < img.width and y < img.height:
                        p = src[x, y]
                        counts[p] = counts.get(p, 0) + 1
            if not counts:
                continue
            candidates = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            best_pixel = candidates[0][0]
            out_px[x_i, y_i] = best_pixel

    return out


def process_image_bytes_common(input_bytes: bytes, config: Optional[Config]) -> bytes:
    cfg = config or Config()
    img = Image.open(io.BytesIO(input_bytes))
    width, height = img.size
    validate_image_dimensions(width, height)
    target_size = _fit_to_target(width, height, cfg.target_width, cfg.target_height)

    quantized = quantize_image(img, cfg)
    profile_x, profile_y = compute_profiles(quantized)

    step_x_opt = estimate_step_size(profile_x, cfg)
    step_y_opt = estimate_step_size(profile_y, cfg)
    step_x, step_y = resolve_step_sizes(step_x_opt, step_y_opt, width, height, cfg)

    raw_col_cuts = walk(profile_x, step_x, width, cfg)
    raw_row_cuts = walk(profile_y, step_y, height, cfg)

    col_cuts, row_cuts = stabilize_both_axes(
        profile_x,
        profile_y,
        raw_col_cuts,
        raw_row_cuts,
        width,
        height,
        cfg,
    )

    if cfg.grid_mode == "target" and cfg.target_width > 0 and cfg.target_height > 0:
        target_w, target_h = target_size
        col_cuts = snap_uniform_cuts_fixed(profile_x, width, target_w, cfg)
        row_cuts = snap_uniform_cuts_fixed(profile_y, height, target_h, cfg)

    output_img = resample(quantized, col_cuts, row_cuts)
    if cfg.target_width > 0 and cfg.target_height > 0 and output_img.size != target_size:
        output_img = output_img.resize(target_size, resample=Image.NEAREST)
    out_buf = io.BytesIO()
    output_img.save(out_buf, format="PNG")
    return out_buf.getvalue()


def process_image_bytes_naive(input_bytes: bytes, config: Optional[Config]) -> bytes:
    cfg = config or Config()
    img = Image.open(io.BytesIO(input_bytes))
    if cfg.target_width > 0 and cfg.target_height > 0:
        target_size = _fit_to_target(img.width, img.height, cfg.target_width, cfg.target_height)
        img = img.resize(target_size, resample=Image.NEAREST)
    width, height = img.size
    validate_image_dimensions(width, height)

    quantized = quantize_image(img, cfg)
    out_buf = io.BytesIO()
    quantized.save(out_buf, format="PNG")
    return out_buf.getvalue()


def process_image(config: Config) -> None:
    print(f"Processing: {config.input_path}")
    with open(config.input_path, "rb") as f:
        input_bytes = f.read()
    keep = list(config.keep_colors or [])
    if config.outliers > 0:
        img = Image.open(io.BytesIO(input_bytes))
        keep.extend(report_outliers(img, config.outliers))
    if config.outliers_far > 0:
        img = Image.open(io.BytesIO(input_bytes))
        keep.extend(report_far_outliers(
            img,
            config.outliers_far,
            config.outliers_min_pct,
            config.outliers_min_count,
        ))
    if keep:
        config.keep_colors = list(dict.fromkeys(keep))
    if config.method == "naive":
        output_bytes = process_image_bytes_naive(input_bytes, config)
    else:
        output_bytes = process_image_bytes_common(input_bytes, config)
    with open(config.output_path, "wb") as f:
        f.write(output_bytes)
    print(f"Saved to: {config.output_path}")


def parse_args() -> Config:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path", nargs="?", default=Config.input_path)
    ap.add_argument("output_path", nargs="?", default=Config.output_path)
    ap.add_argument("k_colors", nargs="?", type=int, default=Config.k_colors)
    ap.add_argument("--width", type=int, default=Config.target_width)
    ap.add_argument("--height", type=int, default=Config.target_height)
    ap.add_argument(
        "--method",
        choices=["grid", "naive"],
        default="naive",
        help="grid=infer pixel grid and resample; naive=nearest resize only",
    )
    ap.add_argument(
        "--quantize_method",
        choices=["diverse", "fast", "kmeans"],
        default=Config.quantize_method,
        help="diverse=spread palette; fast=median-cut quantize; kmeans=slow iterative palette",
    )
    ap.add_argument(
        "--keep_colors",
        default=None,
        help="Comma-separated list of hex RGB colors to preserve (e.g., #ff0000,#aa1122)",
    )
    ap.add_argument(
        "--outliers",
        type=int,
        default=0,
        help="Print N least-frequent colors and exit (useful for finding outliers)",
    )
    ap.add_argument(
        "--outliers_far",
        type=int,
        default=0,
        help="Print N farthest colors (by nearest-neighbor distance) and exit",
    )
    ap.add_argument(
        "--outliers_min_pct",
        type=float,
        default=Config.outliers_min_pct,
        help="Minimum percent (0-100) for far outliers",
    )
    ap.add_argument(
        "--outliers_min_count",
        type=int,
        default=Config.outliers_min_count,
        help="Minimum pixel count for far outliers",
    )
    ap.add_argument(
        "--grid_mode",
        choices=["infer", "target"],
        default=Config.grid_mode,
        help="infer=use inferred grid; target=force grid to align with target size",
    )
    args = ap.parse_args()

    cfg = Config(
        input_path=args.input_path,
        output_path=args.output_path,
        k_colors=args.k_colors,
        target_width=args.width,
        target_height=args.height,
        method=args.method,
        grid_mode=args.grid_mode,
        quantize_method=args.quantize_method,
    )
    if args.keep_colors:
        keep_list = []
        for raw in args.keep_colors.split(","):
            raw = raw.strip().lstrip("#")
            if len(raw) != 6:
                raise PixelSnapperError(f"Invalid keep color: {raw}")
            try:
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
            except ValueError as e:
                raise PixelSnapperError(f"Invalid keep color: {raw}") from e
            keep_list.append((r, g, b))
        cfg.keep_colors = keep_list
    if cfg.k_colors <= 0:
        raise PixelSnapperError("k_colors must be greater than 0")
    cfg.outliers = args.outliers  # type: ignore[attr-defined]
    cfg.outliers_far = args.outliers_far  # type: ignore[attr-defined]
    cfg.outliers_min_pct = args.outliers_min_pct  # type: ignore[attr-defined]
    cfg.outliers_min_count = args.outliers_min_count  # type: ignore[attr-defined]
    return cfg


def main() -> None:
    cfg = parse_args()
    process_image(cfg)


if __name__ == "__main__":
    main()
