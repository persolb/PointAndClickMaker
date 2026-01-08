#!/usr/bin/env python3
"""
Generate in-progress dialogue edits via LLM, merge them, resolve conflicts,
and apply the resulting changes to dialogue/scenes/hotspots JSON.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - runtime dependency
    OpenAI = None

SCENES_PATH = Path("story_specific_gen/scenes.json")
DIALOGUE_DIR = Path("story_specific_gen/dialogue")
IN_PROGRESS_DIR = Path("story_specific_gen/dialogue/in_progress")
HOTSPOTS_PATH = Path("story_specific_gen/hotspots.json")
IN_PROGRESS_HOTSPOTS_PATH = Path("story_specific_gen/dialogue/in_progress/hotspots.json")
SCENES_SCHEMA_PATH = Path("templates/scenes.schema.json")
DEBUG_LOG_PATH = Path("story_specific_gen/dialogue/in_progress/debug.log")


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        print(f"Missing file: {path}", file=sys.stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in {path}: {exc}", file=sys.stderr)
        return None


def extract_json(text: str):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def build_prompt(scene, scene_dialogue, hotspots):
    scene_id = scene.get("id")

    instruction = (
        "For this point and click game, verify that use/look/etc are not embedded into the enter dialogue if they can instead "
        "be tied to clicking a hotspot on the screen. Return ONLY valid JSON with this shape: "
        "{fromScene, items:[{hotspot, destScene, reason}]}. "
        "Use destScene=\"?\" if no existing hotspot scene is found. "
        "If no changes are needed, return items as an empty array. "
        "Return ONLY valid JSON with no extra text."
    )

    prompt = {
        "instruction": instruction,
        "fromScene": scene_id,
        "dialogue": scene_dialogue,
        "hotspots": hotspots,
    }

    return json.dumps(prompt, indent=2, sort_keys=True)


def build_diff_prompt(
    scene_id,
    item,
    current_dialogue,
    hotspots,
    dest_dialogue,
    dest_scene_id,
    scenes_schema,
):
    instruction = (
        "You are updating dialogue scene JSON files based on the hotspot review. "
        "Return ONLY valid JSON with this shape: "
        "{diffs:[{sceneId, ops:[{op, ...}], createScene?}...]}. "
        "Allowed ops: "
        "remove_node(nodeId), remove_choice(nodeId, choiceId), "
        "add_node(node), add_choice(nodeId, choice), "
        "update_node(node), update_choice(nodeId, choice), "
        "set_start(startNodeId). "
        "If a suggested scene is missing, include createScene with a full dialogue JSON object "
        "containing dialogueGraphs (typically a single graph). "
        "Use the provided fromScene/destScene dialogue and hotspot list to determine what nodes/choices to move. "
        "Any node 'next' links must point to a node within the same scene file. "
        "Best choice: move everything related to the hotspot into the target scene. "
        "This is iterative: if moving a node requires moving its next target, then that moved target must also move "
        "to the same file, and so on. Move or duplicate the full chain into the same target scene, "
        "or remove/adjust 'next' links that are no longer needed. "
        "If nodes are moved to a new scene and are not needed in the old scene, delete them from the old scene. "
        "Return ONLY JSON with no extra text."
    )

    payload = {
        "instruction": instruction,
        "sceneSchema": scenes_schema,
        "fromSceneId": scene_id,
        "fromDialogue": current_dialogue,
        "hotspots": hotspots,
        "reviewItem": item,
        "destSceneId": dest_scene_id,
        "destDialogue": dest_dialogue,
    }

    return json.dumps(payload, indent=2, sort_keys=True)


def classify_op(op_type):
    if op_type in ("remove_node", "remove_choice"):
        return "remove"
    if op_type in ("add_node", "add_choice"):
        return "add"
    return "modify"


def append_scene_changes(scene_id, changes):
    out_path = IN_PROGRESS_DIR / f"{scene_id}.json"
    with out_path.open("a", encoding="utf-8") as handle:
        if out_path.stat().st_size > 0:
            handle.write("\n")
        json.dump(changes, handle, indent=2, sort_keys=True)


def append_leave_normalizations(scene_id, dialogue_obj):
    graphs = dialogue_obj.get("dialogueGraphs", [])
    if not isinstance(graphs, list) or not graphs:
        return
    nodes = graphs[0].get("nodes", [])
    if not isinstance(nodes, list):
        return
    for node in nodes:
        choices = node.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            text = choice.get("text")
            if not isinstance(text, str):
                continue
            if text.lstrip().lower().startswith("leave:") and text != "Leave":
                changes = {
                    "fromScene": scene_id,
                    "hotspot": None,
                    "destScene": scene_id,
                    "changes": [
                        {
                            "action": "modify",
                            "op": "update_choice_text",
                            "details": {
                                "nodeId": node.get("id"),
                                "choiceId": choice.get("id"),
                                "text": "Leave",
                            },
                        }
                    ],
                }
                append_scene_changes(scene_id, changes)


def update_hotspot_scene(hotspots_data, hotspot_id, dest_scene_id):
    updated = False
    for screen in hotspots_data.get("hotspotsByScreen", []):
        for hotspot in screen.get("hotspots", []):
            if hotspot.get("id") != hotspot_id:
                continue
            for state in hotspot.get("states", []):
                for interaction in state.get("interactions", []):
                    if "sceneId" not in interaction:
                        interaction["sceneId"] = dest_scene_id
                        updated = True
            return updated
    return updated


def parse_appended_json(path: Path):
    # Files are append-only JSON chunks; parse them sequentially.
    decoder = json.JSONDecoder()
    text = path.read_text()
    idx = 0
    items = []
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        obj, next_idx = decoder.raw_decode(text, idx)
        items.append(obj)
        idx = next_idx
    return items


def review_changes(scene_id):
    # Merge all appended change blocks into a single -merged.json file.
    path = IN_PROGRESS_DIR / f"{scene_id}.json"
    if not path.exists():
        return 0
    entries = parse_appended_json(path)
    merged_changes = []
    seen_ids = set()
    duplicate_ids = 0
    for entry in entries:
        for change in entry.get("changes", []):
            details = change.get("details", {})
            ids = []
            if isinstance(details, dict):
                op = change.get("op")
                if op == "update_choice":
                    node_id = details.get("nodeId")
                    choice = details.get("choice")
                    choice_id = choice.get("id") if isinstance(choice, dict) else None
                    if node_id and choice_id:
                        ids.append(f"{node_id}-{choice_id}")
                else:
                    if "nodeId" in details:
                        ids.append(details.get("nodeId"))
                    if "choiceId" in details:
                        ids.append(details.get("choiceId"))
                    node = details.get("node")
                    if isinstance(node, dict) and "id" in node:
                        ids.append(node.get("id"))
                    choice = details.get("choice")
                    if isinstance(choice, dict) and "id" in choice:
                        ids.append(choice.get("id"))
            for ident in ids:
                if not ident:
                    continue
                if ident in seen_ids:
                    duplicate_ids += 1
                    # print(f"duplicate_id: {ident} in {scene_id}")
                else:
                    seen_ids.add(ident)
            merged_changes.append(change)

    merged_out = {
        "sceneId": scene_id,
        "changes": merged_changes,
    }
    merged_path = IN_PROGRESS_DIR / f"{scene_id}-merged.json"
    with merged_path.open("w", encoding="utf-8") as handle:
        json.dump(merged_out, handle, indent=2, sort_keys=True)

    return duplicate_ids


def resolve_change_conflicts(scene_id):
    # Detect conflicts across merged changes and mark non-removed changes as ignored.
    path = IN_PROGRESS_DIR / f"{scene_id}-merged.json"
    merged = load_json(path)
    if not merged:
        return
    changes = merged.get("changes", [])
    if not isinstance(changes, list):
        return
    nodes = []
    node_removals = set()
    node_updates = set()
    node_adds = set()
    choice_updates = {}
    choice_removals = {}
    for change in changes:
        details = change.get("details", {})
        if not isinstance(details, dict):
            continue
        node_id = details.get("nodeId")
        op = change.get("op") or details.get("op")
        if op == "remove_node" and node_id:
            node_removals.add(node_id)
        elif op == "update_node" and node_id:
            node_updates.add(node_id)
        elif op == "add_node" and node_id:
            node_adds.add(node_id)
        elif op == "update_choice":
            choice = details.get("choice", {}) if isinstance(details.get("choice"), dict) else {}
            choice_id = choice.get("id") or details.get("choiceId")
            if node_id and choice_id:
                choice_updates.setdefault((node_id, choice_id), []).append(change)
        elif op == "remove_choice":
            choice_id = details.get("choiceId")
            if node_id and choice_id:
                choice_removals.setdefault((node_id, choice_id), []).append(change)
        if node_id:
            nodes.append({"nodeId": node_id, "details": details})
    nodes_sorted = sorted(nodes, key=lambda n: n.get("nodeId", ""))
    conflicts = []
    change_action_updates = 0
    # Build a base node/choice index from the current dialogue file.
    base_nodes = {}
    base_choices = {}
    base_dialogue = load_json(DIALOGUE_DIR / f"SCN_{scene_id}.json")
    if base_dialogue:
        graphs = base_dialogue.get("dialogueGraphs", [])
        if isinstance(graphs, list):
            for graph in graphs:
                for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
                    if not isinstance(node, dict):
                        continue
                    node_id = node.get("id")
                    if not isinstance(node_id, str):
                        continue
                    base_nodes[node_id] = node
                    choices = node.get("choices")
                    if isinstance(choices, list):
                        for choice in choices:
                            if isinstance(choice, dict):
                                choice_id = choice.get("id")
                                if isinstance(choice_id, str):
                                    base_choices.setdefault(node_id, set()).add(choice_id)
    for node_id in sorted(node_removals):
        if node_id in node_updates or node_id in node_adds:
            conflicts.append(
                {
                    "type": "node_remove_vs_update",
                    "nodeId": node_id,
                    "updates": node_id in node_updates,
                    "adds": node_id in node_adds,
                }
            )
        choice_conflicts = [
            f"{choice_id}"
            for (n_id, choice_id) in choice_updates.keys() | choice_removals.keys()
            if n_id == node_id
        ]
        if choice_conflicts:
            conflicts.append(
                {
                    "type": "node_remove_vs_choice_change",
                    "nodeId": node_id,
                    "choiceIds": sorted(set(choice_conflicts)),
                }
            )

    for key in sorted(choice_updates.keys() & choice_removals.keys()):
        node_id, choice_id = key
        conflicts.append(
            {
                "type": "choice_remove_vs_update",
                "nodeId": node_id,
                "choiceId": choice_id,
            }
        )

    for (node_id, choice_id), updates in sorted(choice_updates.items()):
        if len(updates) > 1:
            texts = []
            for idx, change in enumerate(updates):
                choice = change.get("details", {}).get("choice", {})
                if isinstance(choice, dict):
                    text = choice.get("text")
                    if isinstance(text, str):
                        texts.append(text)
                if idx == 0 and change.get("action") != "remove":
                    change["action"] = "ignore-duplicate_choice_update"
                    change_action_updates += 1
            if texts:
                if len(set(texts)) == 1:
                    conflict_type = "duplicate_choice_updates_same_text"
                else:
                    conflict_type = "duplicate_choice_updates_diff_text"
                conflicts.append(
                    {
                        "type": conflict_type,
                        "nodeId": node_id,
                        "choiceId": choice_id,
                        "texts": texts,
                    }
                )

    removed_nodes = set(node_removals)
    # Conflicts that involve updates pointing at removed nodes.
    for change in changes:
        details = change.get("details", {})
        if not isinstance(details, dict):
            continue
        op = change.get("op") or details.get("op")
        node_id = details.get("nodeId")
        if not isinstance(node_id, str):
            node_id = None
        if node_id in removed_nodes:
            if op in {"update_node", "add_node"} and change.get("action") != "remove":
                change["action"] = "ignore-node_remove_vs_update"
                change_action_updates += 1
            if op == "update_choice" and change.get("action") != "remove":
                change["action"] = "ignore-node_remove_vs_choice_change"
                change_action_updates += 1
        if op == "update_choice":
            choice = details.get("choice", {}) if isinstance(details.get("choice"), dict) else {}
            next_id = choice.get("next")
            if isinstance(next_id, str) and next_id in removed_nodes:
                conflicts.append(
                    {
                        "type": "choice_updated_to_removed_node",
                        "nodeId": node_id,
                        "choiceId": choice.get("id"),
                        "next": next_id,
                    }
                )
                if change.get("action") != "remove":
                    change["action"] = "ignore-choice_updated_to_removed_node"
                    change_action_updates += 1
        if op == "update_node":
            node = details.get("node", {}) if isinstance(details.get("node"), dict) else {}
            next_id = node.get("next")
            if isinstance(next_id, str) and next_id in removed_nodes:
                conflicts.append(
                    {
                        "type": "node_updated_to_removed_node",
                        "nodeId": node.get("id") or node_id,
                        "next": next_id,
                    }
                )
                if change.get("action") != "remove":
                    change["action"] = "ignore-node_updated_to_removed_node"
                    change_action_updates += 1

        if op in {"update_node", "remove_node", "update_choice", "remove_choice"}:
            target_node = node_id
            if op == "update_node":
                node = details.get("node", {}) if isinstance(details.get("node"), dict) else {}
                if isinstance(node.get("id"), str):
                    target_node = node.get("id")
            if target_node and target_node not in base_nodes and target_node not in node_adds:
                conflicts.append(
                    {
                        "type": "change_to_missing_node",
                        "nodeId": target_node,
                        "op": op,
                    }
                )
        if op in {"update_choice", "remove_choice"} and node_id and node_id in base_nodes:
            choice_id = details.get("choiceId")
            if op == "update_choice":
                choice = details.get("choice", {}) if isinstance(details.get("choice"), dict) else {}
                if isinstance(choice.get("id"), str):
                    choice_id = choice.get("id")
            if isinstance(choice_id, str):
                if node_id not in node_adds:
                    if choice_id not in base_choices.get(node_id, set()):
                        conflicts.append(
                            {
                                "type": "change_to_missing_choice",
                                "nodeId": node_id,
                                "choiceId": choice_id,
                                "op": op,
                            }
                        )

    for node_id, node in sorted(base_nodes.items()):
        if node_id in removed_nodes or node_id in node_updates:
            continue
        next_id = node.get("next")
        if isinstance(next_id, str) and next_id in removed_nodes:
            conflicts.append(
                {
                    "type": "existing_node_points_to_removed_node",
                    "nodeId": node_id,
                    "next": next_id,
                }
            )
        choices = node.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                next_id = choice.get("next")
                if isinstance(next_id, str) and next_id in removed_nodes:
                    conflicts.append(
                        {
                            "type": "existing_choice_points_to_removed_node",
                            "nodeId": node_id,
                            "choiceId": choice.get("id"),
                            "next": next_id,
                        }
                    )

    if change_action_updates:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, sort_keys=True)


def apply_merged_changes(scene_id):
    # Apply a merged change list to a single dialogue file.
    merged_path = IN_PROGRESS_DIR / f"{scene_id}-merged.json"
    merged = load_json(merged_path)
    if not merged:
        return
    changes = merged.get("changes", [])
    if not isinstance(changes, list):
        return

    dialogue_path = DIALOGUE_DIR / f"SCN_{scene_id}.json"
    dialogue = load_json(dialogue_path)
    # Use create_scene payload to seed new dialogue files.
    create_scene_payload = None
    for change in changes:
        if not isinstance(change, dict):
            continue
        op = change.get("op") or change.get("details", {}).get("op")
        if op == "create_scene" and isinstance(change.get("createScene"), dict):
            create_scene_payload = change.get("createScene")
            break
    if create_scene_payload:
        replace_dialogue = False
        if not dialogue:
            replace_dialogue = True
        elif isinstance(dialogue, dict):
            graphs = dialogue.get("dialogueGraphs")
            if not isinstance(graphs, list) or not graphs:
                replace_dialogue = True
            else:
                has_nodes = any(
                    isinstance(graph, dict) and graph.get("nodes")
                    for graph in graphs
                )
                has_start = any(
                    isinstance(graph, dict) and graph.get("startNodeId")
                    for graph in graphs
                )
                if not has_nodes and not has_start:
                    replace_dialogue = True
        if replace_dialogue:
            dialogue = create_scene_payload
    if not dialogue:
        dialogue = {
            "dialogueGraphs": [
                {
                    "id": f"GRAPH_{scene_id}",
                    "sceneId": scene_id,
                    "startNodeId": None,
                    "nodes": [],
                }
            ]
        }
    graphs = dialogue.get("dialogueGraphs", [])
    if not isinstance(graphs, list):
        return

    nodes_by_id = {}
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        for node in graph.get("nodes", []) or []:
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                nodes_by_id[node["id"]] = node

    def ensure_choice_list(node):
        choices = node.get("choices")
        if not isinstance(choices, list):
            node["choices"] = []
        return node["choices"]

    def find_choice_index(choices, choice_id):
        for idx, choice in enumerate(choices):
            if isinstance(choice, dict) and choice.get("id") == choice_id:
                return idx
        return None

    for change in changes:
        if not isinstance(change, dict):
            continue
        action = change.get("action")
        if isinstance(action, str) and action.startswith("ignore-"):
            continue
        op = change.get("op") or details.get("op")
        details = change.get("details", {})
        if not isinstance(details, dict):
            continue

        if op == "add_node":
            node = details.get("node")
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                if node["id"] not in nodes_by_id:
                    target_graph = None
                    for graph in graphs:
                        if isinstance(graph, dict):
                            target_graph = graph
                            break
                    if target_graph is not None:
                        target_graph.setdefault("nodes", [])
                        target_graph["nodes"].append(node)
                        nodes_by_id[node["id"]] = node
            continue

        if op == "remove_node":
            node_id = details.get("nodeId")
            if isinstance(node_id, str):
                for graph in graphs:
                    if not isinstance(graph, dict):
                        continue
                    nodes = graph.get("nodes")
                    if not isinstance(nodes, list):
                        continue
                    graph["nodes"] = [
                        n for n in nodes if not (isinstance(n, dict) and n.get("id") == node_id)
                    ]
                nodes_by_id.pop(node_id, None)
            continue

        if op == "update_node":
            node = details.get("node")
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                node_id = node["id"]
                existing = nodes_by_id.get(node_id)
                if isinstance(existing, dict):
                    existing.update(node)
                else:
                    # For new scenes, missing updates are treated as adds.
                    target_graph = None
                    for graph in graphs:
                        if isinstance(graph, dict):
                            target_graph = graph
                            break
                    if target_graph is not None:
                        target_graph.setdefault("nodes", [])
                        target_graph["nodes"].append(node)
                        nodes_by_id[node_id] = node
            continue

        if op == "update_choice":
            node_id = details.get("nodeId")
            choice = details.get("choice")
            if not isinstance(node_id, str) or not isinstance(choice, dict):
                continue
            choice_id = choice.get("id")
            if not isinstance(choice_id, str):
                continue
            node = nodes_by_id.get(node_id)
            if not isinstance(node, dict):
                continue
            choices = ensure_choice_list(node)
            idx = find_choice_index(choices, choice_id)
            if idx is None:
                choices.append(choice)
            else:
                existing = choices[idx]
                if isinstance(existing, dict):
                    existing.update(choice)
                else:
                    choices[idx] = choice
            continue

        if op == "remove_choice":
            node_id = details.get("nodeId")
            choice_id = details.get("choiceId")
            if not isinstance(node_id, str) or not isinstance(choice_id, str):
                continue
            node = nodes_by_id.get(node_id)
            if not isinstance(node, dict):
                continue
            choices = node.get("choices")
            if not isinstance(choices, list):
                continue
            node["choices"] = [
                c for c in choices if not (isinstance(c, dict) and c.get("id") == choice_id)
            ]
            continue

        if op == "set_start":
            start_id = details.get("startNodeId")
            if isinstance(start_id, str):
                for graph in graphs:
                    if isinstance(graph, dict):
                        graph["startNodeId"] = start_id
                        break

    with dialogue_path.open("w", encoding="utf-8") as handle:
        json.dump(dialogue, handle, indent=2, ensure_ascii=True)

    source_path = IN_PROGRESS_DIR / f"{scene_id}.json"
    try:
        merged_path.unlink()
    except OSError:
        pass
    try:
        source_path.unlink()
    except OSError:
        pass


def backup_old(scene_ids):
    # Snapshot existing dialogue/scenes/hotspots and merged files.
    stamp = time.strftime("%y%m%d-%H%M%S")
    backup_dir = DIALOGUE_DIR / f"BACKUP-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for scene_id in sorted(scene_ids):
        src = DIALOGUE_DIR / f"SCN_{scene_id}.json"
        if src.exists():
            shutil.copy2(src, backup_dir / src.name)
        merged = IN_PROGRESS_DIR / f"{scene_id}-merged.json"
        if merged.exists():
            shutil.copy2(merged, backup_dir / merged.name)
    if HOTSPOTS_PATH.exists():
        shutil.copy2(HOTSPOTS_PATH, backup_dir / HOTSPOTS_PATH.name)
    if SCENES_PATH.exists():
        shutil.copy2(SCENES_PATH, backup_dir / SCENES_PATH.name)


def apply_new_scenes():
    # Append any newly discovered scenes to scenes.json.
    new_scenes_path = IN_PROGRESS_DIR / "new_scenes.json"
    if not new_scenes_path.exists():
        return
    payload = load_json(new_scenes_path)
    if not payload:
        return
    new_scenes = payload.get("scenes", [])
    if not isinstance(new_scenes, list):
        return
    scenes_doc = load_json(SCENES_PATH)
    if not scenes_doc:
        return
    scenes_list = scenes_doc.get("scenes")
    if not isinstance(scenes_list, list):
        return
    existing_ids = {
        scene.get("id")
        for scene in scenes_list
        if isinstance(scene, dict) and isinstance(scene.get("id"), str)
    }
    for scene in new_scenes:
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or scene_id in existing_ids:
            continue
        if "sceneDescription" not in scene:
            scene["sceneDescription"] = ""
        scenes_list.append(scene)
        existing_ids.add(scene_id)
    with SCENES_PATH.open("w", encoding="utf-8") as handle:
        json.dump(scenes_doc, handle, indent=2, ensure_ascii=True)


def rebuild_new_scenes_json():
    # Rebuild new_scenes.json from current in-progress files every run.
    scenes_doc = load_json(SCENES_PATH)
    if not scenes_doc:
        return
    scenes_list = scenes_doc.get("scenes")
    if not isinstance(scenes_list, list):
        return
    screen_id_map = {
        scene.get("id"): scene.get("screenId")
        for scene in scenes_list
        if isinstance(scene, dict) and isinstance(scene.get("id"), str)
    }
    existing_scene_ids = set(screen_id_map.keys())
    new_scenes = []
    new_scene_ids = set()

    for path in IN_PROGRESS_DIR.glob("*.json"):
        name = path.name
        if name.endswith("-merged.json"):
            continue
        if name in {"hotspots.json", "new_scenes.json"}:
            continue
        try:
            entries = parse_appended_json(path)
        except Exception:
            continue
        target_scene_id = path.stem
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            from_scene = entry.get("fromScene")
            if not isinstance(from_scene, str):
                from_scene = None
            screen_id = screen_id_map.get(from_scene)
            for change in entry.get("changes", []) if isinstance(entry.get("changes"), list) else []:
                if not isinstance(change, dict):
                    continue
                op = change.get("op") or change.get("details", {}).get("op")
                if op != "create_scene":
                    continue
                create_scene = change.get("createScene")
                if not isinstance(create_scene, dict):
                    continue
                if target_scene_id in existing_scene_ids or target_scene_id in new_scene_ids:
                    continue
                new_scene_ids.add(target_scene_id)
                character_ids = sorted(extract_character_ids(create_scene))
                new_scenes.append(
                    {
                        "id": target_scene_id,
                        "screenId": screen_id,
                        "sceneDescription": "",
                        "characters": [{"characterId": char_id} for char_id in character_ids],
                    }
                )

    new_scenes_path = IN_PROGRESS_DIR / "new_scenes.json"
    with new_scenes_path.open("w", encoding="utf-8") as handle:
        json.dump({"scenes": new_scenes}, handle, indent=2, sort_keys=True)


def apply_missing_trigger_logic():
    # Fill triggerLogic for scenes missing it by matching hotspot interactions.
    scenes_doc = load_json(SCENES_PATH)
    if not scenes_doc:
        return
    scenes_list = scenes_doc.get("scenes")
    if not isinstance(scenes_list, list):
        return
    hotspots_doc = load_json(HOTSPOTS_PATH)
    if not hotspots_doc:
        return
    hotspots_by_screen = hotspots_doc.get("hotspotsByScreen")
    if not isinstance(hotspots_by_screen, list):
        return

    scene_to_hotspot = {}
    for entry in hotspots_by_screen:
        if not isinstance(entry, dict):
            continue
        hotspots = entry.get("hotspots")
        if not isinstance(hotspots, list):
            continue
        for hotspot in hotspots:
            if not isinstance(hotspot, dict):
                continue
            name = hotspot.get("name")
            if not isinstance(name, str):
                continue
            for state in hotspot.get("states", []) if isinstance(hotspot.get("states"), list) else []:
                interactions = state.get("interactions")
                if not isinstance(interactions, list):
                    continue
                for interaction in interactions:
                    if not isinstance(interaction, dict):
                        continue
                    scene_id = interaction.get("sceneId")
                    if isinstance(scene_id, str) and scene_id not in scene_to_hotspot:
                        scene_to_hotspot[scene_id] = name

    updated = False
    for scene in scenes_list:
        if not isinstance(scene, dict):
            continue
        if scene.get("triggerLogic"):
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str):
            continue
        hotspot_name = scene_to_hotspot.get(scene_id)
        if hotspot_name:
            scene["triggerLogic"] = {"type": "onInteract", "hotspot": hotspot_name}
            updated = True

    if updated:
        with SCENES_PATH.open("w", encoding="utf-8") as handle:
            json.dump(scenes_doc, handle, indent=2, ensure_ascii=True)


def collect_used_scene_ids(scenes_list):
    used = set()
    widths = []
    for scene in scenes_list:
        scene_id = scene.get("id")
        if isinstance(scene_id, str):
            used.add(scene_id)
            match = re.match(r"^[A-Z]+-(\d{2,3})$", scene_id)
            if match:
                widths.append(len(match.group(1)))
    for path in DIALOGUE_DIR.glob("SCN_*.json"):
        stem = path.stem
        if stem.startswith("SCN_"):
            scene_id = stem[4:]
            used.add(scene_id)
            match = re.match(r"^[A-Z]+-(\d{2,3})$", scene_id)
            if match:
                widths.append(len(match.group(1)))
    width = 3 if 3 in widths else 2
    return used, width


def next_unused_scene_id(used_ids, width):
    i = 1
    while True:
        candidate = f"SCN-{i:0{width}d}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        i += 1


def is_valid_scene_id(scene_id):
    return bool(re.match(r"^SCN-\d{2,3}$", str(scene_id)))


def build_graph_id_index():
    index = {}
    for path in DIALOGUE_DIR.glob("SCN_*.json"):
        dialogue = load_json(path)
        if not dialogue:
            continue
        graphs = dialogue.get("dialogueGraphs", [])
        if not isinstance(graphs, list):
            continue
        for graph in graphs:
            graph_id = graph.get("id")
            scene_id = graph.get("sceneId")
            if graph_id and scene_id:
                index[graph_id] = scene_id
    return index


def openai_with_retry(client, prompt, label, max_retries=3, timeout_s=90):
    attempt = 0
    while True:
        try:
            print(f"Requesting LLM: {label}")
            return client.responses.create(
                model="gpt-5.2",
                input=prompt,
                timeout=timeout_s,
            )
        except Exception as exc:
            attempt += 1
            if attempt >= max_retries:
                raise exc
            print(f"Retrying OpenAI request for {label} (attempt {attempt + 1}/{max_retries})")
            time.sleep(2 * attempt)


def extract_character_ids(dialogue_payload):
    ids = set()
    if not isinstance(dialogue_payload, dict):
        return ids
    graphs = dialogue_payload.get("dialogueGraphs", [])
    if not isinstance(graphs, list):
        return ids
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        for node in graph.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            speaker = node.get("speakerId")
            if isinstance(speaker, str) and speaker != "NARRATOR":
                ids.add(speaker)
    return ids


def log_fixes_to_inprogress():
    if OpenAI is None:
        print("Missing dependency: openai. Install with `pip install openai`.", file=sys.stderr)
        return 1

    scenes = load_json(SCENES_PATH)
    if not scenes:
        return 1

    hotspots = load_json(HOTSPOTS_PATH)
    if hotspots is None:
        return 1
    hotspots_dirty = False

    scenes_schema = load_json(SCENES_SCHEMA_PATH)
    if scenes_schema is None:
        return 1

    IN_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    for path in IN_PROGRESS_DIR.glob("*.json"):
        try:
            path.unlink()
        except OSError as exc:
            print(f"Failed to delete {path}: {exc}", file=sys.stderr)
            return 1

    client = OpenAI()

    scenes_list = scenes.get("scenes", [])
    if not isinstance(scenes_list, list):
        print("Expected scenes.json to contain a 'scenes' list.", file=sys.stderr)
        return 1

    on_enter_scenes = [
        scene
        for scene in scenes_list
        if isinstance(scene.get("triggerLogic"), dict)
        and scene.get("triggerLogic", {}).get("type") == "onEnter"
    ]
    if not on_enter_scenes:
        print("No scenes with triggerLogic=onEnter found.")
        return 0

    dialogue_cache = {}
    graph_id_index = build_graph_id_index()
    used_scene_ids, scene_id_width = collect_used_scene_ids(scenes_list)
    existing_scene_ids = {
        scene.get("id")
        for scene in scenes_list
        if isinstance(scene, dict) and isinstance(scene.get("id"), str)
    }
    new_scenes = []

    for scene in on_enter_scenes:
        scene_id = scene.get("id")
        if scene_id is None:
            print("Skipping scene with missing id.", file=sys.stderr)
            continue

        dialogue_path = DIALOGUE_DIR / f"SCN_{scene_id}.json"
        dialogue = load_json(dialogue_path)
        if dialogue is None:
            continue
        dialogue_cache[scene_id] = dialogue
        append_leave_normalizations(scene_id, dialogue)

        prompt = build_prompt(scene, dialogue, hotspots)
        try:
            response = openai_with_retry(client, prompt, f"scene {scene_id}")
        except Exception as exc:
            print(f"OpenAI request failed for scene {scene_id}: {exc}", file=sys.stderr)
            continue

        try:
            parsed = json.loads(response.output_text)
        except json.JSONDecodeError as exc:
            parsed = extract_json(response.output_text)
            if parsed is None:
                print(f"Invalid JSON response for scene {scene_id}: {exc}", file=sys.stderr)
                print("Raw response:")
                print(response.output_text)
                print("\n" + ("-" * 80) + "\n")
                continue
        items = parsed.get("items", [])
        for item in items:
            dest_scene_id = item.get("destScene")
            if dest_scene_id is None:
                print("Missing destScene in item.", file=sys.stderr)
                return 1
            if dest_scene_id != "?" and not is_valid_scene_id(dest_scene_id):
                print(f"Invalid destScene id: {dest_scene_id}", file=sys.stderr)
                print(f"From scene: {scene_id}", file=sys.stderr)
                print(f"Item: {json.dumps(item, indent=2, sort_keys=True)}", file=sys.stderr)
                with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(f"Invalid destScene id: {dest_scene_id}\n")
                    handle.write(f"From scene: {scene_id}\n")
                    handle.write(f"Item: {json.dumps(item, indent=2, sort_keys=True)}\n")
                    handle.write("-" * 80 + "\n")

        for item in items:
            dest_scene_id = item.get("destScene")
            if dest_scene_id == "?":
                dest_scene_id = next_unused_scene_id(used_scene_ids, scene_id_width)
                item["destScene"] = dest_scene_id
                if update_hotspot_scene(hotspots, item.get("hotspot"), dest_scene_id):
                    hotspots_dirty = True
            dest_dialogue = None
            if dest_scene_id and dest_scene_id != "?":
                if dest_scene_id in dialogue_cache:
                    dest_dialogue = dialogue_cache[dest_scene_id]
                else:
                    dest_path = DIALOGUE_DIR / f"SCN_{dest_scene_id}.json"
                    dest_dialogue = load_json(dest_path)
                    if dest_dialogue is not None:
                        dialogue_cache[dest_scene_id] = dest_dialogue

            diff_records = []
            diff_prompt = build_diff_prompt(
                scene_id=scene_id,
                item=item,
                current_dialogue=dialogue_cache[scene_id],
                hotspots=hotspots,
                dest_dialogue=dest_dialogue,
                dest_scene_id=dest_scene_id,
                scenes_schema=scenes_schema,
            )

            try:
                diff_response = openai_with_retry(client, diff_prompt, f"hotspot {item.get('hotspot')}")
            except Exception as exc:
                print(f"OpenAI request failed for hotspot {item.get('hotspot')}: {exc}", file=sys.stderr)
                continue

            try:
                diff_parsed = json.loads(diff_response.output_text)
            except json.JSONDecodeError as exc:
                diff_parsed = extract_json(diff_response.output_text)
                if diff_parsed is None:
                    print(f"Invalid JSON diff for hotspot {item.get('hotspot')}: {exc}", file=sys.stderr)
                    print("Raw response:")
                    print(diff_response.output_text)
                    print("\n" + ("-" * 80) + "\n")
                    continue

            for diff in diff_parsed.get("diffs", []):
                target_scene_id = diff.get("sceneId")
                if not target_scene_id:
                    continue
                if not is_valid_scene_id(target_scene_id):
                    mapped_scene_id = graph_id_index.get(target_scene_id)
                    if mapped_scene_id and is_valid_scene_id(mapped_scene_id):
                        target_scene_id = mapped_scene_id
                        diff["sceneId"] = mapped_scene_id
                    else:
                        print(f"Invalid target scene id in diff: {target_scene_id}", file=sys.stderr)
                        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                            handle.write(f"Invalid target scene id in diff: {target_scene_id}\n")
                            handle.write(f"From scene: {scene_id}\n")
                            handle.write(f"Item: {json.dumps(item, indent=2, sort_keys=True)}\n")
                            handle.write(f"Diff: {json.dumps(diff, indent=2, sort_keys=True)}\n")
                            handle.write("-" * 80 + "\n")
                        continue
                diff_records.append(
                    {
                        "diff": diff,
                        "target_scene_id": target_scene_id,
                    }
                )
                print(f"Queued diff for scene {target_scene_id} from hotspot {item.get('hotspot')}.")

            for record in diff_records:
                diff = record["diff"]
                target_scene_id = record["target_scene_id"]
                changes = {
                    "fromScene": scene_id,
                    "hotspot": item.get("hotspot"),
                    "destScene": item.get("destScene"),
                    "changes": [],
                }
                create_scene = diff.get("createScene")
                if create_scene is not None:
                    changes["changes"].append(
                        {"action": "add", "op": "create_scene", "createScene": create_scene}
                    )
                    if (
                        isinstance(target_scene_id, str)
                        and target_scene_id not in existing_scene_ids
                        and target_scene_id not in {s.get("id") for s in new_scenes}
                    ):
                        screen_id = scene.get("screenId")
                        character_ids = sorted(extract_character_ids(create_scene))
                        new_scenes.append(
                            {
                                "id": target_scene_id,
                                "screenId": screen_id,
                                "sceneDescription": "",
                                "characters": [
                                    {"characterId": char_id} for char_id in character_ids
                                ],
                            }
                        )
                for op in diff.get("ops", []):
                    changes["changes"].append(
                        {
                            "action": classify_op(op.get("op")),
                            "op": op.get("op"),
                            "details": op,
                        }
                    )
                append_scene_changes(target_scene_id, changes)
                print(f"Wrote {IN_PROGRESS_DIR / f'{target_scene_id}.json'}")

    if hotspots_dirty:
        with IN_PROGRESS_HOTSPOTS_PATH.open("w", encoding="utf-8") as handle:
            json.dump(hotspots, handle, indent=2, sort_keys=True)
    return 0


def main():
    # End-to-end pipeline: generate edits (if needed), merge, resolve, apply, and sync.
    result = 0
    if not IN_PROGRESS_HOTSPOTS_PATH.exists():
        result = log_fixes_to_inprogress()
    else:
        print(f"Found {IN_PROGRESS_HOTSPOTS_PATH}, skipping in_progress generation.")

    potential_conflicts = 0
    for path in IN_PROGRESS_DIR.glob("*.json"):
        name = path.name
        if name.endswith("-merged.json"):
            continue
        if name == "hotspots.json":
            continue
        scene_id = path.stem
        potential_conflicts += review_changes(scene_id)
        print(f"{path.name} potential_conflicts: {potential_conflicts}")
        resolve_change_conflicts(scene_id)

    rebuild_new_scenes_json()

    merged_paths = list(IN_PROGRESS_DIR.glob("*-merged.json"))
    scene_ids = [path.stem.replace("-merged", "") for path in merged_paths]
    if merged_paths:
        backup_old(scene_ids)
    for path in merged_paths:
        scene_id = path.stem.replace("-merged", "")
        apply_merged_changes(scene_id)
    apply_new_scenes()
    if IN_PROGRESS_HOTSPOTS_PATH.exists():
        try:
            shutil.copy2(IN_PROGRESS_HOTSPOTS_PATH, HOTSPOTS_PATH)
        except OSError as exc:
            print(f"Failed to copy hotspots.json: {exc}", file=sys.stderr)
    apply_missing_trigger_logic()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
