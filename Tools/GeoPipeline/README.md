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

AOIs are defined in `config/project.json` (`full`, `test_leblon_vidigal`, `test_leblon_arpoador`, `test_lagoa`). Add new test areas there.

`build_area.py` also handles inland water (config `water`: lagoon/lake/canal beds + flat water meshes, kind `Water`) and the flat-shore cap near lagoons and large beaches (`water.shore_cap`). Check any built area with `scripts/preview_area.py --aoi <aoi> --roads "<road name>"` (writes `GeoData/exports/<aoi>/preview.png`).

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

## Offline OSM extract (instead of Overpass)

`fetch_beach.py` (all feature sets) reads a local extract when it exists (`--source auto`, the default;
`--source pbf|overpass` to force one). The public Overpass servers were all down on 2026-11-24.

```sh
mkdir -p ../../GeoData/raw/osm/extract
curl -fL -o ../../GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf \
  https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf     # BBBike, ~38 MB, covers every AOI
.venv/bin/python scripts/fetch_beach.py --aoi test_vidigal --set vidigal       # ~35 s per set on the 2017 MacBook
```

`rio_geo/osm_pbf.py` clips the pbf to the set's bbox with pyosmium (objects with the wanted tag keys, plus every
node / member way they reference) into `GeoData/raw/osm/_pbf_cache/<set>.osm`, then `osmnx.features_from_xml`
applies the exact tags and the bbox, so the `.gpkg` is the same as from Overpass (checked 2026-11-24 for beach,
lagoa and vidigal: identical OSM ids, columns and geometry). `<set>_metadata.json -> source` says which was used.
Fallback extract if BBBike stops covering the area: Geofabrik `south-america/brazil/sudeste-latest.osm.pbf` (864 MB).
`fetch_osm.py` (roads, buildings, reference) still uses Overpass only.

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
.venv/bin/python scripts/fetch_beach.py --aoi test_leblon_arpoador   # once; local pbf or Overpass
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

## Lake shore promenade (`scripts/build_shore.py`)

Walking + bike lanes, benches, trees / palms and kiosks around `shore.water_name` (Lagoa Rodrigo de Freitas).
Run after `build_area.py`; needs `scripts/fetch_beach.py --aoi <aoi> --set lagoa` for real kiosks, benches and
trees (procedural only without it). Same design as the beach: loop (outer shoreline) + measured profile
(`shore/shore_profile.json`) -> geometry; outputs kind `Shore`. One-time Unreal step: import
`shore/SM_ShorePalette.obj` with materials into `/Game/Rio/Materials/Blockout/` (after the beach palette).
Check `shore/shore_preview.png` and `shore/shore_report.json`. Settings: `config/project.json -> shore`.

Editor helpers (untested): `unreal/goto.py` moves the viewport camera to a UTM / UE point, `unreal/trace_heights.py`
line-traces heights at UTM points. See `Docs/Map/UnrealDebugGuide.md`.

## Favelas, landmarks and places (Vidigal starting area)

All inside `build_area.py` unless noted; settings in `config/project.json`.

* **Landmark summits** (`landmark_peaks`): listed OSM peaks are raised to their surveyed `ele` with a smooth dome
  (the 30 m DEM cuts steep granite summits short: Irmao Maior 382 vs 539 m).
* **Favela infill** (`favela_infill`, `rio_geo/favela.py`): procedural houses in under-mapped favela polygons up to
  a coverage target, keeping streets / becos / stairs clear; runs before the ground model, which subtracts the
  infill footprints like mapped buildings. Houses first line every street / beco / stair (aligned, varied widths
  from a small / medium / wide mix, mostly wall to wall, occasional passages), then fill the interior along the
  contours. `house_style` gives 2-4 storeys (+1 on car streets, smooth spatial variation, ~3% 5-7 storey
  blocks), a facade material (bare brick `M_BuildingInformal`, render `M_HouseRender`, painted `M_HousePaint*`,
  blockout palette) and laje extras (parapet, small roof room, water tank); OSM-mapped favela houses get the
  same looks. Around a hub: `infill_max_levels`, and `places.hub_clear_margin_m` keeps designed spaces open.
* **Places** (`places`, `rio_geo/places.py`): flat terraces under OSM pitch outlines (`fetch_beach.py --set
  <set>` writes them to the `areas` layer) and under designed hubs (`places.hubs`, UTM layout); houses and becos
  inside are removed. Then run `scripts/build_places.py --aoi <aoi>` for pitch surfaces, goals, fences, retaining
  walls, hub buildings and gameplay markers (`manifest.json -> markers`), plus `places/hub_preview_<n>.png`.
  One-time Unreal step: import `places/SM_PlacesPalette.obj` with materials into `/Game/Rio/Materials/Blockout/`.

