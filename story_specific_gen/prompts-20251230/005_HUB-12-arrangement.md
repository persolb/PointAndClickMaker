Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior wide overlooking machinery
- camera: 2D side-on; shallow depth; machinery framed as background mass
- composition: Observation window frames the rotating frames and central sphere; test bench and tube tray sit in the foreground
- important details: Observation window and safety rail; three independent rotating frames with mismatched maintenance markings; central sphere (matte in nominal state); reflectivity test bench with calibration puck tray; tube station tray for returned canister event

## HOTSPOTS
- Reflectivity test station
- Calibration puck
- Return canister tray - foreground right
- Observation window latch

## SCREENS TRANSITION POINTS
- Control Room
  - Direction: down
  - Transition: return_to_control_room
- SPINDLE Chamber Floor (Containment Sphere)
  - Direction: onscreen
  - Transition: descend_to_chamber_floor (hotspot forward)
- Records Hallway and Tube Station
  - Direction: left
  - Transition: tube_bay_return_walkway
