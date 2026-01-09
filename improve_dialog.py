#!/usr/bin/env python3
"""
Extract character dialogue lines, ask an LLM to improve them, lint revisions,
and optionally write updates back to scene dialogue files.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from openai import OpenAI
from pathlib import Path


EXPLAINER_HITS = [
    "because",
    "which means",
    "that means",
    "this means",
    "in our framework",
    "in this framework",
    "as a result",
    "in order to",
    "therefore",
    "thus",
    "hence",
]


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def iter_dialogue_paths(base_dir: Path) -> list[Path]:
    candidates = []
    dialogue_dir = base_dir / "dialogue"
    if dialogue_dir.exists():
        candidates.extend(sorted(dialogue_dir.glob("SCN_SCN-*.json")))
    candidates.extend(sorted(base_dir.glob("SCN_SCN-*.json")))
    candidates = [p for p in candidates if "-orig" not in p.name]
    seen = set()
    unique = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def format_line(lines: list[str]) -> str:
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    joined = " ".join(f"({line})" for line in lines)
    return f"(multiple) {joined}"


def build_nodes(data: dict, character_id: str, scene_name: str) -> list[dict]:
    # Extract each line spoken by the target character with surrounding context.
    results = []
    for graph in data.get("dialogueGraphs", []):
        nodes = graph.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        node_by_id = {
            node.get("id"): node
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        }
        for idx, node in enumerate(nodes):
            if node.get("speakerId") != character_id:
                continue
            line = node.get("text")
            if not isinstance(line, str):
                continue
            prior_lines = []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                if n.get("next") == node.get("id"):
                    text = n.get("text")
                    if isinstance(text, str) and text.strip():
                        prior_lines.append(text.strip())
                choices = n.get("choices")
                if isinstance(choices, list):
                    for choice in choices:
                        if not isinstance(choice, dict):
                            continue
                        if choice.get("next") != node.get("id"):
                            continue
                        choice_text = choice.get("text")
                        if isinstance(choice_text, str) and choice_text.strip():
                            prior_lines.append(choice_text.strip())
            next_lines = []
            next_id = node.get("next")
            if isinstance(next_id, str):
                next_node = node_by_id.get(next_id)
                if isinstance(next_node, dict):
                    next_text = next_node.get("text")
                    if isinstance(next_text, str) and next_text.strip():
                        next_lines = [next_text.strip()]
            results.append(
                {
                    "scene": scene_name,
                    "id": node.get("id", ""),
                    "priorLine": format_line(prior_lines),
                    "lineToEdit": line.strip(),
                    "nextLine": format_line(next_lines),
                    "revisedLine": "",
                    "changeSummary": "",
                }
            )
    return results


def count_explainer_hits(text: str) -> int:
    lower = text.lower()
    return sum(lower.count(phrase) for phrase in EXPLAINER_HITS)


def max_beat_words(text: str) -> int:
    parts = re.split(r"\(pause\)", text, flags=re.IGNORECASE)
    max_words = 0
    for part in parts:
        words = [w for w in part.strip().split() if w]
        max_words = max(max_words, len(words))
    return max_words


def needs_lint(text: str, *, max_beats: int, max_commas: int, max_explainer: int) -> list[str]:
    # Return any lint rule violations for the line.
    issues = []
    beats = max_beat_words(text)
    if beats > max_beats:
        issues.append(f"max_beat_words={max_beats} (found {beats})")
    commas = text.count(",")
    if commas > max_commas:
        issues.append(f"max_commas={max_commas} (found {commas})")
    explainer_hits = count_explainer_hits(text)
    if explainer_hits > max_explainer:
        issues.append(f"max_explainer_hits={max_explainer} (found {explainer_hits})")
    return issues


def lint_line_with_llm(
    *,
    line: str,
    issues: list[str],
    max_beats: int,
    max_commas: int,
    max_explainer: int,
    model: str,
    timeout: int,
) -> dict:
    # Ask the LLM to repair only the issues listed.
    prompt = (
        "You are a script editor. Revise to meet these constraints:\n"
        f"- max_beat_words: {max_beats} (beat = text between '(pause)')\n"
        f"- max_commas: {max_commas}\n"
        f"- max_explainer_hits: {max_explainer} (counts words/phrases like 'because', 'which means', "
        "'in our framework', 'that means', etc.)\n\n"
        "Do not change meaning or facts. Keep the tone and cadence. "
        "Return JSON with keys revisedLine and changeSummary only.\n\n"
        f"Issues: {', '.join(issues)}\n\n"
        f"Line:\n{line}"
    )
    # print(f"LINT: Revising line '{line}' ({', '.join(issues)})")
    client = OpenAI(timeout=timeout)
    try:
        rsp = client.responses.create(
            model=model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": {"type": "json_object"}},
        )
        return json.loads(rsp.output_text.strip())
    except Exception as exc:
        print(f"Warning: lint request failed; skipping line. ({exc})")
        return {}


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
    line = f"\r- Linting [{bar}] {done}/{total} {format_eta(eta)}"
    sys.stdout.write(line)
    sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--c", required=True, help="Character ID to extract")
    ap.add_argument("--timeout", type=int, default=30, help="Timeout (s) for GPT requests")
    ap.add_argument(
        "--debug",
        type=int,
        default=0,
        help="Debug step (1 writes prompt to debug.log and exits; 2 saves response to debug.log)",
    )
    args = ap.parse_args()

    character_id = args.c.strip()
    if not character_id:
        raise SystemExit("Character ID cannot be empty.")
    if character_id == "NARRATOR":
        max_beats = 16
        max_commas = 2
        max_explainer = 4
    else:
        max_beats = 8
        max_commas = 1
        max_explainer = 2

    base_dir = Path("story_specific_gen")
    paths = iter_dialogue_paths(base_dir)
    if not paths:
        raise SystemExit(f"No dialogue files found under {base_dir}")

    all_nodes = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"Warning: invalid JSON in {path}")
            continue
        scene_name = path.name
        print(scene_name)
        all_nodes.extend(build_nodes(data, character_id, scene_name))

    out_dir = base_dir / "dialogue"
    if not out_dir.exists():
        out_dir = base_dir
    out_path = out_dir / f"improve_char-{safe_name(character_id)}.json"
    created_new = not out_path.exists()
    if not created_new:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        print(f"- Using existing {out_path}")
        updated_payload = payload
    else:
        payload = {"nodes": all_nodes}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=True)
        print(f"- Wrote {out_path}")

    rules_path = Path("story_specific") / "improve_dialog.md"
    if not rules_path.exists():
        raise SystemExit(f"Missing rules file: {rules_path}")
    rules_text = rules_path.read_text(encoding="utf-8").strip()
    format_hint = {
        "nodes": [
            {
                "scene": "SCN_SCN-001.json",
                "id": "NODE_ID",
                "priorLine": "",
                "lineToEdit": "",
                "nextLine": "",
                "revisedLine": "",
                "changeSummary": "",
            }
        ]
    }
    editor_prompt = (
        "You are a script editor. Edit the following lines based on these rules.\n\n"
        + rules_text
        + "\n\nReturn in this format:\n"
        + json.dumps(format_hint, indent=2, ensure_ascii=True)
        + "\n\nINPUT:\n"
        + json.dumps(payload, indent=2, ensure_ascii=True)
    )
    if args.debug == 1:
        with open("debug.log", "w", encoding="utf-8") as f:
            f.write(editor_prompt)
        print("Debug step 1 complete: wrote prompt to debug.log")
        return
    if created_new:
        print("- Requesting GPT-5.2 edits...")
        client = OpenAI(timeout=args.timeout*10)
        try:
            rsp = client.responses.create(
                model="gpt-5.2",
                input=[{"role": "user", "content": [{"type": "input_text", "text": editor_prompt}]}],
                reasoning={"effort": "high"},
                text={"format": {"type": "json_object"}},
            )
            updated = rsp.output_text.strip()
        except Exception as exc:
            print(f"Warning: initial edit request failed; skipping edits. ({exc})")
            updated = None
        if args.debug == 2:
            with open("debug.log", "w", encoding="utf-8") as f:
                f.write(updated or "")
            print("Debug step 2 complete: wrote response to debug.log")
        if updated:
            try:
                updated_payload = json.loads(updated)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSON from model: {exc}") from exc
            if not isinstance(updated_payload, dict) or "nodes" not in updated_payload:
                raise SystemExit("Model output missing 'nodes' list.")
            if not isinstance(updated_payload.get("nodes"), list):
                raise SystemExit("Model output 'nodes' is not a list.")

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(updated_payload, f, indent=2, ensure_ascii=True)
            print(f"- Updated {out_path} (pre-lint)")
        else:
            updated_payload = payload

    # Iterative linting: batch-fix only the lines that fail rules.
    max_rounds = 3
    for round_idx in range(1, max_rounds + 1):
        lint_queue = []
        total_items = 0
        for node in updated_payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            revised = node.get("revisedLine")
            if not isinstance(revised, str) or not revised.strip():
                revised = node.get("lineToEdit")
            if not isinstance(revised, str) or not revised.strip():
                continue
            total_items += 1
            issues = needs_lint(
                revised,
                max_beats=max_beats,
                max_commas=max_commas,
                max_explainer=max_explainer,
            )
            if issues:
                lint_queue.append((node, revised, issues))

        failed_items = len(lint_queue)
        started = time.time()
        render_progress(0, failed_items, started)
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    lint_line_with_llm,
                    line=revised,
                    issues=issues,
                    max_beats=max_beats,
                    max_commas=max_commas,
                    max_explainer=max_explainer,
                    model="gpt-5.2",
                    timeout=args.timeout,
                ): (node, revised, issues)
                for node, revised, issues in lint_queue
            }
            for future in concurrent.futures.as_completed(futures):
                linted = future.result()
                node, _revised, _issues = futures[future]
                new_line = linted.get("revisedLine")
                summary = linted.get("changeSummary")
                if isinstance(new_line, str) and new_line.strip():
                    node["revisedLine"] = new_line.strip()
                if isinstance(summary, str) and summary.strip():
                    existing = node.get("changeSummary", "")
                    if isinstance(existing, str) and existing.strip():
                        node["changeSummary"] = f"{existing} | {summary.strip()}"
                    else:
                        node["changeSummary"] = summary.strip()
                completed += 1
                render_progress(completed, failed_items, started)
        if failed_items:
            sys.stdout.write("\n")

        print(f"- Linted lines sent: {failed_items}")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(updated_payload, f, indent=2, ensure_ascii=True)
        print(f"- Updated {out_path} (lint round {round_idx})")

        if total_items == 0:
            break
        if failed_items / total_items <= 0.10:
            break

    updates_by_scene = {}
    for node in updated_payload.get("nodes", []):
        if not isinstance(node, dict):
            continue
        scene = node.get("scene")
        node_id = node.get("id")
        revised = node.get("revisedLine")
        if not isinstance(scene, str) or not isinstance(node_id, str):
            continue
        if not isinstance(revised, str) or not revised.strip():
            print(f"Warning: empty revisedLine for scene {scene} node {node_id}; skipping.")
            continue
        updates_by_scene.setdefault(scene, {})[node_id] = revised.strip()

    dialogue_dir = Path("story_specific_gen") / "dialogue"
    for scene_name, updates in updates_by_scene.items():
        scene_path = dialogue_dir / scene_name
        if not scene_path.exists():
            continue
        backup_path = scene_path.with_name(scene_path.stem + "-orig.json")
        if not backup_path.exists():
            backup_path.write_text(scene_path.read_text(encoding="utf-8"), encoding="utf-8")
        data = json.loads(scene_path.read_text(encoding="utf-8"))
        changed = False
        for graph in data.get("dialogueGraphs", []):
            for node in graph.get("nodes", []):
                node_id = node.get("id")
                if node_id in updates:
                    node["text"] = updates[node_id]
                    changed = True
        if changed:
            scene_path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
            print(f"- Updated {scene_path}")


if __name__ == "__main__":
    main()
