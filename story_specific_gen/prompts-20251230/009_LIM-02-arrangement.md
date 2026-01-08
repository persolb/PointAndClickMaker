Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior wide service counter
- camera: 2D side-on; shallow depth; counters spaced as distinct hotspots
- composition: Five windows line the back wall; each has a unique icon and a stamp tray; forms rack sits midground as the puzzle resource
- important details: Service counter wall with five windows and queue markings; forms rack with incident templates and category sheets; stamp trays and badge sleeve dispenser; small side kiosk that prints TER and routing slips

## HOTSPOTS
- Window 0 (Intake/TER) - central
- Window 3 (Orientation)
- Window 5 (Safety)
- Window 7 (Property)
- Window 9 (Complaints)
- Forms rack

## SCREENS TRANSITION POINTS
- Overflow Intake Concourse
  - Direction: left
  - Transition: return_to_concourse
- Concourse Service Corridors and Map Kiosk
  - Direction: onscreen
  - Transition: back_corridor_access
- Legacy Interconnections Archive
  - Direction: right
  - Transition: door_to_archive
