Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: exterior wide establishing
- camera: 2D side-on; shallow depth; mild convergence toward gate
- composition: Primary read: cracked road → chained sliding gate. Secondary reads: guard shack visible inside/right of the fence line; behind it, the admin lobby block (HUB-06) is visible through/behind fence as a 1979 office façade. Foreground includes a flush ground service hatch near the road edge that reads as an intentional access point.
- important details: cracked road leading to chained sliding gate; guard shack visible inside/right of fence line; admin lobby block visible beyond/behind fence; foreground flush ground service hatch near road edge

## HOTSPOTS
- Equipment case - in car trunk (foreground)
- Gate keypad and receipt slot
- Layered signpost
- Debris pile - by fence line
- Instrument conduit box
- Exterior ground service hatch

## SCREENS TRANSITION POINTS
- Guard Shack Exterior
  - Direction: right
  - Transition: walk_along_fence
- Exterior Ground Service Hatch
  - Direction: down
  - Transition: open_ground_service_hatch
- Admin Lobby and Kiosk
  - Direction: onscreen
  - Transition: walk_through_gate
