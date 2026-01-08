Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior industrial staging
- camera: 2D side-on; shallow depth; big equipment shapes for readability
- composition: AMR reader loop and dummy load fixture are staged as the main interaction set; aperture access is a clear door
- important details: AMR tag reader loop (physical interface); dummy mass fixture and balancing hardware; resistor bank / test load panel; supervisor station with clipped work orders

## HOTSPOTS
- AMR reader loop
- Dummy mass fixture
- Test load panel
- Supervisor clipboard station

## SCREENS TRANSITION POINTS
- Interconnection Maintenance Triage
  - Direction: down
  - Transition: return_to_triage
- Scheduling Counter (Time-Asset Cage)
  - Direction: left
  - Transition: walk_to_scheduling
- Aperture Bay (Mirror Surface)
  - Direction: onscreen
  - Transition: enter_aperture_bay
