Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior wide civic-industrial
- camera: 2D side-on; shallow depth; concourse depth shown by repeating pillars
- composition: Aperture-bay shutter sits to one side; a kiosk and directional railings guide the player toward Intake Hall
- important details: Clean service concourse with industrial finishes; barcode kiosk labeled for incident processing; directional railings and queue stanchions; sealed bay door leading toward the mirror-surface aperture

## HOTSPOTS
- Barcode kiosk
- Incident queue stanchions
- Sealed bay door

## SCREENS TRANSITION POINTS
- Intake Hall (Windows 0/3/5/7/9)
  - Direction: right
  - Transition: walk_to_intake_hall
- Concourse Service Corridors and Map Kiosk
  - Direction: onscreen
  - Transition: enter_service_corridors
- Aperture Bay (Mirror Surface)
  - Direction: left
  - Transition: secured_aperture_bay_door
