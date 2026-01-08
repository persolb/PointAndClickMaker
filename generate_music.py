#!/usr/bin/env python3
"""
Generate structured music JSON via LLM, validate/lint it, and render MIDI.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo
from openai import OpenAI

def load_json(path: Path):
    return json.loads(path.read_text())


def _prune_keys(data, keys_to_remove):
    return {key: value for key, value in data.items() if key not in keys_to_remove}


def fingerprint() -> str:
    tempo_bucket = random.choice(["92", "96", "100", "104", "108"])
    groove_family = random.choice(
        [
            'quarter-hat office tick (no 8th hats)',
            '8th-hat procedural (current default)',
            'no-hat / rim-grid (rim carries subdivision)',
            'half-time brush (snare feel moves later)',
        ]
    )
    bass_motion = random.choice(
        [
            "pedal (whole-bar holds, dur=4)",
            "two-step (your current dur=2 + dur=2)",
            "syncopated (attacks at 1.5 and 3.0, short)",
            "walking-lite (quarters, very low velocity)",
        ]
    )
    ep_rhythm = random.choice(
        [
            "on-beat stabs (0 and 2)",
            "off-beat stabs (0.5 and 2.5)",
            "single-hit bars (only beat 0, dur≈3–4)",
            "sparse counterline (single notes, not dyads)",
        ]
    )
    motif_methods = [
        "EP inner voice fragment",
        "pad/vibes fragment (short notes, very low vel)",
        "bass upper-register fragment (very low vel)",
    ]
    first_motif = random.choice(motif_methods)
    remaining = [m for m in motif_methods if m != first_motif]
    second_motif = random.choice(remaining)
    extra_constraint = random.choice(
        [
            "In this cue, at least 8 of 16 bars must have EP attacks that are not on beats 0 or 2 (use 0.5/1/1.5/2.5/3/3.5).",
            "In this cue, at least 4 bars must have bass as a single dur=4 pedal (not the beat-0/beat-2 split).",
            "",
        ]
    )
    return "\n".join(
        [
            "CUE_FINGERPRINT (MUST FOLLOW)",
            f"Tempo bucket: {tempo_bucket}",
            f"Groove family (drums): {groove_family}",
            f"Bass motion: {bass_motion}",
            f"EP rhythm role: {ep_rhythm}",
            "Motif hiding place: "
            + random.choice(
                [
                    "Cue A: motif fragment inside EP (low velocity, not top note)",
                    "Cue B: motif fragment as pad short notes (not a sustained dyad)",
                    "Cue C: motif fragment as vibes ticks (two notes here, two later)",
                ]
            ),
            "Motif embedding method (must occur twice, must differ):",
            f"- {first_motif}",
            f"- {second_motif}",
            extra_constraint,
        ]
    )


def apply_fingerprint(style_text: str) -> str:
    lines = style_text.splitlines()
    replacement = fingerprint()
    return "\n".join(replacement if line.strip() == "{fingerprint}" else line for line in lines)


@dataclass(frozen=True)
class InstrumentDef:
    channel_1to16: int
    program: Optional[int]


def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(x)))


def beats_to_ticks(beats: float, tpq: int) -> int:
    return int(round(float(beats) * tpq))


def validate_doc(doc: Dict[str, Any]) -> None:
    if doc.get("format") != "pattern-midi.v1":
        raise ValueError("Expected format == 'pattern-midi.v1'")

    g = doc.get("global", {})
    required_global = {"ticks_per_quarter", "time_signature", "tempo_bpm", "bars", "loop"}
    missing = required_global - set(g.keys())
    if missing:
        raise ValueError(f"Missing global keys: {sorted(missing)}")

    ts = g["time_signature"]
    if not (isinstance(ts, list) and len(ts) == 2):
        raise ValueError("global.time_signature must be [numerator, denominator]")
    num, den = int(ts[0]), int(ts[1])
    if den not in (1, 2, 4, 8, 16, 32):
        raise ValueError("time_signature denominator must be power-of-two (1,2,4,8,16,32)")

    if "instruments" not in doc or not isinstance(doc["instruments"], dict) or not doc["instruments"]:
        raise ValueError("instruments must be a non-empty object")
    if "patterns" not in doc or not isinstance(doc["patterns"], dict) or not doc["patterns"]:
        raise ValueError("patterns must be a non-empty object")
    if "arrangement" not in doc or not isinstance(doc["arrangement"], list) or not doc["arrangement"]:
        raise ValueError("arrangement must be a non-empty array")

    for pid, pat in doc["patterns"].items():
        dg = pat.get("drum_grid")
        if dg is None:
            continue
        res = int(dg["resolution"])
        lanes = dg.get("lanes", {})
        for lname, lane in lanes.items():
            steps = lane.get("steps")
            if not isinstance(steps, list):
                raise ValueError(f"pattern '{pid}' lane '{lname}': steps must be an array")
        has_notes = isinstance(pat.get("notes"), list) and len(pat.get("notes")) > 0
        has_drum = pat.get("drum_grid") is not None
        if not (has_notes or has_drum):
            raise ValueError(f"pattern '{pid}': must include 'notes' or 'drum_grid'")

    inst_ids: Set[str] = set(doc["instruments"].keys())
    for pid, pat in doc["patterns"].items():
        for n in pat.get("notes", []) or []:
            inst = n.get("instrument")
            if inst not in inst_ids:
                raise ValueError(f"pattern '{pid}': note references unknown instrument '{inst}'")


def build_instruments(doc: Dict[str, Any]) -> Dict[str, InstrumentDef]:
    insts: Dict[str, InstrumentDef] = {}
    for inst_id, d in doc["instruments"].items():
        ch = int(d["channel"])
        if not (1 <= ch <= 16):
            raise ValueError(f"instrument '{inst_id}': channel must be 1..16")
        prog = d.get("program", None)
        if prog is None:
            insts[inst_id] = InstrumentDef(channel_1to16=ch, program=None)
        else:
            insts[inst_id] = InstrumentDef(channel_1to16=ch, program=clamp_int(int(prog), 0, 127))
    return insts


def expand_events(
    doc: Dict[str, Any],
) -> Tuple[int, int, Tuple[int, int], Dict[int, List[Tuple[int, Message]]]]:
    """
    Returns:
      tpq, loop_end_tick, (ts_num, ts_den), events_by_channel0
    """
    g = doc["global"]
    tpq = int(g["ticks_per_quarter"])
    ts_num, ts_den = int(g["time_signature"][0]), int(g["time_signature"][1])
    bars = int(g["bars"])

    beats_per_bar = ts_num
    loop_end_tick = beats_to_ticks(bars * beats_per_bar, tpq)

    insts = build_instruments(doc)
    patterns = doc["patterns"]

    events_by_ch: Dict[int, List[Tuple[int, Message]]] = {}

    def add(abs_tick: int, msg: Message) -> None:
        events_by_ch.setdefault(msg.channel, []).append((abs_tick, msg))

    for a in doc["arrangement"]:
        if bool(a.get("mute", False)):
            continue
        pat_id = a["pattern"]
        if pat_id not in patterns:
            raise ValueError(f"arrangement references unknown pattern '{pat_id}'")

        pat = patterns[pat_id]
        pat_len_beats = float(pat["length_beats"])
        start_bar = int(a["start_bar"])
        repeat = int(a["repeat"])
        transpose = int(a.get("transpose", 0))
        vel_scale = float(a.get("velocity_scale", 1.0))

        for r in range(repeat):
            bar = start_bar + r
            if bar >= bars:
                break
            start_beat_global = bar * beats_per_bar

            for n in pat.get("notes", []) or []:
                inst = insts[n["instrument"]]
                ch0 = inst.channel_1to16 - 1
                pitch = clamp_int(int(n["pitch"]) + transpose, 0, 127)
                vel = clamp_int(int(round(int(n["vel"]) * vel_scale)), 1, 127)

                on_tick = beats_to_ticks(start_beat_global + float(n["beat"]), tpq)
                off_tick = beats_to_ticks(start_beat_global + float(n["beat"]) + float(n["dur"]), tpq)

                add(on_tick, Message("note_on", channel=ch0, note=pitch, velocity=vel, time=0))
                add(off_tick, Message("note_off", channel=ch0, note=pitch, velocity=0, time=0))

            dg = pat.get("drum_grid")
            if dg:
                res = int(dg["resolution"])
                swing = float(dg.get("swing", 0.0))
                drum_ch0 = 9

                for lane in dg["lanes"].values():
                    pitch = clamp_int(int(lane["pitch"]), 0, 127)
                    base_vel = clamp_int(int(round(int(lane["vel"]) * vel_scale)), 1, 127)
                    steps = lane["steps"]
                    lane_res = len(steps) if len(steps) > 0 and len(steps) != res else res
                    step_beats = pat_len_beats / lane_res

                    for i, on in enumerate(steps):
                        if not on:
                            continue
                        swing_off = (swing * step_beats) if (i % 2 == 1) else 0.0
                        t = beats_to_ticks(start_beat_global + i * step_beats + swing_off, tpq)

                        add(t, Message("note_on", channel=drum_ch0, note=pitch, velocity=base_vel, time=0))
                        add(t + 1, Message("note_off", channel=drum_ch0, note=pitch, velocity=0, time=0))

    used_ch = sorted(events_by_ch.keys())
    for ch0 in used_ch:
        if ch0 == 9:
            continue
        prog: Optional[int] = None
        for inst in insts.values():
            if inst.channel_1to16 - 1 == ch0 and inst.program is not None:
                prog = inst.program
                break
        if prog is not None:
            add(0, Message("program_change", channel=ch0, program=prog, time=0))

    return tpq, loop_end_tick, (ts_num, ts_den), events_by_ch


def write_midi(
    doc: Dict[str, Any],
    out_path: str,
    *,
    force: bool = False,
    nolint: bool = False,
) -> None:
    validate_doc(doc)

    # --- Musical lint pass ---
    if not force and not nolint:
        passed = music_lint(doc)
        if not passed:
            raise ValueError("Music lint failed; MIDI generation aborted.")

    g = doc["global"]
    tempo_bpm = float(g["tempo_bpm"])

    tpq, loop_end_tick, (ts_num, ts_den), events_by_ch = expand_events(doc)

    mid = MidiFile(type=1, ticks_per_beat=tpq)

    conductor = MidiTrack()
    conductor.append(MetaMessage("track_name", name="Conductor", time=0))
    conductor.append(
        MetaMessage(
            "time_signature",
            numerator=ts_num,
            denominator=ts_den,
            clocks_per_click=24,
            notated_32nd_notes_per_beat=8,
            time=0,
        )
    )
    conductor.append(MetaMessage("set_tempo", tempo=bpm2tempo(tempo_bpm), time=0))
    conductor.append(MetaMessage("end_of_track", time=loop_end_tick))
    mid.tracks.append(conductor)

    for ch0 in sorted(events_by_ch.keys()):
        tr = MidiTrack()
        tr.append(MetaMessage("track_name", name=f"Ch{ch0+1}", time=0))

        evs = events_by_ch[ch0]
        evs.sort(key=lambda x: x[0])

        last = 0
        for abs_tick, msg in evs:
            msg.time = abs_tick - last
            tr.append(msg)
            last = abs_tick

        tr.append(MetaMessage("end_of_track", time=max(0, loop_end_tick - last)))
        mid.tracks.append(tr)

    mid.save(out_path)


def save_music_response(
    *,
    doc: Dict[str, Any],
    screen_id: str,
    force: bool,
    nolint: bool,
) -> None:
    out_dir = Path("story_specific_gen/music")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{screen_id}.json"
    midi_path = out_dir / f"{screen_id}.mid"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True))
    write_midi(doc, str(midi_path), force=force, nolint=nolint)


def remake_midi_from_json(*, json_path: Path, force: bool, nolint: bool) -> None:
    doc = json.loads(json_path.read_text())
    midi_path = json_path.with_suffix(".mid")
    write_midi(doc, str(midi_path), force=force, nolint=nolint)


def build_concat(style_text, schema_text, screen, scenes):
    style_text = apply_fingerprint(style_text)
    screen_pruned = _prune_keys(
        screen,
        {
            "key_props",
            "hotspots",
            "connections",
        },
    )
    scenes_pruned = [
        _prune_keys(
            scene,
            {
                "triggerLogic",
                "characters",
                "possibleOutcomes",
            },
        )
        for scene in scenes
    ]
    screen_block = json.dumps(screen_pruned, indent=2, ensure_ascii=True)
    scenes_block = json.dumps(scenes_pruned, indent=2, ensure_ascii=True)
    return "\n\n".join(
        [
            style_text.strip(),
            schema_text.strip(),
            "## 9. Set The Scene\n\n This is the screen/scene you need to create the midi for.",
            "SCREEN_JSON:",
            screen_block,
            "SCENES_JSON:",
            scenes_block,
            "",
        ]
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build concatenated music prompts for each screen."
    )
    parser.add_argument(
        "--debug",
        type=int,
        default=None,
        help=(
            "If set, target the nth concat. --debug 1 writes prompt to debug.log then "
            "requests OpenAI; --debug 2 saves the OpenAI response to debug.log."
        ),
    )
    parser.add_argument(
        "--model",
        default="gpt-5.2",
        help="OpenAI model to use for debug prompt requests.",
    )
    parser.add_argument(
        "--screen",
        default=None,
        help="If set, only process the screen with this id.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass music lint failures when writing MIDI.",
    )
    parser.add_argument(
        "--nolint",
        action="store_true",
        help="Skip music lint entirely.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry count when music lint fails.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="If set, use this text as the full prompt (ignores style/screens/scenes).",
    )
    return parser.parse_args()


def request_structured_music_response(
    *,
    model: str,
    prompt_text: str,
) -> Dict[str, Any]:
    # Call the LLM and parse the structured JSON response.
    system_text = "Return JSON only."
    client = OpenAI()
    rsp = client.responses.create(
        model=model,
        reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt_text}]},
        ],
        text={"format": {"type": "json_object"}},
    )
    raw_text = rsp.output_text.strip()
    return json.loads(raw_text)


def choose_best_music_variant(prompt_text: str, variants: List[Dict[str, Any]], model: str) -> int:
    # Ask the LLM to pick the strongest candidate when multiple variants exist.
    count = len(variants)
    auto_prompt = (
        "Choose the best music JSON for this screen based on the prompt. "
        "Return only a single digit. No JSON, no prose.\n"
        f"Valid answers: 1 through {count}."
    )
    content = [
        {"type": "input_text", "text": prompt_text},
        {"type": "input_text", "text": auto_prompt},
    ]
    for idx, variant in enumerate(variants, start=1):
        variant_text = json.dumps(variant, indent=2, ensure_ascii=True)
        content.append(
            {"type": "input_text", "text": f"Variant {idx}:\n{variant_text}"}
        )
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
    raise SystemExit(f"Auto selection failed: {choice}")

def music_lint(doc: Dict[str, Any]) -> bool:
    # Basic rule checks for tempo, channels, and event validity.
    """
    Lints a pattern-midi.v1 JSON doc.

    Prints:
      - "Music lint passed." or "Music lint failed."
      - then a bullet list of issues (if any)

    Returns:
      bool (passed)
    """

    issues: List[str] = []

    # ----------------------------
    # Helpers
    # ----------------------------
    def _get(d: Dict[str, Any], path: List[str], default=None):
        cur: Any = d
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def _is_pitched_channel(ch_1to16: int) -> bool:
        # MIDI channel 10 is drums => 1-based 10
        return int(ch_1to16) != 10

    def _likely_hat_lane(lane_name: str, pitch: int) -> bool:
        n = (lane_name or "").lower()
        if "hat" in n or "hh" in n:
            return True
        # Common GM hat pitches: 42 closed, 44 pedal, 46 open
        return int(pitch) in (42, 44, 46)

    def _is_ep_instrument(inst_id: str) -> bool:
        s = (inst_id or "").lower()
        return any(k in s for k in ("ep", "e_p", "epiano", "electricpiano", "rhodes"))

    # ----------------------------
    # Basic reads
    # ----------------------------
    g = doc.get("global", {})
    bars = int(g.get("bars", 0) or 0)
    ts = g.get("time_signature", [4, 4])
    beats_per_bar = int(ts[0]) if isinstance(ts, list) and len(ts) >= 1 else 4

    patterns: Dict[str, Any] = doc.get("patterns", {}) or {}
    instruments: Dict[str, Any] = doc.get("instruments", {}) or {}
    arrangement: List[Dict[str, Any]] = doc.get("arrangement", []) or []

    if bars <= 0:
        issues.append("Structural Coverage: global.bars missing or invalid.")
        _print_result(False, issues)
        return False

    # Build sets of pitched instruments (by id) for “all instruments play all bars” check
    pitched_insts: Set[str] = set()
    for inst_id, idef in instruments.items():
        ch = idef.get("channel")
        if ch is None:
            continue
        if _is_pitched_channel(int(ch)):
            pitched_insts.add(inst_id)

    # ----------------------------
    # Collect per-bar stats
    # ----------------------------
    # For each bar:
    # - earliest_event_beat: min beat of any note/drum hit within that bar (0..beats_per_bar)
    # - pitched_layers: set of pitched instrument IDs with any note-on in that bar
    # - drum_hit_count: number of drum hits in that bar
    # - hat_dense: True if hat lane has 8ths/16ths density (heuristic)
    # - ep_dense: True if EP is running continuous 8ths/16ths (heuristic)
    earliest_event_beat: List[float | None] = [None] * bars
    pitched_layers: List[Set[str]] = [set() for _ in range(bars)]
    drum_hit_count: List[int] = [0] * bars
    hat_dense: List[bool] = [False] * bars
    ep_dense: List[bool] = [False] * bars

    # Track which pitched instruments appear in which bars (for “all instruments play all bars”)
    inst_bars: Dict[str, Set[int]] = {iid: set() for iid in pitched_insts}

    # Iterate arrangement and expand events at bar-level
    for a in arrangement:
        if bool(a.get("mute", False)):
            continue

        pat_id = a.get("pattern")
        if not pat_id or pat_id not in patterns:
            issues.append(f"Structural Coverage: arrangement references unknown pattern '{pat_id}'.")
            continue

        pat = patterns[pat_id]
        pat_len_beats = float(pat.get("length_beats", 0) or 0)
        if pat_len_beats <= 0:
            issues.append(f"Structural Coverage: pattern '{pat_id}' has invalid length_beats.")
            continue

        start_bar = int(a.get("start_bar", 0) or 0)
        repeat = int(a.get("repeat", 1) or 1)

        # Expand repeats bar-by-bar
        for r in range(repeat):
            bar = start_bar + r
            if not (0 <= bar < bars):
                continue

            # Notes (pitched)
            notes = pat.get("notes") or []
            if isinstance(notes, list):
                # EP density heuristic within this bar: count of short notes on EP
                ep_short_count = 0

                for n in notes:
                    if not isinstance(n, dict):
                        continue
                    inst_id = n.get("instrument")
                    if not inst_id:
                        continue

                    beat = float(n.get("beat", 0) or 0)
                    dur = float(n.get("dur", 0) or 0)

                    # Earliest event beat for this bar
                    cur_min = earliest_event_beat[bar]
                    if cur_min is None or beat < cur_min:
                        earliest_event_beat[bar] = beat

                    # Pitched layer accounting (exclude drums by channel)
                    idef = instruments.get(inst_id, {})
                    ch = idef.get("channel")
                    if ch is not None and _is_pitched_channel(int(ch)):
                        pitched_layers[bar].add(inst_id)
                        if inst_id in inst_bars:
                            inst_bars[inst_id].add(bar)

                        # EP dense heuristic: many short notes
                        if _is_ep_instrument(inst_id) and dur > 0 and dur <= 0.5:
                            ep_short_count += 1

                # Flag EP dense if it looks like continuous 8ths/16ths (heuristic)
                if ep_short_count >= 6:
                    ep_dense[bar] = True

            # Drum grid hits
            dg = pat.get("drum_grid")
            if isinstance(dg, dict) and dg:
                res = int(dg.get("resolution", 0) or 0)
                if res > 0:
                    step_beats = pat_len_beats / float(res)
                    lanes = dg.get("lanes") or {}
                    if isinstance(lanes, dict):
                        for lname, lane in lanes.items():
                            if not isinstance(lane, dict):
                                continue
                            lpitch = int(lane.get("pitch", 0) or 0)
                            steps = lane.get("steps") or []
                            if not isinstance(steps, list):
                                continue

                            # Hat density heuristic: many hits across the bar
                            lane_hits = 0

                            for i, on in enumerate(steps):
                                if int(on) != 1:
                                    continue
                                lane_hits += 1
                                drum_hit_count[bar] += 1

                                hit_beat = float(i) * step_beats
                                cur_min = earliest_event_beat[bar]
                                if cur_min is None or hit_beat < cur_min:
                                    earliest_event_beat[bar] = hit_beat

                            if _likely_hat_lane(str(lname), lpitch):
                                # Heuristic: >=8 hits in a bar implies 8ths/16ths presence
                                if lane_hits >= 8:
                                    hat_dense[bar] = True

    # ----------------------------
    # 1) Structural Coverage Lint
    # ----------------------------
    # Every bar must have at least one event within first 2 beats
    for b in range(bars):
        t = earliest_event_beat[b]
        if t is None or t > 2.0:
            issues.append(f"Structural Coverage: bar {b} has no event within first 2 beats.")

    # ----------------------------
    # 2) Density & Energy Lint
    # ----------------------------
    busy_bars = 0
    change_bars = 0

    prev_stack = None
    prev_drum_hits = None

    for b in range(bars):
        stack = pitched_layers[b]
        drums = drum_hit_count[b]

        if len(stack) >= 2:
            busy_bars += 1

        if prev_stack is not None:
            if stack != prev_stack or drums != prev_drum_hits:
                change_bars += 1

        prev_stack = stack
        prev_drum_hits = drums

    # Heuristic thresholds (tuned for your “underscore” target)
    if busy_bars < 4:
        issues.append(f"Density/Energy: only {busy_bars} bars have ≥2 pitched layers (target ≥4).")

    if change_bars < 3:
        issues.append(f"Density/Energy: only {change_bars} bars show any change vs prior bar (target ≥3).")

    # ----------------------------
    # 6) Rhythm Interaction Lint
    # ----------------------------
    for b in range(bars):
        if hat_dense[b] and ep_dense[b]:
            issues.append(
                f"Rhythm Interaction: bar {b} has dense hats and dense EP (masking risk)."
            )

    # ----------------------------
    # 8) Red-Flag Summary (Auto-Reject)
    # ----------------------------
    # Fewer than 3 bars contain any change (stronger than above: use change_bars)
    if change_bars < 3:
        issues.append("Auto-Reject: cue is nearly identical bar-to-bar (<3 change bars).")

    # All pitched instruments play in all bars
    if pitched_insts:
        all_play_all = True
        missing_detail: List[str] = []
        for iid in sorted(pitched_insts):
            bars_played = inst_bars.get(iid, set())
            if len(bars_played) != bars:
                all_play_all = False
                missing_detail.append(f"{iid} plays {len(bars_played)}/{bars} bars")
        if all_play_all:
            issues.append("Auto-Reject: all pitched instruments play in all bars (no dropouts).")
        else:
            # Not a failure by itself, but helpful diagnostic if you want it:
            # comment out if you prefer quieter output.
            pass

    passed = (len(issues) == 0)
    _print_result(passed, issues)
    return passed


def _print_result(passed: bool, issues: List[str]) -> None:
    if passed:
        print("Music lint passed.", flush=True)
        return
    print("Music lint failed.", flush=True)
    for msg in issues:
        print(f"- {msg}", flush=True)



def main():
    args = parse_args()

    schema_path = Path("templates/music.schema.json")
    schema_text = schema_path.read_text()
    if args.prompt is None:
        style_path = Path("story_specific/music_style.md")
        screens_path = Path("story_specific/screens.json")
        scenes_path = Path("story_specific_gen/scenes.json")

        style_text = style_path.read_text()
        screens_data = load_json(screens_path)
        scenes_data = load_json(scenes_path)

        screens = screens_data.get("screens", [])
        scenes = scenes_data.get("scenes", [])
    else:
        screens = [{"id": args.screen or "prompt"}]
        scenes = []
        style_text = ""

    debug_target = args.debug if args.debug is not None else None
    concat_index = 0

    for screen in screens:
        screen_id = screen.get("id")
        if args.screen is not None and screen_id != args.screen:
            continue
        if args.prompt is None:
            applicable_scenes = [s for s in scenes if s.get("screenId") == screen_id]
            concat_text = build_concat(style_text, schema_text, screen, applicable_scenes)
        else:
            concat_text = "\n\n".join(
                [
                    "You are composing procedural game music for a point-and-click adventure.",
                    "Return ONLY valid JSON that conforms to schema: \"pattern-midi.v1\".",
                    "Do NOT output MIDI bytes, audio, prose, commentary, markdown, or explanations.",
                    "Output MUST be a compact pattern library + explicit arrangement (do NOT dump an entire song into one giant pattern).",
                    "This music must function as a **loopable underscore** that supports player problem-solving and dialogue, not a linear cue.",
                    schema_text.strip(),
                    "\n\n",
                    args.prompt.strip(),
                ]
            )
        concat_index += 1

        if debug_target == 1:
            Path("debug.log").write_text(concat_text)
            return

        screen_label = screen_id or "screen"
        json_path = Path("story_specific_gen/music") / f"{screen_label}.json"
        midi_path = Path("story_specific_gen/music") / f"{screen_label}.mid"
        if json_path.exists():
            if midi_path.exists():
                return
            try:
                remake_midi_from_json(json_path=json_path, force=args.force, nolint=args.nolint)
                return
            except ValueError as exc:
                if "Music lint failed" not in str(exc) or args.force:
                    raise
                print(
                    f"Music lint failed for {screen_label}; retrying generation.",
                    flush=True,
                )

        if debug_target == 2:
            response_doc = request_structured_music_response(
                model=args.model,
                prompt_text=concat_text,
            )
            Path("debug.log").write_text(json.dumps(response_doc, indent=2, ensure_ascii=True))
            return

        variants = 3
        max_retries = max(1, int(args.retries))
        variant_docs: List[Optional[Dict[str, Any]]] = [None] * variants
        pending = set(range(variants))
        attempt = 0

        while pending and attempt < max_retries:
            attempt += 1
            with concurrent.futures.ThreadPoolExecutor(max_workers=variants) as executor:
                futures = {
                    executor.submit(
                        request_structured_music_response,
                        model=args.model,
                        prompt_text=concat_text,
                    ): idx
                    for idx in pending
                }
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    doc = future.result()
                    try:
                        if not args.force and not args.nolint:
                            if not music_lint(doc):
                                raise ValueError("Music lint failed; MIDI generation aborted.")
                        variant_docs[idx] = doc
                        pending.discard(idx)
                        variant_path = Path("story_specific_gen/music") / f"{screen_label}-v{idx+1}.json"
                        variant_path.parent.mkdir(parents=True, exist_ok=True)
                        variant_path.write_text(json.dumps(doc, indent=2, ensure_ascii=True))
                    except ValueError:
                        continue

        if pending:
            raise ValueError(f"Music lint failed after {max_retries} attempts for {screen_label}.")

        best_idx = choose_best_music_variant(concat_text, variant_docs, args.model)
        best_doc = variant_docs[best_idx - 1]
        if best_doc is None:
            raise SystemExit(f"Auto selection failed: variant {best_idx} missing")

        save_music_response(
            doc=best_doc,
            screen_id=screen_label,
            force=args.force,
            nolint=args.nolint,
        )
        for idx in range(variants):
            variant_path = Path("story_specific_gen/music") / f"{screen_label}-v{idx+1}.json"
            if variant_path.exists():
                variant_path.unlink()


if __name__ == "__main__":
    main()
