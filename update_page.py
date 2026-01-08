#!/usr/bin/env python3
"""Copy story_specific_gen outputs and screens.json into pointclickjs-main."""

from __future__ import annotations

import argparse
import os
import shutil


def copy_tree(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst)
    ignore = None
    if os.path.basename(src) == "images":
        ignore = shutil.ignore_patterns("raw")
    shutil.copytree(src, dst, ignore=ignore)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dest",
        default="/home/jwbatey/Documents/coding/pointclickjs-main",
        help="Destination repo path",
    )
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    images_src = os.path.join(here, "story_specific_gen", "images")
    dialogue_src = os.path.join(here, "story_specific_gen", "dialogue")
    screens_src = os.path.join(here, "story_specific", "screens.json")
    scenes_src = os.path.join(here, "story_specific_gen", "scenes.json")
    hotspots_src = os.path.join(here, "story_specific_gen", "hotspots.json")
    music_src = os.path.join(here, "story_specific_gen", "music")

    if not os.path.isdir(images_src):
        raise SystemExit(f"Missing images directory: {images_src}")
    if not os.path.isfile(screens_src):
        raise SystemExit(f"Missing screens.json: {screens_src}")
    if not os.path.isfile(scenes_src):
        raise SystemExit(f"Missing scenes.json: {scenes_src}")
    if not os.path.isfile(hotspots_src):
        raise SystemExit(f"Missing hotspots.json: {hotspots_src}")
    if not os.path.isdir(music_src):
        raise SystemExit(f"Warning: music directory missing: {music_src}")

    dest_root = os.path.abspath(args.dest)
    images_dst = os.path.join(dest_root, "images")
    dialogue_dst = os.path.join(dest_root, "dialogue")
    screens_dst = os.path.join(dest_root, "screens.json")
    scenes_dst = os.path.join(dest_root, "scenes.json")
    hotspots_dst = os.path.join(dest_root, "hotspots.json")
    music_dst = os.path.join(dest_root, "music")

    if not os.path.isdir(dest_root):
        raise SystemExit(f"Destination not found: {dest_root}")

    copy_tree(images_src, images_dst)
    if os.path.isdir(music_src):
        copy_tree(music_src, music_dst)
        print(f"Copied {music_src} -> {music_dst}")
    if os.path.isdir(dialogue_src):
        copy_tree(dialogue_src, dialogue_dst)
        print(f"Copied {dialogue_src} -> {dialogue_dst}")
    shutil.copy2(screens_src, screens_dst)
    shutil.copy2(scenes_src, scenes_dst)
    shutil.copy2(hotspots_src, hotspots_dst)
    print(f"Copied {images_src} -> {images_dst}")
    print(f"Copied {screens_src} -> {screens_dst}")
    print(f"Copied {scenes_src} -> {scenes_dst}")
    print(f"Copied {hotspots_src} -> {hotspots_dst}")


if __name__ == "__main__":
    main()
