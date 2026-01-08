Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior wide
- camera: 2D side-on; shallow depth; consoles layered foreground-to-midground
- composition: Main console is foreground; printer and alarm panel flank it; a single door leads to the observation deck
- important details: Main control console with mismatched labeling; alarm panel triggers load-shedding event; baseline log printer (paper trail); breaker map binder and outdated procedure book

## HOTSPOTS
- Main console
- Alarm panel
- Log printer tray
- Procedure binder

## SCREENS TRANSITION POINTS
- Operations Corridor (Interlocks and LOTO)
  - Direction: left
  - Transition: exit_to_ops_corridor
- SPINDLE Chamber Observation Deck
  - Direction: onscreen
  - Transition: enter_observation_deck