* **Stairs** (`rio_geo/stairs.py`): one tread generator for two uses. OSM `highway=steps` become solid treads
  along their solved profile (`roads.stepped_stairs`, 17 cm risers) instead of a flat ribbon. Designed hub stairs
  (`places.hubs[].stairs`: 2D path + width) take their end heights from what each end stands on (terrace,
  road surface, terrain) after the road solve, get equal risers, a terrain ramp under them, stepped cheek walls
  and retaining walls where they cut into the slope; fences / terrace walls open where a stair enters (and at
  `gates`). Resolved profiles: `GeoData/exports/<aoi>/stairs.json`.
* **Terrace edges:** after the road carve, terraces are re-flattened (the street blend used to pull hills into
  the pitches), streets are cut out of OSM pitch aprons, and on uphill sides the 2 m-grid slope is moved behind
  the retaining wall, which gets a flat top ledge.

* **Favela lanes** (`roads.favela_lanes`, `rio_geo/roads.py narrow_in_zones`): drivable streets mostly inside a
  favela polygon with no mapped width get one-car widths (residential 4.2 m, living street 3.4 m, service 3.0 m;
  Av. Pres. Joao Goulart 6.0 m by name). Hub streets keep theirs (`hub_keep_radius_m`).
* **Turnarounds** (`roads.turnarounds`, `rio_geo/turnarounds.py`, in `build_area.py` after the road solve): every
  dead end of a narrow (<= 5 m) lane inside a favela gets an 11 m turning bulb tilted like the lane's end, the
  ground graded to it and informal houses on it dropped (skipped / smaller where it would hit a formal building,
  a hub element, an escadaria or another street at a different height). Writes `turnarounds.json`; meshed in
  the Roads tiles; `build_details.py` keeps poles off the bulbs.
* **Street details** (`details`, `rio_geo/details.py`, `scripts/build_details.py --aoi <aoi>`, after
  `build_places.py`): doors on every favela house (on the wall facing its street / beco / stair, at the ground
  outside it; roller shutters on some ground floors on car streets), concrete poles along favela car streets with
  power lines, sagging telecom / "gato" cables, transformers and service drops, and cables zigzagging wall to wall
  over becos and stairs (kind `Wires`, no collision). Every pole gets a power line: poles are linked in order along
  their street, then to neighbours across junctions; a blocked span is retried with both poles raised (up to
  12.5 m), then via a new pole at the street point in between; a pole that still reaches nothing is removed. Lookouts (mirantes): `build_area.py` picks the best few
  points on becos / stairs / lanes looking over the roofs to the sea (`lookout_sites`, clears the house or two on
  each deck, writes `lookouts.json`); `build_details.py` builds the deck, railing and bench and adds `lookout`
  markers. Reads `houses.gpkg` (all placed footprints with heights, written by `build_area.py`).
  One-time Unreal step: import `details/SM_DetailsPalette.obj` with materials into `/Game/Rio/Materials/Blockout/`.

* **Signature escadarias** (`escadarias`, `rio_geo/escadarias.py`): each configured route (OSM node ids, cheapest
  pedestrian path between them) is retagged a 3 m stair at the top of `build_area.py` (so infill lines it and the
  profile solve treats it as a stair); after the road solve `build_area.py` clears buildings from its corridor,
  paints the facades next to it, inserts landings into its profile, lowers the ground under it and writes
  `escadarias.json` (it does not mesh those edges). `build_details.py` builds painted treads (alternating or
  height bands), a handrail, lamps, benches, murals, bars and `escadaria` / `bar` markers. Routes whose nodes are
  not in an AOI are skipped.

Vidigal order: `clip_osm.py`, `fetch_beach.py --set vidigal`, `build_area.py`, `build_places.py`,
`build_details.py`, import.

## 3D views without Unreal (`scripts/render_view.py`)

A numpy z-buffer render of the exported OBJ tiles (flat shading, palette colours), for checking geometry on the
MacBook:

```sh
.venv/bin/python scripts/render_view.py --aoi test_vidigal --target 680830 7456045 40 --eye-rel 235 32 75 \
    --radius 80 --out /tmp/hub.png        # eye-rel = azimuth from north, elevation, distance (or --eye E N Z)
```

About 20-40 s for 10-20 k triangles; keep `--radius` small. Empty pixels below the horizon are drawn as sea
(sea level), so long views (`--radius 900`) show the coast.
