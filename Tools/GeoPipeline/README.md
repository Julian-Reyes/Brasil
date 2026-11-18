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
| 5b | `scripts/build_beach.py --aoi <aoi>` | `GeoData/exports/<aoi>/beach/` | Hero beach blockout; run after step 5, needs `scripts/fetch_beach.py --aoi <aoi>` (1b) for Postos/kiosks. See "Hero beach" below. |
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

## Clipping instead of downloading

`scripts/clip_osm.py --aoi <aoi>` derives an AOI's raw OSM inputs from the already-downloaded `full` extract,
so a new test area needs no Overpass request.

## Unreal import

Primary path: full editor Python over PythonScriptPlugin **Remote Execution** (Project Settings > Plugins >
Python > Enable Remote Execution, bind address 127.0.0.1). `unreal/ue_remote.py` wraps the engine's
`remote_execution.py`, with a macOS fix: the editor's discovery socket is bound to 127.0.0.1, which never
receives multicast on BSD stacks, so discovery is also sent unicast. Open the target level, then run:

```sh
.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_leblon_vidigal"}'
```

`import_area.py` loads (World Partition) and deletes the area's tagged actors, re-imports every mesh in place,
binds the shared blockout materials, sets per-triangle collision, re-spawns the actors and saves. It takes about
2.5 minutes for the 46-mesh test area. AOIs with `"always_loaded": true` place non-spatially-loaded actors, so
small test levels never open empty.

Fallback without Remote Execution: `unreal/mcp_import_area.py` through unreal-mcp's sandboxed
`execute_tool_script` (phases `clear` / `import` / `place`). Caveat: it only sees *loaded* actors, so load the
area in the World Partition editor first, or `place` will duplicate actors.

**Regeneration safety.** Generated assets live only under `/Game/Rio/Generated/<Area>/` and are overwritten on
re-import. Generated actors carry the tags `RioGenerated` and `RioArea:<aoi>` and live in the outliner folder
`Generated/<Area>/`; `clear`/`place` delete only actors carrying the area tag. Hand-made work goes in
`/Game/Rio/Manual/` and untagged actors, and is never touched by the pipeline.

The blockout materials in `/Game/Rio/Materials/Blockout/` were created once by importing
`GeoData/exports/<aoi>/SM_BlockoutPalette.obj` with materials enabled; tile slot names match them.

## Hero beach (`scripts/build_beach.py`)

Sand, ciclovia, calcadao, kiosks, Postos, palm rows and the canal mouths (Jardim de Alah, Visconde de
Albuquerque) for the Leblon to Arpoador beach. Order for an AOI that contains the beach:

```sh
.venv/bin/python scripts/fetch_beach.py --aoi test_leblon_arpoador   # once; Overpass, cached
.venv/bin/python scripts/build_area.py  --aoi test_leblon_arpoador   # rewrites manifest.json
.venv/bin/python scripts/build_beach.py --aoi test_leblon_arpoador   # appends kind "Beach" to manifest.json
.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_leblon_arpoador"}'
```

How it works (`rio_geo/beach.py`): the sea-side carriageway of `beach.front_roads` (Delfim Moreira, Vieira
Souto, Francisco Bhering) is chained W->E into one 3D centreline, taking z from the solved road profiles. One-way
edges count as sea-side when the sea is on their right (right-hand traffic); two-way edges by a probe toward the
coast. Every 5 m a ray to the sea measures the ciclovia (OSM cycleway), the sand edge and the waterline (where
the ray leaves the OSM beach polygons; coastline as fallback). The smoothed result is saved as
`beach/beach_profile.json` with stations normalised 0..1, so the compressed map can re-apply the same profile
to its own centreline. Geometry is built only from centreline + profile; the sand is draped over the generated
terrain where that is higher. Postos and kiosks come from `beach.gpkg`; kiosk gaps over
`beach.kiosk.fill_gap_m` are filled procedurally. All widths/heights/spacings are in `config/project.json` ->
`beach`.

Check `beach/beach_preview.png` (top down vs OSM, Jardim de Alah and Arpoador close-ups, profile, road height)
and `beach/beach_report.json` before importing.

**One-time Unreal step:** the beach uses new material slots (`M_Sand`, `M_Calcadao`, `M_Ciclovia`, `M_Kiosk`,
`M_KioskRoof`, `M_Posto`, `M_PostoCabin`, `M_PalmTrunk`, `M_PalmLeaf`). Import
`GeoData/exports/<aoi>/beach/SM_BeachPalette.obj` once *with materials* into `/Game/Rio/Materials/Blockout/`,
exactly as was done for `SM_BlockoutPalette.obj`. `import_area.py` now stops with "missing material ..." if a slot
has no material asset.

Postos are also written to `manifest.json` -> `markers` (name + location) for spawn points / gameplay; the
importer ignores them for now.
