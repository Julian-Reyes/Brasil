# Rio geographic pipeline

Builds Unreal-ready blockout geometry from real geographic data. Everything is reproducible from
`config/project.json`; nothing in `GeoData/` is hand-edited (it is git-ignored and can be deleted).

## Setup

```sh
cd Tools/GeoPipeline
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

## Steps

| # | Script | Output | Notes |
|---|--------|--------|-------|
| 1 | `scripts/fetch_osm.py --aoi <aoi>` | `GeoData/raw/osm/<aoi>/` | Roads (drivable + pedestrian), buildings, reference features. Overpass responses cached; retries mirrors. |
| 2 | `scripts/fetch_dem.py --aoi full` | `GeoData/raw/dem/`, `GeoData/processed/dem/full_10m.tif` | Copernicus GLO-30 tiles, reprojected to EPSG:31983 (cubic). |
| 3 | `scripts/validate.py --aoi <aoi>` | `Docs/Map/validation/<aoi>.md` | Blocking checks: CRS, coverage, connectivity, licences. Warnings: DEM vs surveyed peaks. |
| 4 | `scripts/reference_map.py --aoi <aoi>` | `Docs/Map/reference/<aoi>.png` | Top-down map of the real geography (Phase 3 design input). |
| 5 | `scripts/build_area.py --aoi <aoi>` | `GeoData/exports/<aoi>/` | Tiled OBJ meshes, source road graph, manifest, verification report. |
| 6 | `unreal/mcp_import_area.py` (via unreal-mcp) | `/Game/Rio/Generated/...` + actors | See "Unreal import" below. |

AOIs are defined in `config/project.json` (`full`, `test_leblon_vidigal`). Add new test areas there.

## What `build_area.py` does

1. **Ground model.** Copernicus is a surface model (rooftops, canopy). Subtract the mapped building volume
   (OSM heights, blurred to radar resolution), then on flat lowland only take the minimum with a 150 m
   morphological opening. Sea cells become a sloping seabed.
2. **Road vertical alignment.** Least-squares solve over the whole network, per tier (arterials, then local
   streets, then footways/steps). Shared junction nodes are single unknowns, so intersections are exact. Grades
   are limited per class by iterative reweighting (`roads.max_grade`). Each lower tier follows the terrain
   already carved by the tiers above it.
3. **Terrain carving.** Corridors are flattened to road height under the carriageway + shoulder and blended
   back to natural ground; the higher-rank road wins where corridors overlap. Bridges are not carved; tunnels
   (`tunnel=yes`) are skipped and listed in the report.
4. **Meshes.** 500 m tiles: 2 m terrain grid, road ribbons with skirts (realistic widths from OSM
   `width`/`lanes`/class), extruded building footprints seated on the terrain. Placeholder heights come from OSM
   `height`/`building:levels`, otherwise deterministic per-OSM-id defaults (reproducible across runs).
5. **Verification.** `report.json`: road-surface-vs-terrain gaps, steepest drivable segments, building/road
   conflicts, height-source counts, triangle counts.

## Unreal import

Unreal Python remote execution is not enabled (see `Docs/Map/README.md`), so the import runs through
unreal-mcp's sandboxed `ProgrammaticToolset.execute_tool_script`. The script reads the manifest that
`build_area.py` mirrors to `Saved/RioPipeline/<aoi>_manifest.json`. Send the contents of
`unreal/mcp_import_area.py` with a `PARAMS` line prepended:

```python
PARAMS = {"aoi": "test_leblon_vidigal", "phase": "clear"}                        # in each level using the area
PARAMS = {"aoi": "test_leblon_vidigal", "phase": "import", "start": 0, "count": 15}  # repeat until all imported
PARAMS = {"aoi": "test_leblon_vidigal", "phase": "place"}                         # in the target level
```

**Regeneration safety.** Generated assets live only under `/Game/Rio/Generated/<Area>/` and are overwritten on
re-import. Generated actors carry the tags `RioGenerated` and `RioArea:<aoi>` and live in the outliner folder
`Generated/<Area>/`; `clear`/`place` delete only actors carrying the area tag. Hand-made work goes in
`/Game/Rio/Manual/` and untagged actors, and is never touched by the pipeline.

The blockout materials in `/Game/Rio/Materials/Blockout/` were created once by importing
`GeoData/exports/<aoi>/SM_BlockoutPalette.obj` with materials enabled; tile slot names match them.
