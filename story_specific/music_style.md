You are composing procedural game music for a point-and-click adventure.

Return ONLY valid JSON that conforms to schema: "pattern-midi.v1".
Do NOT output MIDI bytes, audio, prose, commentary, markdown, or explanations.
Output MUST be a compact pattern library + explicit arrangement (do NOT dump an entire song into one giant pattern).

This music must function as a **loopable underscore** that supports player problem-solving and dialogue, not a linear cue.

## 0. Critical Format Rules (MUST FOLLOW)

0) Be creative to make a good product... that's more important than any rule.

1) Pattern size discipline
- Every pattern MUST be 4 or 8 beats long (1–2 bars in 4/4).
- No pattern may exceed 8 beats.
- Patterns are reusable building blocks; keep them short.

2) Arrangement-first structure
- The final loop MUST be built by placing patterns via `arrangement`.
- `arrangement` MUST contain at least 12 entries.
- Use at least 6 distinct pattern IDs in `arrangement`.
- Arrangement math
  - start_bar is 0–15 (16-bar loop).
  - repeat means “repeat this pattern back-to-back.”
  - Pattern length in bars = length_beats / 4.
  - Coverage rule: start_bar + repeat * (length_beats/4) <= 16.
  - If length_beats = 8, then start_bar must be even.
- If used, motif interventions must follow these forms:
  - ep_*_MA (Motif A substitutes EP hits for 1 bar)
  - pad_*_MA (pad borrowed-color dyad for 1 bar)
  - dr_*_MB (Motif B “competence stamp”: remove one rim hit in that bar)
  - hat_*_MC or vibes_*_MC (one-bar 1/8 late shift, then revert)
- Include Motif A as a buried fragment at least twice in the 16 bars:
  - Each time: 2–5 notes, stepwise, low velocity, integrated into EP or vibes/pad.
  - It must not become the lead melody.
  - It must occur in two different octaves or transpositions.
- Loop
  - Bar 15 must not introduce new material.
  - No final-bar cadence gestures.
  - If harmony changes in bar 15, it must be a “setup” that feels natural when returning to bar 0.
- Asymmetry rule (anti-mechanical):
  - At least once per cue, allow either the bass or the EP to hold the same harmony across two consecutive bars while the other changes.
  - This hold must not create silence and must not coincide with bar 15.
- Choose exactly one variation lever for this cue:
  - Lever R (Rhythm): shift the drum pattern’s backbeat by 1/16 for 1 bar (not bar 15).
  - Lever H (Harmony): use one borrowed-color dyad in 1 bar (not bar 15).
  - Lever T (Texture): drop bass for 1 bar and let EP carry time.
  - Lever P (Phrase): repeat bar 7 exactly (one-bar ‘paperwork loop’).
  - Classical abstraction lever:
    - Choose one public-domain classical source and borrow exactly one abstraction from it (interval contour, sequence logic, voice-leading behavior, rhythmic grid, or register choreography).
    - Do not quote melody or harmony directly.
    - The reference must remain unrecognizable without analysis and must not replace the buried motif.
  - Place the variation lever in a bar that is not the same bar as a motif occurrence.
  - If the chosen variation lever is not Texture, still include one bar (not bar 15) where either the bass or the EP is absent entirely, while drums continue.
  
3) Drum grid correctness
- For each `drum_grid`, every lane’s `steps` array length MUST equal `drum_grid.resolution`.
- Prefer `resolution` = 16 for 4/4.

4) Repetition rule
- If a pattern repeats more than 4 times, either alternate A/B or mute one layer for 1 bar somewhere in the middle.
  Example: `pulse_A` and `pulse_B`, with arrangement alternating A/B every 2–4 bars.

5) Notes vs. drums
- Use `notes` for pitched instruments.
- Use `drum_grid` for drums (channel 10).
- Do not represent drums as long note lists.

6) Dynamics
- Flat dynamics. No crescendos, no swells.
- Typical velocity bands:
  - pad: 20–32
  - bass: 32–48
  - epiano/vibes: 36–56
  - brass hits: 52–68 (rare)
  
7) Density budget
- At any bar, use at most 3 active pitched layers simultaneously (e.g., epiano + bass + vibes).
- The pad counts as a pitched layer only when present.
- Brass is never sustained and may occur in at most 2 bars per 16-bar loop.
- If drums include hat 8ths/16ths, then the epiano must avoid continuous 8ths/16ths in that same bar.

- Per bar, across all pitched layers combined: max 10 note events.
- EP: max 6 note events per bar.
- Vibes: max 2 note events per bar.
- Bass: max 2 note events per bar (ties allowed).

8) Negative pattern
- Vibraphone patterns may be placed in at most 8 of 16 bars total, unless they are the only melodic layer in those bars.
- No dead bars: Every bar must contain an event within the first 2 beats.

---

{fingerprint}

## 1. Core Musical Identity

**Genre blend**

* Institutional comedy + procedural tension + restrained sci-fi

**MIDI constraint**

* All cues must work as **low-instrument-count MIDI** without relying on timbral realism
* Melody, harmony, and rhythm must carry the intent, not sound design

---

## 2. Instrument Palette

**Core instruments**

