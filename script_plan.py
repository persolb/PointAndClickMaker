#!/usr/bin/env python3
from __future__ import annotations

"""
End-to-end story planning pipeline: scenes, characters, hotspots, and dialogue.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import threading
import time

from openai import BadRequestError, OpenAI


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def build_prompt(
    *,
    story_text: str,
    screens_text: str,
    templates: dict[str, str],
) -> str:
    instructions = (
        "You are creating story_specific_gen/scenes.json for a point-and-click narrative game.\n"
        "Use the story and screens as the source of truth.\n"
        "Follow the scenes schema and keep screenId values consistent with screens.json.\n"
        "Do NOT fill in the characters for any scene; if you include a characters field, it must be an empty array.\n"
        "Return ONLY valid JSON for story_specific_gen/scenes.json with a top-level object containing a 'scenes' array.\n"
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "TEMPLATE: story.md",
        templates.get("story.md", ""),
        "TEMPLATE: screens.schema.json",
        templates.get("screens.schema.json", ""),
        "TEMPLATE: scenes.schema.json",
        templates.get("scenes.schema.json", ""),
        "TEMPLATE: dialogue.schema.json",
        templates.get("dialogue.schema.json", ""),
        "STORY.md",
        story_text,
        "SCREENS.json",
        screens_text,
    ]
    return "\n\n".join(part for part in parts if part)


def load_templates(templates_dir: str) -> dict[str, str]:
    template_files = [
        "story.md",
        "screens.schema.json",
        "scenes.schema.json",
        "dialogue.schema.json",
    ]
    templates = {}
    for name in template_files:
        path = os.path.join(templates_dir, name)
        if not os.path.exists(path):
            raise SystemExit(f"Missing template: {path}")
        templates[name] = read_text(path)
    return templates


def normalize_scenes(raw_text: str) -> dict:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model output was not valid JSON: {exc}") from exc
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Output JSON must include a top-level 'scenes' array")
    for scene in scenes:
        if isinstance(scene, dict):
            scene["characters"] = []
    return data


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def build_char_prompt(*, story_text: str, scenes_text: str, screens_text: str) -> str:
    instructions = (
        "You are creating a complete, canonical character list for a point-and-click narrative game.\n\n"
        "Source of truth\n\n"
        "- Use the provided story, story_specific_gen/scenes.json, and story_specific/screens.json only.\n\n"
        "What counts as a character\n\n"
        "- A character is a specific individual the player can plausibly interact with, be opposed by, receive "
        "information from, or whose actions materially affect access, evidence, operations, or outcomes.\n"
        "- Do not list factions, organizations, locations, or abstract groups as characters.\n\n"
        "Required coverage (do not omit)\n\n"
        "Include:\n\n"
        "- PLAYER (must be first; exact line below)\n"
        "- Distinct individuals covering each faction’s presence\n"
        "- Any implied role that controls:\n"
        "  - access/badges/keys\n"
        "  - records/logs/evidence handling\n"
        "  - device operations (power, interlocks, testing, alias control)\n"
        "  - negotiation/communications with STRD\n"
        "  - demo-day/public messaging\n\n"
        "Naming rules\n\n"
        "- If a personal name is not provided, assign a stable role-name (examples: “Site Director”, “Gate Guard”, "
        "“Records Custodian”, “Control Room Operator”, “MERI Systems Lead”, “COAL Organizer”, "
        "“STRD Returnist Lead”, “STRD Warden Lead”). If a major character, give them a name and not their role.\n"
        "- If you assign a name, keep it minimal and consistent; do not create backstory.\n\n"
        "Consistency check (must apply before output)\n\n"
        "- If two entries describe the same implied person/role, merge into one.\n"
        "- If a role appears in multiple places (e.g., guard shack + access gating), treat as one character unless "
        "the text clearly implies separate individuals.\n"
        "- No duplicates, no near-duplicates, no “also/another guard” style filler.\n\n"
        "Output requirements\n\n"
        "- Return ONLY the character list, one per line, no extra text.\n"
        "- Format exactly:\n"
        "  Name: role / one-line description\n"
        "- The first line must be exactly:\n"
        "  PLAYER: field systems specialist / point-of-view character\n"
        "- Keep each description to one sentence fragment (no semicolons, no multi-sentence entries)."
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "STORY.md",
        story_text,
        "SCENES.json",
        scenes_text,
        "SCREENS.json",
        screens_text,
    ]
    return "\n\n".join(part for part in parts if part)


def build_characters_json_prompt(
    *,
    story_text: str,
    screens_text: str,
    scenes_text: str,
    characters_schema_text: str,
    character_list_text: str,
) -> str:
    instructions = (
        "You are creating story_specific_gen/characters.json for a point-and-click narrative game.\n"
        "Use the provided story, scenes, and screens as the source of truth.\n"
        "Follow the characters schema and return ONLY valid JSON for story_specific_gen/characters.json.\n"
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "TEMPLATE: characters.schema.json",
        characters_schema_text,
        "STORY.md",
        story_text,
        "SCREENS.json",
        screens_text,
        "SCENES.json",
        scenes_text,
        "CHARACTER LIST",
        character_list_text,
    ]
    return "\n\n".join(part for part in parts if part)


def build_hotspots_prompt(
    *,
    screen_text: str,
    scenes_text: str,
    hotspots_schema_text: str,
) -> str:
    instructions = (
        "You are creating hotspots for a single screen in story_specific_gen/hotspots.json.\n"
        "Use the provided screen and applicable scenes as the source of truth.\n"
        "Follow the hotspots schema and return ONLY valid JSON for a single hotspotsForScreen object.\n"
        "Output must be a JSON object with keys: screenId, optional variantId, and hotspots.\n"
        "Do not wrap the response in hotspotsByScreen; that will be assembled separately.\n"
        "For geometry, leave exact coordinates blank: use {\"type\": \"mask\", \"maskId\": \"TBD\"}.\n"
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "TEMPLATE: hotspots.schema.json",
        hotspots_schema_text,
        "SCREEN",
        screen_text,
        "APPLICABLE SCENES",
        scenes_text,
    ]
    return "\n\n".join(part for part in parts if part)


def parse_character_lines(raw_text: str) -> list[str]:
    lines = []
    for line in raw_text.splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def extract_story_excerpt(story_text: str) -> str:
    wanted = {
        "core premise",
        "narrative rules",
        "factions",
        "act context",
        "acts",
        "act",
    }
    lines = story_text.splitlines()
    sections: list[str] = []
    current_title = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title and current_lines:
            sections.append(f"{current_title}\n" + "\n".join(current_lines).strip())
        current_title = None
        current_lines = []

    for line in lines:
        if re.match(r"^#{1,6}\\s+\\S", line):
            flush()
            current_title = line.strip()
            continue
        if current_title is not None:
            current_lines.append(line)

    flush()

    picked = []
    for section in sections:
        header = section.splitlines()[0].lstrip("# ").strip().lower()
        if any(key in header for key in wanted):
            picked.append(section)
    if not picked:
        return "\n".join(lines[:200])
    return "\n\n".join(picked)


def summarize_trigger_logic(trigger: dict | None) -> dict:
    if not isinstance(trigger, dict):
        return {}
    summary = {"type": trigger.get("type")}
    conditions = trigger.get("conditions")
    if isinstance(conditions, list):
        summary["conditions"] = conditions
    return summary


def should_generate_dialogue(scene: dict) -> bool:
    if not isinstance(scene, dict):
        return False
    if scene.get("characters"):
        return True
    if scene.get("possibleOutcomes"):
        return True
    description = scene.get("sceneDescription", "") or ""
    return "[dialogue]" in description.lower()


def normalize_name_list(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    names = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("id")
            if name:
                names.append(str(name))
    return names


def prune_empty(value: object) -> object:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            pruned = prune_empty(item)
            if pruned is None:
                continue
            if pruned == "" or pruned == [] or pruned == {}:
                continue
            cleaned[key] = pruned
        return cleaned
    if isinstance(value, list):
        cleaned_list = [prune_empty(item) for item in value]
        cleaned_list = [
            item for item in cleaned_list if item not in (None, "", [], {})
        ]
        return cleaned_list
    return value


def validate_schema(data: object, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type:
        if expected_type == "object" and not isinstance(data, dict):
            return [f"{path}: expected object"]
        if expected_type == "array" and not isinstance(data, list):
            return [f"{path}: expected array"]
        if expected_type == "string" and not isinstance(data, str):
            return [f"{path}: expected string"]
        if expected_type == "integer" and not (isinstance(data, int) and not isinstance(data, bool)):
            return [f"{path}: expected integer"]
        if expected_type == "number" and not (isinstance(data, (int, float)) and not isinstance(data, bool)):
            return [f"{path}: expected number"]
        if expected_type == "boolean" and not isinstance(data, bool):
            return [f"{path}: expected boolean"]

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: value not in enum")

    pattern = schema.get("pattern")
    if pattern and isinstance(data, str):
        if re.match(pattern, data) is None:
            errors.append(f"{path}: string does not match pattern")

    if isinstance(data, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                errors.append(f"{path}: missing required key '{key}'")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in data.items():
            if key in properties:
                errors.extend(validate_schema(value, properties[key], f"{path}.{key}"))
            elif additional is False:
                errors.append(f"{path}: unexpected key '{key}'")
    elif isinstance(data, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(data) < min_items:
            errors.append(f"{path}: expected at least {min_items} items")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(data):
                errors.extend(validate_schema(item, items_schema, f"{path}[{idx}]"))
    return errors


def filter_other_dialogue(content: str) -> dict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    graphs = data.get("dialogueGraphs")
    if not isinstance(graphs, list):
        return {}
    cleaned_graphs = []
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        scene_id = graph.get("sceneId")
        nodes = graph.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []
        cleaned_nodes = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            filtered_node = {
                k: v
                for k, v in node.items()
                if k not in {"id", "type", "speakerId", "next", "choices", "outcomeId", "effects"}
            }
            cleaned_nodes.append(filtered_node)
        cleaned_graphs.append({"sceneId": scene_id, "nodes": cleaned_nodes})
    return {"dialogueGraphs": cleaned_graphs}


def format_dialogue_lines(content: str, allowed_speakers: set[str]) -> list[str]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    graphs = data.get("dialogueGraphs")
    if not isinstance(graphs, list):
        return []
    lines: list[str] = []
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        nodes = graph.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            speaker_id = node.get("speakerId")
            text = node.get("text")
            if (
                speaker_id
                and text
                and speaker_id in allowed_speakers
                and speaker_id not in {"PLAYER", "NARRATOR"}
            ):
                lines.append(f"{speaker_id}: {text}")
    return lines


def generate_scenes(args: argparse.Namespace) -> None:
    # Create scenes.json from story/screen inputs via LLM.
    story_text = read_text(args.story)
    screens_text = read_text(args.screens)
    try:
        json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc
    templates = load_templates(args.templates_dir)
    prompt = build_prompt(
        story_text=story_text,
        screens_text=screens_text,
        templates=templates,
    )

    client = OpenAI()
    text_format = {"type": "json_object"}
    if args.response_format == "json_schema":
        schema_text = read_text(args.schema_path)
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as exc:
            print(
                f"Warning: {args.schema_path} is not valid JSON ({exc}). "
                "Falling back to json_object response format.",
                file=sys.stderr,
            )
            schema = None
        if schema is not None:
            text_format = {
                "type": "json_schema",
                "name": "scenes",
                "schema": schema,
                "strict": True,
            }

    print("Requesting LLM to generate story_specific_gen/scenes.json... (may take up to 15 minutes)")
    done_event = threading.Event()

    def progress_loop() -> None:
        waited = 0
        while not done_event.wait(60):
            waited += 60
            print(f"Still waiting... ({waited}s)")

    progress_thread = threading.Thread(target=progress_loop, daemon=True)
    progress_thread.start()
    try:
        response = client.responses.create(
            model=args.model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": text_format},
        )
    except BadRequestError as exc:
        message = str(exc)
        if args.response_format == "json_schema" and "invalid_json_schema" in message:
            print(
                "Warning: json_schema rejected by API. "
                "Retrying with json_object response format.",
                file=sys.stderr,
            )
            response = client.responses.create(
                model=args.model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                reasoning={"effort": "high"},
                text={"format": {"type": "json_object"}},
            )
        else:
            raise
    finally:
        done_event.set()

    raw_text = response.output_text.strip()
    try:
        data = normalize_scenes(raw_text)
    except ValueError as exc:
        raw_path = f"{args.out}.raw.txt"
        with open(raw_path, "w", encoding="utf-8") as handle:
            handle.write(raw_text)
            handle.write("\n")
        raise SystemExit(f"{exc}. Raw output saved to {raw_path}") from exc

    write_json(args.out, data)


def generate_char_list(args: argparse.Namespace) -> list[str]:
    # Produce a canonical character list from story/screen/scene context.
    story_text = read_text(args.story)
    scenes_text = read_text(args.scenes)
    screens_text = read_text(args.screens)
    try:
        json.loads(scenes_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.scenes} is not valid JSON: {exc}") from exc
    try:
        json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc

    prompt = build_char_prompt(
        story_text=story_text,
        scenes_text=scenes_text,
        screens_text=screens_text,
    )

    client = OpenAI()
    print("Requesting LLM to generate character list...")
    response = client.responses.create(
        model=args.model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    )

    raw_text = response.output_text.strip()
    lines = parse_character_lines(raw_text)
    if not lines:
        raise SystemExit("No characters returned by the model.")

    print("CHARACTERS")
    for line in lines:
        if ":" in line:
            name, desc = line.split(":", 1)
            print(f" - {name.strip()}: {desc.strip()}")
        else:
            print(f" - {line}")
    return lines


def generate_characters_json(args: argparse.Namespace, character_lines: list[str]) -> None:
    # Convert the short character list into a structured characters.json.
    story_text = read_text(args.story)
    screens_text = read_text(args.screens)
    scenes_text = read_text(args.scenes)
    try:
        json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc
    try:
        json.loads(scenes_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.scenes} is not valid JSON: {exc}") from exc

    schema_path = os.path.join(args.templates_dir, "characters.schema.json")
    if not os.path.exists(schema_path):
        raise SystemExit(f"Missing template: {schema_path}")
    characters_schema_text = read_text(schema_path)
    character_list_text = "\n".join(character_lines)
    prompt = build_characters_json_prompt(
        story_text=story_text,
        screens_text=screens_text,
        scenes_text=scenes_text,
        characters_schema_text=characters_schema_text,
        character_list_text=character_list_text,
    )

    print("Requesting LLM to generate story_specific_gen/characters.json... (may take up to 15 minutes)")
    done_event = threading.Event()

    def progress_loop() -> None:
        waited = 0
        while not done_event.wait(60):
            waited += 60
            print(f"Still waiting... ({waited}s)")

    progress_thread = threading.Thread(target=progress_loop, daemon=True)
    progress_thread.start()
    try:
        client = OpenAI()
        response = client.responses.create(
            model=args.model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": {"type": "json_object"}},
        )
    finally:
        done_event.set()

    raw_text = response.output_text.strip()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raw_path = f"{args.characters_out}.raw.txt"
        with open(raw_path, "w", encoding="utf-8") as handle:
            handle.write(raw_text)
            handle.write("\n")
        raise SystemExit(f"Model output was not valid JSON: {exc}. Raw output saved to {raw_path}") from exc

    write_json(args.characters_out, data)
    print(f"Wrote {args.characters_out}")


def generate_hotspots_json(args: argparse.Namespace) -> None:
    # Generate per-screen hotspots.json using scene context.
    screens_text = read_text(args.screens)
    scenes_text = read_text(args.scenes)
    try:
        screens_data = json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc
    try:
        scenes_data = json.loads(scenes_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.scenes} is not valid JSON: {exc}") from exc

    if isinstance(screens_data, dict):
        screens_list = screens_data.get("screens")
    else:
        screens_list = screens_data
    if not isinstance(screens_list, list):
        raise SystemExit("Screens data must be a list or contain a top-level 'screens' array.")

    scenes_list = scenes_data.get("scenes") if isinstance(scenes_data, dict) else scenes_data
    if not isinstance(scenes_list, list):
        raise SystemExit("Scenes data must be a list or contain a top-level 'scenes' array.")

    schema_path = os.path.join(args.templates_dir, "hotspots.schema.json")
    if not os.path.exists(schema_path):
        raise SystemExit(f"Missing template: {schema_path}")
    hotspots_schema_text = read_text(schema_path)

    client = OpenAI()
    hotspots_by_screen: list[dict] = []
    total_screens = len(screens_list)
    for idx, screen in enumerate(screens_list, start=1):
        if not isinstance(screen, dict):
            continue
        screen_id = screen.get("id") or screen.get("screenId") or screen.get("screen_id")
        if not screen_id:
            continue
        applicable_scenes = [
            scene
            for scene in scenes_list
            if isinstance(scene, dict) and scene.get("screenId") == screen_id
        ]
        screen_json = json.dumps(screen, indent=2, ensure_ascii=True)
        scenes_json = json.dumps(applicable_scenes, indent=2, ensure_ascii=True)
        prompt = build_hotspots_prompt(
            screen_text=screen_json,
            scenes_text=scenes_json,
            hotspots_schema_text=hotspots_schema_text,
        )

        if args.debug == 1:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(prompt)
                handle.write("\n")
            print("Wrote debug.log with first hotspot prompt.")
            raise SystemExit(0)

        percent = int((idx / total_screens) * 100) if total_screens else 100
        print(f"Requesting LLM to generate hotspots for {screen_id}... [{percent:02d}%]")
        response = client.responses.create(
            model="gpt-5.2",
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": {"type": "json_object"}},
        )
        raw_text = response.output_text.strip()
        if args.debug == 2:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            print("Wrote debug.log with first hotspot response.")
            raise SystemExit(0)
        try:
            entry = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raw_path = f"{args.hotspots_out}.{screen_id}.raw.txt"
            with open(raw_path, "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            raise SystemExit(
                f"Model output for {screen_id} was not valid JSON: {exc}. "
                f"Raw output saved to {raw_path}"
            ) from exc
        hotspots_by_screen.append(entry)

    write_json(args.hotspots_out, {"hotspotsByScreen": hotspots_by_screen})
    print(f"Wrote {args.hotspots_out}")


def build_scene_characters_prompt(
    *,
    scene_text: str,
    screen_text: str,
    characters_text: str,
    story_text: str,
) -> str:
    instructions = (
        "You are assigning characters to a single scene.\n"
        "Use the provided scene, screen, and characters as the source of truth.\n"
        "Return ONLY a JSON object with a single key 'characters'.\n"
        "The 'characters' value must be a JSON array of character entries for story_specific_gen/scenes.json.\n"
        "Each entry must include characterId and may include role and notes.\n"
        "The 'characters' array must follow this schema:\n"
        "{\n"
        '  "characters": {\n'
        '    "type": "array",\n'
        '    "description": "Characters present or participating in this scene.",\n'
        '    "items": {\n'
        '      "type": "object",\n'
        '      "required": ["characterId"],\n'
        '      "additionalProperties": true,\n'
        '      "properties": {\n'
        '        "characterId": {\n'
        '          "type": "string",\n'
        '          "description": "ID of the character (from story_specific_gen/characters.json)."\n'
        "        },\n"
        '        "role": {\n'
        '          "type": "string",\n'
        '          "description": "Narrative role in this scene (e.g., speaker, observer, antagonist, companion)."\n'
        "        },\n"
        '        "notes": {\n'
        '          "type": "string",\n'
        '          "description": "Writer-facing notes about this character’s behavior or constraints in the scene."\n'
        "        }\n"
        "      }\n"
        "    },\n"
        '    "default": []\n'
        "  }\n"
        "}\n"
        "Do not invent characters not present in story_specific_gen/characters.json.\n"
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "SCENE",
        scene_text,
        "SCREEN",
        screen_text,
        "CHARACTERS.json",
        characters_text,
        "STORY.md (relevant excerpt)",
        story_text,
    ]
    return "\n\n".join(part for part in parts if part)


def assign_characters_to_scenes(args: argparse.Namespace) -> None:
    # Fill scenes.json character lists using LLM output.
    scenes_text = read_text(args.scenes)
    screens_text = read_text(args.screens)
    story_text = read_text(args.story)
    characters_text = read_text(args.characters_out)
    try:
        scenes_data = json.loads(scenes_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.scenes} is not valid JSON: {exc}") from exc
    try:
        screens_data = json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc
    try:
        json.loads(characters_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.characters_out} is not valid JSON: {exc}") from exc

    scenes_list = scenes_data.get("scenes") if isinstance(scenes_data, dict) else scenes_data
    if not isinstance(scenes_list, list):
        raise SystemExit("Scenes data must be a list or contain a top-level 'scenes' array.")

    if isinstance(screens_data, dict):
        screens_list = screens_data.get("screens")
    else:
        screens_list = screens_data
    if not isinstance(screens_list, list):
        raise SystemExit("Screens data must be a list or contain a top-level 'screens' array.")

    screens_by_id = {
        (screen.get("id") or screen.get("screenId") or screen.get("screen_id")): screen
        for screen in screens_list
        if isinstance(screen, dict)
        and (screen.get("id") or screen.get("screenId") or screen.get("screen_id"))
    }

    client = OpenAI()
    total_scenes = len(scenes_list)
    auto_accept = bool(args.yolo)
    for idx, scene in enumerate(scenes_list, start=1):
        if not isinstance(scene, dict):
            continue
        scene_id_raw = scene.get("id")
        if not isinstance(scene_id_raw, str) or not scene_id_raw.strip():
            print(f"Skipping scene with missing id at index {idx}")
            continue
        scene_id = re.sub(r"[^A-Za-z0-9_-]+", "", scene_id_raw)
        if scene.get("characters"):
            continue
        screen_id = scene.get("screenId")
        screen = screens_by_id.get(screen_id, {})
        scene_json = json.dumps(scene, indent=2, ensure_ascii=True)
        screen_json = json.dumps(screen, indent=2, ensure_ascii=True)
        prompt = build_scene_characters_prompt(
            scene_text=scene_json,
            screen_text=screen_json,
            characters_text=characters_text,
            story_text=extract_story_excerpt(story_text),
        )
        percent = int((idx / total_scenes) * 100) if total_scenes else 100
        print(f"Requesting LLM to assign characters for {scene_id}... [{percent:02d}%]")
        if args.debug == 3:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(prompt)
                handle.write("\n")
            print("Wrote debug.log with first scene-character prompt.")
            raise SystemExit(0)
        response = client.responses.create(
            model="gpt-5.2",
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": {"type": "json_object"}},
        )
        raw_text = response.output_text.strip()
        if args.debug == 4:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            print("Wrote debug.log with first scene-character response.")
            raise SystemExit(0)
        try:
            assigned = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raw_path = f"{args.scenes}.characters.{scene_id}.raw.txt"
            with open(raw_path, "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            raise SystemExit(
                f"Model output for {scene_id} was not valid JSON: {exc}. "
                f"Raw output saved to {raw_path}"
            ) from exc
        if isinstance(assigned, dict) and isinstance(assigned.get("characters"), list):
            scene["characters"] = assigned["characters"]
        elif isinstance(assigned, list):
            scene["characters"] = assigned
        else:
            raw_path = f"{args.scenes}.characters.{scene_id}.raw.txt"
            with open(raw_path, "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            raise SystemExit(
                f"Model output for {scene_id} was not a JSON object with 'characters'. "
                f"Raw output saved to {raw_path}"
            )

    write_json(args.scenes, scenes_data)
    print(f"Updated {args.scenes} with characters.")


def build_dialogue_prompt(
    *,
    context_packet: dict,
    dialogue_schema_text: str,
    story_excerpt: str,
    narrative_text: str,
) -> str:
    instructions = (
        "You are generating a branching dialogue graph for a point-and-click game.\n"
        "Follow the dialogue schema exactly and return ONLY valid JSON.\n"
        "Apply the graph design rules and mechanical constraints.\n"
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "DIALOGUE SCHEMA",
        dialogue_schema_text,
        "CONTEXT PACKET",
        json.dumps(context_packet, indent=2, ensure_ascii=True),
        "NARRATIVE DRAFT",
        narrative_text,
        "GRAPH DESIGN RULES",
        (
            "1) Include at least one loop-back choice to the main menu node.\n"
            "2) Include at least one gated option if any faction/skill variables exist.\n"
            "3) Map outcomes: at least one end node sets outcomeId; if multiple outcomes, cover each or explain why.\n"
            "4) Put mechanical effects only on the committing choice or the end node.\n"
        ),
        "MECHANICAL CONSTRAINTS",
        "Allowed verbs: talk, ask, leave, use, look, give, show, persuade, threaten, bargain.",
    ]
    return "\n\n".join(part for part in parts if part)


def build_dialogue_narrative_prompt(
    *,
    context_packet: dict,
    dialog_style_text: str,
    story_excerpt: str,
) -> str:
    instructions = (
        "Draft a narrative outline of the dialogue for this scene.\n"
        "Describe the flow, key beats, and intent of choices, without JSON.\n"
        "Keep it concise and focused on what the player sees and chooses.\n"
        "Then return draft dialog, script style, with duplicate indented dialog where there are multiple conversation paths. "
        "The narrative should not include looking at other things in the scene, or options to leave the room. "
        "Return ONLY the narrative text and draft dialog."
    )
    parts = [
        "INSTRUCTIONS",
        instructions,
        "CONTEXT PACKET",
        json.dumps(context_packet, indent=2, ensure_ascii=True),
        "DIALOG STYLE",
        dialog_style_text,
        "STORY EXCERPT",
        story_excerpt,
        "GRAPH DESIGN RULES",
        (
            "1) Include at least one loop-back choice to the main menu node.\n"
            "2) Include at least one gated option if any faction/skill variables exist.\n"
            "3) Map outcomes: at least one end node sets outcomeId; if multiple outcomes, cover each or explain why.\n"
            "4) Put mechanical effects only on the committing choice or the end node.\n"
        ),
        "MECHANICAL CONSTRAINTS",
        "Allowed verbs: talk, ask, leave, use, look, give, show, persuade, threaten, bargain.",
    ]
    return "\n\n".join(part for part in parts if part)


def validate_scene_references(
    *,
    scenes_list: list[dict],
    screens_by_id: dict,
    character_ids: set[str],
) -> None:
    for scene in scenes_list:
        if not isinstance(scene, dict):
            continue
        screen_id = scene.get("screenId")
        if screen_id not in screens_by_id:
            raise SystemExit(f"Scene {scene.get('id')} references unknown screenId: {screen_id}")
        for entry in scene.get("characters", []) or []:
            if not isinstance(entry, dict):
                continue
            character_id = entry.get("characterId")
            if character_id and character_id not in character_ids:
                raise SystemExit(
                    f"Scene {scene.get('id')} references unknown characterId: {character_id}"
                )
        outcomes = scene.get("possibleOutcomes", [])
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if isinstance(outcome, dict) and "id" not in outcome:
                    raise SystemExit(f"Scene {scene.get('id')} has possibleOutcome missing id")


def generate_dialogue_for_scenes(args: argparse.Namespace) -> None:
    # Draft narrative, accept/reject, then generate structured dialogue JSON.
    story_text = read_text(args.story)
    dialog_style_text = read_text(args.dialog_style)
    dialogue_schema_text = read_text(args.dialogue_schema)
    scenes_text = read_text(args.scenes)
    screens_text = read_text(args.screens)
    characters_text = read_text(args.characters_out)

    try:
        scenes_data = json.loads(scenes_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.scenes} is not valid JSON: {exc}") from exc
    try:
        screens_data = json.loads(screens_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.screens} is not valid JSON: {exc}") from exc
    try:
        characters_data = json.loads(characters_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.characters_out} is not valid JSON: {exc}") from exc
    try:
        dialogue_schema = json.loads(dialogue_schema_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{args.dialogue_schema} is not valid JSON: {exc}") from exc

    scenes_list = scenes_data.get("scenes") if isinstance(scenes_data, dict) else scenes_data
    if not isinstance(scenes_list, list):
        raise SystemExit("Scenes data must be a list or contain a top-level 'scenes' array.")
    if isinstance(screens_data, dict):
        screens_list = screens_data.get("screens")
    else:
        screens_list = screens_data
    if not isinstance(screens_list, list):
        raise SystemExit("Screens data must be a list or contain a top-level 'screens' array.")

    screens_by_id = {
        screen.get("id") or screen.get("screenId") or screen.get("screen_id"): screen
        for screen in screens_list
        if isinstance(screen, dict)
    }
    characters_list = characters_data.get("characters") if isinstance(characters_data, dict) else []
    if not isinstance(characters_list, list):
        raise SystemExit("Characters data must contain a top-level 'characters' array.")
    characters_by_id = {
        c.get("id") or c.get("characterId"): c for c in characters_list if isinstance(c, dict)
    }
    character_ids = set(characters_by_id.keys())

    validate_scene_references(
        scenes_list=scenes_list,
        screens_by_id=screens_by_id,
        character_ids=character_ids,
    )

    os.makedirs(args.dialogue_dir, exist_ok=True)
    manifest_entries: list[dict] = []
    story_excerpt = extract_story_excerpt(story_text)
    client = OpenAI()

    target_scenes = [
        scene
        for scene in scenes_list
        if isinstance(scene, dict) and should_generate_dialogue(scene)
    ]
    total_targets = len(target_scenes)
    start_time = time.perf_counter()
    narrative_done = 0
    auto_accept = False

    def format_eta(seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    for idx, scene in enumerate(target_scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("id", f"scene-{idx}")).strip()
        screen_id = scene.get("screenId")
        output_path = os.path.join(args.dialogue_dir, f"SCN_{scene_id}.json")
        existing_path = output_path if os.path.exists(output_path) else None
        if existing_path is None:
            target_name = f"scn_{scene_id}.json".lower()
            try:
                for name in os.listdir(args.dialogue_dir):
                    if name.lower() == target_name:
                        existing_path = os.path.join(args.dialogue_dir, name)
                        break
            except FileNotFoundError:
                existing_path = None
        if existing_path:
            output_path = existing_path
            print(f"Skipping existing dialogue: {output_path}")
            with open(output_path, "r", encoding="utf-8") as handle:
                raw = handle.read()
            try:
                existing = json.loads(raw)
                graph_id = None
                if isinstance(existing, dict):
                    graphs = existing.get("dialogueGraphs", [])
                    if isinstance(graphs, list) and graphs:
                        graph_id = graphs[0].get("id")
            except json.JSONDecodeError:
                graph_id = None
            entry = {
                "sceneId": scene_id,
                "screenId": screen_id,
                "dialogueGraphId": graph_id,
                "path": output_path,
                "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "model": args.dialogue_model,
                "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
            }
            manifest_entries.append(entry)
            continue
        print(f"Dialogue file not found for scene {scene_id}: {output_path}")

        screen = screens_by_id.get(screen_id, {})
        screen_context = {
            "id": screen.get("id"),
            "name": screen.get("name"),
        "hotspots": normalize_name_list(screen.get("hotspots", [])),
            "adjacentScreens": [
                {
                    "description": " - ".join(
                        part
                        for part in (
                            screens_by_id.get(conn.get("to"), {}).get("name"),
                            conn.get("direction"),
                        )
                        if part
                    )
                }
                for conn in screen.get("connections", []) or []
                if isinstance(conn, dict)
            ],
        }

        scene_characters = scene.get("characters", []) or []
        current_character_ids = {
            entry.get("characterId")
            for entry in scene_characters
            if isinstance(entry, dict) and entry.get("characterId")
        }
        context_characters = []
        for entry in scene_characters:
            if not isinstance(entry, dict):
                continue
            context_characters.append(
                {
                    "characterId": entry.get("characterId"),
                    "required": True,
                }
            )
        character_sheets = []
        character_scene_index = []
        other_dialogue = []
        for s in scenes_list:
            if not isinstance(s, dict) or s.get("id") == scene_id:
                continue
            scene_char_ids = [
                c.get("characterId")
                for c in (s.get("characters") or [])
                if isinstance(c, dict) and c.get("characterId")
            ]
            shared = [cid for cid in scene_char_ids if cid in current_character_ids]
            shared_non_player = [cid for cid in shared if cid != "PLAYER"]
            if not shared_non_player:
                continue
            entry = {
                "sceneId": s.get("id"),
                "screenId": s.get("screenId"),
                "sharedCharacters": sorted(set(shared_non_player)),
            }
            other_scene_id = s.get("id")
            other_path = os.path.join(args.dialogue_dir, f"SCN_{other_scene_id}.json")
            if not os.path.exists(other_path):
                # print(f"Warning: dialogue not found at {other_path}")
                other_path = None
            formatted: list[str] = []
            if other_path:
                try:
                    other_content = read_text(other_path)
                except OSError:
                    other_content = ""
                filtered = filter_other_dialogue(other_content)
                formatted = format_dialogue_lines(other_content, current_character_ids)
                other_dialogue.append(
                    {
                        "sceneId": other_scene_id,
                        "path": other_path,
                        "content": filtered,
                    }
                )
            entry["other_scene_dialogue"] = formatted
            character_scene_index.append(entry)
        for entry in scene_characters:
            if not isinstance(entry, dict):
                continue
            char_id = entry.get("characterId")
            if not char_id:
                continue
            char = characters_by_id.get(char_id, {})
            character_sheets.append(
                {
                    "id": char.get("id"),
                    "name": char.get("name"),
                    "role": char.get("role"),
                    "factions": char.get("factions", []),
                    "short_description": char.get("short_description"),
                    "goals": char.get("goals"),
                    "secrets": char.get("secrets"),
                    "dialogue": char.get("dialogue"),
                    "speech_patterns": char.get("speech_patterns"),
                    "scene_role": entry.get("role"),
                    "scene_notes": entry.get("notes"),
                }
            )

        context_payload = {
            "scene": {
                "sceneId": scene_id,
                "screenId": screen_id,
                "triggerLogic": summarize_trigger_logic(scene.get("triggerLogic")),
                "possibleOutcomes": [
                    {
                        "id": outcome.get("id"),
                        "description": outcome.get("description"),
                        "effects": outcome.get("effects"),
                    }
                    for outcome in (scene.get("possibleOutcomes") or [])
                    if isinstance(outcome, dict)
                ],
            },
            "screen": screen_context,
            "characters": character_sheets,
            "character_scene_index": character_scene_index,
        }
        #if other_dialogue:
        #    context_payload["other_dialogue"] = other_dialogue
        context_packet = prune_empty(context_payload)

        while True:
            narrative_prompt = build_dialogue_narrative_prompt(
                context_packet=context_packet,
                dialog_style_text=dialog_style_text,
                story_excerpt=story_excerpt,
            )
            if narrative_done == 0:
                avg_seconds = 600.0
            else:
                avg_seconds = (time.perf_counter() - start_time) / narrative_done
            remaining = max(total_targets - narrative_done, 0) * avg_seconds
            percent = int((narrative_done / total_targets) * 100) if total_targets else 100
            eta = format_eta(remaining)
            print(
                f"Requesting LLM to draft dialogue narrative for {scene_id}... "
                f"[{percent:02d}% - {eta} left]"
            )
            if args.debug == 5:
                with open("debug.log", "w", encoding="utf-8") as handle:
                    handle.write(narrative_prompt)
                    handle.write("\n")
                print("Wrote debug.log with first dialogue narrative prompt.")
                raise SystemExit(0)
            narrative_rsp = client.responses.create(
                model=args.dialogue_model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": narrative_prompt}]}],
                reasoning={"effort": "none"},
            )
            narrative_text = narrative_rsp.output_text.strip()
            narrative_done += 1
            print(f"\n--- Narrative draft for {scene_id} ---\n{narrative_text}\n--- End draft ---\n")
            if args.yolo or auto_accept:
                choice = "a"
            else:
                choice = input(
                    "Accept narrative and generate JSON? [a]ccept/[r]edo/[s]kip/[y]olo: "
                ).strip().lower()
                if choice in ("y", "yolo"):
                    auto_accept = True
                    choice = "a"
            if choice in ("s", "skip"):
                narrative_text = None
                break
            if choice in ("a", "accept", ""):
                break

        if narrative_text is None:
            continue

        prompt = build_dialogue_prompt(
            context_packet=context_packet,
            dialogue_schema_text=dialogue_schema_text,
            story_excerpt=story_excerpt,
            narrative_text=narrative_text,
        )

        if narrative_done == 0:
            avg_seconds = 600.0
        else:
            avg_seconds = (time.perf_counter() - start_time) / narrative_done
        remaining = max(total_targets - narrative_done, 0) * avg_seconds
        percent = int((narrative_done / total_targets) * 100) if total_targets else 100
        eta = format_eta(remaining)
        print(
            f"Requesting LLM to generate dialogue for {scene_id}... "
            f"[{percent:02d}% - {eta} left]"
        )
        if args.debug == 6:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(prompt)
                handle.write("\n")
            print("Wrote debug.log with first dialogue JSON prompt.")
            raise SystemExit(0)
        response = client.responses.create(
            model=args.dialogue_model,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            reasoning={"effort": "high"},
            text={"format": {"type": "json_object"}},
        )
        raw_text = response.output_text.strip()
        if args.debug == 7:
            with open("debug.log", "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            print("Wrote debug.log with first dialogue JSON response.")
            raise SystemExit(0)
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raw_path = f"{output_path}.raw.txt"
            with open(raw_path, "w", encoding="utf-8") as handle:
                handle.write(raw_text)
                handle.write("\n")
            raise SystemExit(f"Model output for {scene_id} was not valid JSON: {exc}") from exc

        errors = validate_schema(data, dialogue_schema)
        if errors:
            repair_prompt = (
                "The JSON output failed schema validation. Fix the JSON.\n"
                "Return ONLY corrected JSON.\n\n"
                f"Validation errors:\n- " + "\n- ".join(errors) + "\n\n"
                f"Invalid JSON:\n{raw_text}"
            )
            repair_rsp = client.responses.create(
                model=args.dialogue_model,
                input=[{"role": "user", "content": [{"type": "input_text", "text": repair_prompt}]}],
                reasoning={"effort": "high"},
                text={"format": {"type": "json_object"}},
            )
            raw_text = repair_rsp.output_text.strip()
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raw_path = f"{output_path}.repair.raw.txt"
                with open(raw_path, "w", encoding="utf-8") as handle:
                    handle.write(raw_text)
                    handle.write("\n")
                raise SystemExit(f"Repair output for {scene_id} was not valid JSON: {exc}") from exc
            errors = validate_schema(data, dialogue_schema)
            if errors:
                raw_path = f"{output_path}.repair.raw.txt"
                with open(raw_path, "w", encoding="utf-8") as handle:
                    handle.write(raw_text)
                    handle.write("\n")
                raise SystemExit(
                    f"Repair output for {scene_id} still fails validation: {errors}. "
                    f"Raw output saved to {raw_path}"
                )

        write_json(output_path, data)
        graph_id = None
        graphs = data.get("dialogueGraphs") if isinstance(data, dict) else None
        if isinstance(graphs, list) and graphs:
            graph_id = graphs[0].get("id")
        entry = {
            "sceneId": scene_id,
            "screenId": screen_id,
            "dialogueGraphId": graph_id,
            "path": output_path,
            "hash": hashlib.sha256(json.dumps(data, ensure_ascii=True).encode("utf-8")).hexdigest(),
            "model": args.dialogue_model,
            "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        }
        manifest_entries.append(entry)

    manifest_path = os.path.join(args.dialogue_dir, "manifest.json")
    write_json(manifest_path, {"entries": manifest_entries})
    print(f"Wrote {manifest_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", default=os.path.join("story_specific", "story.md"))
    ap.add_argument("--screens", default=os.path.join("story_specific", "screens.json"))
    ap.add_argument("--scenes", default=os.path.join("story_specific_gen", "scenes.json"))
    ap.add_argument("--templates_dir", default="templates")
    ap.add_argument("--out", default=os.path.join("story_specific_gen", "scenes.json"))
    ap.add_argument("--characters_out", default=os.path.join("story_specific_gen", "characters.json"))
    ap.add_argument("--hotspots_out", default=os.path.join("story_specific_gen", "hotspots.json"))
    ap.add_argument("--dialogue_dir", default=os.path.join("story_specific_gen", "dialogue"))
    ap.add_argument("--dialog_style", default=os.path.join("story_specific", "dialog_style.md"))
    ap.add_argument("--dialogue_schema", default=os.path.join("templates", "dialogue.schema.json"))
    ap.add_argument("--dialogue_model", default="gpt-5.2") # gpt-5.2-pro
    ap.add_argument("--debug", type=int, default=0, choices=[0, 1, 2, 3, 4, 5, 6, 7])
    ap.add_argument("--model", default="gpt-5.2") # gpt-5.2-pro
    ap.add_argument("--yolo", action="store_true", help="Auto-accept prompts in interactive steps")
    ap.add_argument(
        "--response_format",
        default="json_object",
        choices=["json_object", "json_schema"],
        help="Structured output mode for the LLM response.",
    )
    ap.add_argument(
        "--schema_path",
        default=os.path.join("templates", "scenes.schema.json"),
        help="JSON schema path when using json_schema response format.",
    )
    args = ap.parse_args()

    for path in [
        args.out,
        args.characters_out,
        args.hotspots_out,
        os.path.join(args.dialogue_dir, "placeholder"),
    ]:
        os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(args.out):
        print(f"{args.out} already exists. Skipping generation.")
    else:
        generate_scenes(args)
        print(f"Wrote {args.out}")

    if os.path.exists(args.characters_out):
        print(f"{args.characters_out} already exists. Skipping generation.")
    else:
        while True:
            character_lines = generate_char_list(args)
            if args.yolo:
                proceed = "y"
            else:
                proceed = input("Ok to proceed? [y]/n ").strip().lower()
            if proceed in ("", "y", "yes"):
                generate_characters_json(args, character_lines)
                break

    if os.path.exists(args.hotspots_out):
        print(f"{args.hotspots_out} already exists. Skipping generation.")
    else:
        generate_hotspots_json(args)

    assign_characters_to_scenes(args)
    generate_dialogue_for_scenes(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
