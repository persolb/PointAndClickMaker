Create an image that is a sketch showing where each hotspot and navigation should be for this point-and-click game screen.

Each screen edge (left, right, top, bottom) may contain at most one exit. An exit is defined as a transition to one and only one adjacent screen. Multiple destinations may not share a single edge, path, or walkable corridor. If multiple narrative destinations exist, they must be distributed across distinct edges or be an onscreen hotspot (like a seperate door/road).

Don't list the same exit or hotspot twice.

Only make a line sketch so an artist has a guideline. No shading, colors, etc.

# PROMPT

## SCREEN ART NOTES
- shot_type: interior workshop
- camera: 2D side-on; shallow depth; bench as interaction plane
- composition: Workbench is foreground; scope board and warranty postings are midground; bay access door is a clear exit
- important details: Workbench with tool fixtures and seal punch; scope tags and warranty postings; registry reader that accepts AMR tag; parts bins labeled by service class, not by function; bay access door reads as an exit

## HOTSPOTS
- AMR registry reader
- Workbench tool fixture
- Scope board

## SCREENS TRANSITION POINTS
- Concourse Service Corridors and Map Kiosk
  - Direction: down
  - Transition: exit_to_corridors
- Interconnection Bay Staging
  - Direction: right
  - Transition: enter_bay_staging (via bay access door)
- Legacy Interconnections Archive
  - Direction: left
  - Transition: return_to_archive
