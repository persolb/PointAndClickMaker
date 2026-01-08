Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior corridor hub
- camera: 2D side-on; shallow depth; corridor branches clearly readable
- composition: Map kiosk sits center; three corridor doors branch with distinct token slots and signage
- important details: Map kiosk that prints corridor fragments; badge validator pedestal; token-slot doors with simple iconography; service-route reader loop for glove tag

## HOTSPOTS
- Map kiosk
- Badge validator
- Service-route reader loop
- Maintenance triage door

## SCREENS TRANSITION POINTS
- Overflow Intake Concourse
  - Direction: back
  - Transition: return_to_concourse
- Intake Hall (Windows 0/3/5/7/9)
  - Direction: left
  - Transition: return_to_intake_hall
- Scheduling Counter (Time-Asset Cage)
  - Direction: forward
  - Transition: enter_scheduling_counter
- Legacy Interconnections Archive
  - Direction: right
  - Transition: enter_archive
- Interconnection Maintenance Triage
  - Direction: onscreen
  - Transition: enter_maintenance_triage
