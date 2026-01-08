Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior medium
- camera: 2D side-on; shallow depth; wall text as texture, not readable blocks
- composition: A dense wall of notices forms the backdrop; one clean forms rack and a sealed envelope stand out as interactable
- important details: Room lined with generic compliance notices and postings; forms racks with standardized templates; stack of preprinted envelopes and routing sleeves; one notice set that looks out-of-place but is filed as ordinary

## HOTSPOTS
- Forms rack
- Envelope stack
- Notice (unfamiliar barcode format)

## SCREENS TRANSITION POINTS
- Admin Lobby and Kiosk
  - Direction: onscreen
  - Transition: exit_to_lobby
- Copy and Fax Room
  - Direction: left
  - Transition: back_corridor_to_copy_room
