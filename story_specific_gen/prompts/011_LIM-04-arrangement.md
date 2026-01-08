Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior library-like
- camera: 2D side-on; shallow depth; stacks as background geometry
- composition: Catalog terminal is foreground; stacks form a structured backdrop; rules placards dominate the wall space
- important details: Catalog terminal with barcode scan bed; Index rules placards and filing constraints; Drawer bank for legacy records and reels; Quiet service bell with a single instruction label

## HOTSPOTS
- Catalog terminal
- Index rules placards
- Legacy drawer bank

## SCREENS TRANSITION POINTS
- Concourse Service Corridors and Map Kiosk
  - Direction: left
  - Transition: exit_to_corridors
- Intake Hall (Windows 0/3/5/7/9)
  - Direction: onscreen
  - Transition: door_to_intake_hall
- Interconnection Maintenance Triage
  - Direction: right
  - Transition: back_corridor_to_triage