* Electric piano (dry, no reverb)
* Muted trumpet or synth brass
* Vibraphone or marimba
* Bass clarinet or low synth pad
* Simple drum kit (kick, rim, brushed snare)

Use exactly: drums + bass + EP + (choose one: pad or vibes) + (optional brass hits).
Do not introduce any other pitched instruments.

---

## 3. Harmonic Language

* Major keys with frequent **minor-iv, flat-VI, or diminished passing tones**
* Avoid V–I cadences and any heroic “arrival.”
* “Machine” feel is allowed: steady cycling, interlocking rhythms, occasional missing beat to imply imperfect response.

---

## 4. Rhythm and Groove Philosophy

* 80–110 BPM for most cues
* Avoid urgency unless the scene insists on it


Examples of the types of music to use could be:
* 80s corporate training video bed: straight 16ths, dry EP stabs, polite pad dyads held slightly too long
* Library-music procedural funk (subtle): tight bass ostinato, clipped chord hits, no swagger
* Deadpan bossa (restrained): brushed kit pattern with understated syncopation and minimal harmony motion
* Hold-music in a waiting room: pleasant loop with a delayed cadence and one “wrong” chord per cycle
* Badge-reader / intercom protocol beeps: sparse vibraphone ticks over static pad, mechanically even pacing
* Minimalist phasing paperwork loop: two simple cells slightly offset so the loop point feels mildly misaligned
* Noir-leaning fluorescent tension: low sustained tones with dry, small voicings and non-functional color chords
* Mechanical maintenance pulse: repeating ostinato with occasional missing beat to imply “systems not quite responding”
* Polite denial waltz: light rim-on-2 feel with careful, clipped motifs and no romantic motion
* After-hours office jazz (sanitized): simple ii–V hints that never resolve, kept deliberately small and flat
* Minimal chamber counterpoint: two dry lines in simple imitation, kept small and emotionally neutral
Early-music processional (sanitized): modal cadence avoidance, steady pulse, no “religious” color
* Classical-era clockwork allegretto: light Alberti-like figures, clipped phrases, deliberate non-resolution
* Klezmer-leaning bureaucratic march (very restrained): minor-tinged turns, dry staccato, no exuberance
* Film-noir “bureaucracy blues” without blues: chromatic passing tones and quiet pedal points, no swing emphasis


---

## 5. Thematic Families (Reusable Motifs)

**Motif-as-operator rule (MANDATORY)**
Motif A must appear as a buried fragment twice.
Motif B and Motif C may intervene at most once each if musically appropriate, but are not required.

An intervention means:
- Rhythmic substitution: replace the epiano chord hits in one bar with Motif A stepwise notes (same rhythmic slots), or
- Harmonic substitution: when Motif A occurs, the pad must switch to a borrowed-color dyad (iv/♭VI/° tone) for that bar, or
- Structural misalignment: Motif C shifts one layer (vibes or hat) late by 1/8 for exactly one bar, then snaps back, or
- Competence stamp: Motif B replaces the brass hit AND forces one drum hit omission (e.g., remove a rim hit) in that bar.

The motifs are:
A) Bureaucracy motif (stepwise, repeats, avoids tonic C):
- C major: E F G F E
- minor-tinged: D Eb F Eb D
Rules: no leaps > whole step; start/end same pitch; avoid tonic.

B) Competence motif (P4/P5 leap, declarative, ends early):
- C major: C G E
- variant: D G F
Rules: one leap at start only; clipped ending; not triumphant.

C) Alignment motif (symmetry, chromatic/whole-tone fragments, misaligned loop):
- C Db D Db C
- variant: C D E D C
Rules: symmetrical; no functional pull; implies continuation.

---

## 6. Tone Mapping by Act

### Act I — “Everything Is Under Control” - in HUB before act II

Tempo 92–108, major key, EP+bass+light kit, pad rare, vibes sparse.

* Dry optimism
* Clean loops
* Almost cheerful

---

### Act II — “Everything Is Working Normally” - in LIM during act II

Tempo ±6 BPM from Act I, add one “wrong chord” per 4 bars, slightly more chromatic passing.

* Same motifs, wrong context
* Familiar themes with altered harmony or tempo

---

### Act III — “Pending Compliance” - after act II, mostly in HUB

Tempo 80–108, fewer drum hits, more space, motif overlaps.

* Slower
* More space
* Motifs overlap and interfere

---

## 7. Comedy Without Gags

No punchlines. Use:
* Chords held slightly too long
* Loop one bar longer than comfortable
* Bring in a counter-melody that sounds helpful but does nothing

---

## 8. Output Requirements (pattern-midi.v1)

Your JSON MUST include:
- `global` with 16 bars, 4/4, tempo 80–110, ticks_per_quarter 480
- `instruments` mapping IDs → {channel, program}
- `patterns` containing:
  - short patterns (4 or 8 beats)
  - A/B alternates for any pattern used >4 times
  - drum patterns expressed with `drum_grid` (steps length equals resolution)
- `arrangement` containing explicit placement and A/B alternation schedule

The schema defines structure only; musical intent and motif behavior should be maximally expressed within that structure.

Return ONLY the JSON object.
