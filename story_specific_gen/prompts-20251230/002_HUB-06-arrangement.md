Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior wide
- camera: 2D side-on; shallow depth; clear read of kiosk and doors
- composition: Reads as 1979 office construction directly behind the guard shack: beige panels, narrow windows, older fixtures. Door layout is simple and legible: kiosk centered; doors to Records hall, Conference, and Notices room are spaced as distinct exits.
- important details: Lobby kiosk with limited menu and barcode scanner; reception desk nook with drawers and phone list board; door frames labeled with inconsistent terminology; faded asset plaque partially covered by contractor stickers; 1979 office finishes with dated fluorescent troffers; later safety placards/newer label stock over older signage.

## HOTSPOTS
- Lobby kiosk terminal
- Wall phone list
- Faded asset plaque
- Reception desk drawers

## SCREENS TRANSITION POINTS
- Guard Shack Vestibule
  - Direction: left
  - Transition: walk_back_to_shack
- Records Hallway and Tube Station
  - Direction: forward
  - Transition: walk_to_records_hall
- Conference Room (EAC Liaison)
  - Direction: right
  - Transition: enter_conference_room
- Non-Local Compliance Notices Room
  - Direction: down
  - Transition: enter_notices_room
