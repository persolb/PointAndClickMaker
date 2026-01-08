#!/usr/bin/env python3
"""
Collect dialogue lines per character and emit a debug markdown file per speaker.
"""
import json
import os
import re
from pathlib import Path


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def load_dialogue_files(dialogue_dir: Path) -> list[Path]:
    return sorted(
        p for p in dialogue_dir.glob("*.json") if p.name != "manifest.json"
    )


def extract_lines(dialogue_path: Path) -> list[dict]:
    # Flatten all dialogue nodes into a simple speaker/text list.
    data = json.loads(dialogue_path.read_text(encoding="utf-8"))
    lines = []
    for graph in data.get("dialogueGraphs", []):
        graph_id = graph.get("id")
        scene_id = graph.get("sceneId")
        for node in graph.get("nodes", []):
            speaker = node.get("speakerId")
            text = node.get("text")
            if not speaker or not text:
                continue
            lines.append(
                {
                    "speakerId": speaker,
                    "text": text,
                    "sceneId": scene_id,
                    "graphId": graph_id,
                    "nodeId": node.get("id"),
                }
            )
    return lines


def main() -> None:
    dialogue_dir = Path("story_specific_gen") / "dialogue"
    if not dialogue_dir.exists():
        raise SystemExit(f"Missing dialogue folder: {dialogue_dir}")

    character_lines: dict[str, list[dict]] = {}
    for path in load_dialogue_files(dialogue_dir):
        for line in extract_lines(path):
            character_lines.setdefault(line["speakerId"], []).append(line)

    if not character_lines:
        print("No dialogue lines found.")
        return

    for speaker_id, lines in character_lines.items():
        safe_id = safe_name(speaker_id)
        out_path = dialogue_dir / f"debug-{safe_id}.md"
        with out_path.open("w", encoding="utf-8") as f:
            f.write(f"# {speaker_id}\n\n")
            for item in lines:
                scene = item.get("sceneId") or "UNKNOWN_SCENE"
                graph = item.get("graphId") or "UNKNOWN_GRAPH"
                node = item.get("nodeId") or "UNKNOWN_NODE"
                text = item.get("text", "").strip()
                f.write(f"- {scene} / {graph} / {node}\n")
                for line in text.splitlines():
                    f.write(f"  {line}\n")
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
