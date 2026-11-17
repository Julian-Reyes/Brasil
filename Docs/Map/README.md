# Rio de Janeiro open-world map

Design goal: a recognisable, **compressed** Rio in the spirit of Midnight Club: Los Angeles. Neighbourhoods,
landmarks, coastline, mountains and favelas keep their real relative positions and connections, while
distances shrink so driving stays fun (target 8-12 min across most of the map). Buildings, roads, vehicles,
turning radii and gradients stay at **real scale**. Only the connective tissue between places is compressed.

## Two worlds, kept separate

| | SOURCE (geographic, 1:1) | PLAYABLE (compressed) |
|---|---|---|
| Purpose | Ground truth and reference; per-area tests at real scale | The game map |
| Data | `GeoData/` (raw, processed, exports), git-ignored, reproducible | Compressed road graph (Phase 3), hand-edited |
| Frame | EPSG:31983 -> UE source frame (`CoordinateSystem.md`) | Own frame, defined by the compressed graph |
| Unreal | `L_Rio_Test_*` levels, `/Game/Rio/Generated/` | `L_Rio_Blockout` |

## Layout

```
Tools/GeoPipeline/         pipeline code + config (versioned)          -> Tools/GeoPipeline/README.md
GeoData/raw|processed|exports/   downloads and generated meshes (ignored)
Docs/Map/                  this documentation, validation reports, reference maps, screenshots
Content/Rio/
  Maps/L_Rio_Blockout          World Partition, compressed playable map (empty: Phase 3)
  Maps/L_Rio_Test_LeblonVidigal World Partition, 1:1 geographic test (Phase 4), georeferenced
  Generated/<Area>/{Terrain,Roads,Buildings,Water}  pipeline output, overwritten on regeneration
  Materials/Blockout/          flat placeholder materials
  Manual/  Procedural/  Data/  hand-made assets, PCG tools, data assets (never touched by the pipeline)
Content/ThirdPerson/        original template, untouched
```

## Status (2026-11-17)

* [x] Phase 1: project inspected, git + LFS, folders, World Partition levels, Third Person map preserved
* [x] Phase 2: reproducible OSM + Copernicus pipeline, fixed CRS, validation reports, licence records
* [ ] Phase 3: reference map of the full area done (`reference/full.png`); compressed road-graph proposal next
* [x] Phase 4: Leblon / Av. Niemeyer / Vidigal test, built, imported, verified and walkable (`Phase4_TestReport.md`)

**Design priorities:** `DesignPriorities.md`. Hero area 1 is the Leblon to Arpoador beach, protected from
compression and built first.

## Tooling constraints found

* **unreal-mcp** covers asset/actor/property work, mesh import, PIE, viewport capture and line traces. It
  cannot run arbitrary editor Python, create Landscapes or run console commands.
* **Unreal Python Remote Execution** is enabled (by the user; 127.0.0.1 only). The pipeline drives the editor
  with full `unreal` API access through `Tools/GeoPipeline/unreal/ue_remote.py` (includes a macOS multicast
  workaround). Terrain is still a tiled static mesh with per-triangle collision; Landscape import is now possible
  and planned for the full map.
* Machine: Apple M1, 16 GB RAM, about 13 GB free disk. Keep test areas small; the full-city export will need
  per-district streaming and should not be generated in one pass.
