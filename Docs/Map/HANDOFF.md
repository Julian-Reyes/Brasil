# Handoff: picking the project up on another machine / in a new session

Read this first, then `README.md`, `DesignPriorities.md` and `Tools/GeoPipeline/README.md`.

## START HERE ON THE MAC MINI (written 2026-11-24 on the MacBook)

Branch: **`macbook/vidigal-infill`** (contains everything: `macbook/vidigal` + the offline OSM source + the hub
work in `macbook/vidigal-hub` + the new favela infill). `main` is still untouched. Nothing below has been opened in Unreal yet; the laptop checked it with
pipeline renders only (`Docs/Map/screenshots/vidigal_hub_3d.png`, `vidigal_hub_praca.png`,
`vidigal_hub_long_stair.png`, `vidigal_osm_steps.png`, `vidigal_hub_preview.png`).

What changed (details: `MacBookSessionNotes.md` -> Session 5 and Session 6):

* `fetch_beach.py` reads a local OSM extract (no Overpass needed). Output identical to the old Overpass files.
* Vidigal hub layout 2: big praca (34 x 22 m) at the centre, shops facing it, boteco / kiosk / mototaxi.
* Hub stairs (4) + a field gate; OSM `highway=steps` are now solid stepped meshes everywhere.
* Terrace fixes in every area: no more hills inside pitches, streets cut out of pitch aprons, retaining walls
  with top ledges on uphill sides.
* New tool `scripts/render_view.py` (3D views of the exports; handy on the mini too).
* Favela infill v2 (Session 7): houses line the streets / becos / stairs, mixed sizes and heights, painted /
  rendered / brick facades, parapets, roof rooms. Before / after renders: `screenshots/vidigal_infill_*.png`.

### Checklist

```sh
git fetch && git checkout macbook/vidigal-infill && git pull
cd Tools/GeoPipeline
uv pip install --python .venv/bin/python -r requirements.txt          # adds osmium (pyosmium)
mkdir -p ../../GeoData/raw/osm/extract && curl -fL -o ../../GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf \
  https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf
P=.venv/bin/python
$P scripts/fetch_beach.py --aoi test_leblon_arpoador --set beach
$P scripts/fetch_beach.py --aoi test_lagoa --set lagoa
$P scripts/fetch_beach.py --aoi test_vidigal --set vidigal
cp ../../GeoData/raw/osm/test_lagoa/lagoa.gpkg ../../GeoData/raw/osm/test_leblon_arpoador/
# builds (each area: build_area first, then its extra steps)
$P scripts/build_area.py --aoi test_vidigal && $P scripts/build_places.py --aoi test_vidigal
$P scripts/build_area.py --aoi test_lagoa && $P scripts/build_shore.py --aoi test_lagoa && $P scripts/build_places.py --aoi test_lagoa
$P scripts/build_area.py --aoi test_leblon_arpoador && $P scripts/build_beach.py --aoi test_leblon_arpoador \
  && $P scripts/build_shore.py --aoi test_leblon_arpoador && $P scripts/build_places.py --aoi test_leblon_arpoador
```

Expected log lines for Vidigal: `hub stairs: praca to campinho 40.5->38.7 m, 11 steps ...`, `stepped stairs: 153
steps edges, 4.4 km, 7244 steps`, `favela infill: Rocinha: +5717 houses ... Vidigal: +5170 houses, coverage 8% -> 55%`. If the mini's numbers differ a lot, suspect the laptop's older geo packages
(`MacBookSessionNotes.md` -> "Laptop setup").

In the editor (one time, with materials, into `/Game/Rio/Materials/Blockout/`): re-import
`SM_BlockoutPalette.obj` (has `M_WaterTank` and the new house facades `M_HouseRender`, `M_HousePaint*`), import `beach/SM_BeachPalette.obj`, `shore/SM_ShorePalette.obj`,
and **re-import `places/SM_PlacesPalette.obj`** (new `M_Stair`). Then for each level (`L_Rio_Test_Vidigal`,
`L_Rio_Test_Lagoa`, `L_Rio_Test_LeblonArpoador`):
`$P unreal/ue_remote.py unreal/import_area.py '{"aoi": "<aoi>"}'`. Place the Vidigal PlayerStart at
`manifest.json -> markers -> player_home`.

### What to test in the editor

Fly to the spots in **`UnrealDebugGuide.md` -> "Vidigal hub, layout 2"** (UTM + UE coordinates) and:

1. Walk up and down all four hub stairs and a few beco stairs with the template character (17 cm risers; step
   height 45 cm). Any lip at the stair ends, cheek wall in the way, terrain poking through treads?
2. Campinho: flat (no hills), fence gaps at the 3 stair landings + west gate, retaining walls.
3. Praca: size and feel from street level, shops across the avenue, does it read as the social heart?
4. Wall top ledges next to streets: gaps, or ledges floating above the road.
4b. Favela infill from the street and from Leblon (spots: `UnrealDebugGuide.md` -> "Vidigal housing infill v2"):
    does it read as a favela, any house blocking a beco / stair, frame rate.
5. Report back anything off; layout changes go in `config/project.json -> places.hubs[0]`, then re-run
   `build_area.py` + `build_places.py`.

Known: the old `test_leblon_vidigal` area has a 20-35 m higher ground model (pre-existing); the hub stairs are
unwalkable there. Use `test_vidigal`.

## Earlier (2026-11-24, Mac mini): Overpass was down; offline OSM source (now done, see above)

Branch: still `macbook/vidigal` (latest commit pushed from the Mac mini). `main` is untouched and still at the
2026-11-17 handoff; all MacBook work plus the items below are on `macbook/vidigal` only.

### Done on the Mac mini

* Checked out `macbook/vidigal`. Full-area reference layer fetched: `GeoData/raw/osm/full/reference.gpkg`,
  1,867 features (same count as the MacBook). `clip_osm.py` run for `test_leblon_arpoador`, `test_lagoa`,
  `test_vidigal`.
* New levels created in Unreal (empty apart from lighting): `L_Rio_Test_Lagoa`, `L_Rio_Test_Vidigal`, via the new
  `Tools/GeoPipeline/unreal/create_test_level.py` (World Partition, GeoReferencingSystem identical to
  `L_Rio_Test_LeblonArpoador`, same sky / light / fog set, optional PlayerStart). No PlayerStarts yet: place them
  after the builds (Vidigal at `player_home` from `manifest.json -> markers`; Lagoa on the shore).
* `scripts/fetch_beach.py` now queries in ~1 km cells (`--chunk-m`, default 1000) instead of one request.
  **Rule: never send Overpass large areas; always small chunks.** Each cell is cached by OSMnx, so a re-run only
  re-requests missing cells.

### Blocked

`fetch_beach.py` (beach / lagoa / vidigal sets) cannot run: on 2026-11-24 every Overpass server failed even for a
500 m test query (overpass-api.de 502, kumi.systems and private.coffee time out, mail.ru / osm.jp do not resolve).
Every build step needs these files (`build_area.py` reads pitch outlines from all feature-set `.gpkg`s), so nothing
was built or imported on the mini yet.

### Next task: offline OSM source for `fetch_beach.py` (works on the MacBook too)

Replace the Overpass dependency with a local extract:

1. `uv pip install --python .venv/bin/python osmium` (pyosmium 4.3.2 has prebuilt wheels for macOS 11+ on both
   x86_64 and arm64, Python 3.12: no compiling on the 2017 MacBook). Add it to `requirements.txt`.
2. Download the BBBike Rio de Janeiro extract once, 38 MB, updated 2026-11-22:
   `https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf` -> `GeoData/raw/osm/extract/`.
   First check its extent covers every AOI (Vidigal at lon -43.252 to Lagoa / Arpoador at -43.19).
   Fallback: Geofabrik `south-america/brazil/sudeste-latest.osm.pbf` (864 MB).
3. Add `--source pbf` (or auto-fallback) to `fetch_beach.py`: read the pbf with pyosmium (nodes + ways + multipolygon
   relations), keep features inside the set's anchor bbox that match `feature_sets.<set>.tags` /
   `optional_tags`, and write the **same** `<set>.gpkg` layers as now (`features` = representative points with
   `osm_type`, `osm_id`, `area_m2`, `length_m`, KEEP columns; `areas` = polygon outlines). Keep Overpass as the
   other source.
4. Run it for the three sets, then `cp GeoData/raw/osm/test_lagoa/lagoa.gpkg GeoData/raw/osm/test_leblon_arpoador/`.

Shortcut if the MacBook still has them from 2026-11-18: copy
`GeoData/raw/osm/test_leblon_arpoador/beach.gpkg`, `test_lagoa/lagoa.gpkg`, `test_vidigal/vidigal.gpkg` to the
mini (AirDrop / USB), which unblocks the mini without any of the above.

### DONE 2026-11-24 on the MacBook: offline source works (`MacBookSessionNotes.md` -> Session 5)

`fetch_beach.py --source auto` (default) now reads `GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf` with pyosmium;
results identical to the 11-18 Overpass files. On the mini:

```sh
cd Tools/GeoPipeline && git pull && uv pip install --python .venv/bin/python -r requirements.txt   # adds osmium
mkdir -p ../../GeoData/raw/osm/extract && curl -fL -o ../../GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf \
  https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf
.venv/bin/python scripts/fetch_beach.py --aoi test_leblon_arpoador --set beach
.venv/bin/python scripts/fetch_beach.py --aoi test_lagoa --set lagoa
.venv/bin/python scripts/fetch_beach.py --aoi test_vidigal --set vidigal
cp ../../GeoData/raw/osm/test_lagoa/lagoa.gpkg ../../GeoData/raw/osm/test_leblon_arpoador/
```

### Then, on the Mac mini (needs the editor)

Builds and imports exactly as in `MacBookSessionNotes.md` (Session 3 order for beach + Lagoa, Session 4 for
Vidigal), plus the one-time palette imports with materials into `/Game/Rio/Materials/Blockout/`:
`SM_BlockoutPalette.obj` (re-import, adds `M_WaterTank`), `beach/SM_BeachPalette.obj`, `shore/SM_ShorePalette.obj`,
`places/SM_PlacesPalette.obj`. The editor is reachable via `unreal/ue_remote.py` (verified on the mini today).

## Set up a new machine

1. `git lfs install`, then clone. All `.uasset` / `.umap` files are in Git LFS.
2. Open the project once in UE 5.8. Python Remote Execution is already enabled in `Config/DefaultEngine.ini`.
3. Pipeline env: `cd Tools/GeoPipeline && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
4. Raw and derived geodata are **not** in git (`GeoData/` is ignored; it is regenerable). Either copy
   `GeoData/raw` from the old machine (fastest), or re-run:
   `scripts/fetch_dem.py`, `scripts/fetch_osm.py --aoi full`, then
   `scripts/clip_osm.py --aoi <test aoi>` and `scripts/build_area.py --aoi <test aoi>`.
   Overpass (the OSM API) was unreliable on 2026-11-17; `fetch_osm.py` retries mirrors.

## Working rules learned so far

* Drive the editor with `Tools/GeoPipeline/.venv/bin/python unreal/ue_remote.py <script.py> '<json>'` (full editor
  Python). On macOS the stock remote-execution client cannot find the editor; `ue_remote.py` contains the fix.
* unreal-mcp is the fallback (call tools by short name plus `toolset_name`).
* Regenerate generated content only via `build_area.py` and `unreal/import_area.py`. Generated actors are tagged
  `RioGenerated` / `RioArea:<aoi>`; never hand-edit them. Manual work goes in `/Game/Rio/Manual`.
* World Partition: test-area actors are non-spatially-loaded so levels never open empty.
* Never modify the Third Person template. Keep the 1:1 SOURCE levels separate from the compressed playable map.
* Hero area: the Leblon to Arpoador beach (see `DesignPriorities.md`).

## Vidigal plan (agreed 2026-11-18)

Steps 1-3 **done on 2026-11-24** on branch `macbook/vidigal-hub` (`MacBookSessionNotes.md` -> Session 6; spots to
walk in `UnrealDebugGuide.md` -> "Vidigal hub, layout 2"). Step 4 **done** on `macbook/vidigal-infill`
(Session 7). Next on the MacBook: step 5.

1. ~~Hub praca~~: 34 x 22 m praca at the centre, shops facing it, boteco / kiosk / mototaxi on its east side.
2. ~~Hub stairs~~: four designed stairs (street, praca, campinho) plus a gate; fence and walls open at them.
3. ~~Stairways everywhere~~: OSM `highway=steps` are solid treads (4.4 km / 7,244 steps in the Vidigal AOI).
4. ~~Less grid-like infill~~ (done, Session 7). Was: varied lot sizes / heights, merged blocks, colour variation (painted houses).
5. **Details:** house doors / entrances, overhead wire tangles, narrow car access, lookout points.
6. Later, after the walk-around test: one or two signature escadarias (e.g. a long painted stair from the hub
   toward the top).

Still open from earlier: Unreal verification of everything (no editor on the MacBook), `UnrealDebugGuide.md`
problems 1-2, the restaurants across the Lagoa ring road (decide after the drive / walk test), then the
compressed map.

## State on 2026-11-18, latest (MacBook, branch `macbook/vidigal`, on top of `macbook/lagoa`)

* Vidigal starting area: test AOI `test_vidigal`, favela housing infill, Dois Irmaos at surveyed height, the
  central hub (campinho, praca, shops, player house, crew hangout) and real landmarks. Design in
  `DesignPriorities.md`; details and editor checklist in `MacBookSessionNotes.md` -> Session 4.

## State on 2026-11-18 (MacBook, branch `macbook/lagoa`)

* Lagoa shore promenade (`build_shore.py`). Road-height debugging steps for the editor:
  **`UnrealDebugGuide.md`**. See `MacBookSessionNotes.md` -> Session 3.

## State on 2026-11-18, later (MacBook, branch `macbook/lagoa`, on top of `macbook/beach-blockout`)

* Inland water (lagoon was a green field), flat-shore cap (beachfront / Lagoa ring road bumps), new AOI
  `test_lagoa`, `scripts/preview_area.py`. See `MacBookSessionNotes.md` -> Session 2. Re-import the beach area too.

## State on 2026-11-18 (MacBook, branch `macbook/beach-blockout`)

* **Read `MacBookSessionNotes.md` first.** Beach blockout generator written (`scripts/build_beach.py`), not yet
  imported into Unreal. Full-area reference layer downloaded on the laptop (copy it, or re-run `fetch_osm.py`).
* The Cantagalo slope issue is *not* fixed by the reference layer: the cause is the minor-street grade limit
  overriding the ground data (details and fix options in the notes).

## State on 2026-11-17

* Done: pipeline, Vidigal test (`L_Rio_Test_LeblonVidigal`, `Phase4_TestReport.md`), hero beach test
  (`L_Rio_Test_LeblonArpoador`, `BeachTestReport.md`).
* Pending: full-area OSM *reference* layer (coastline, peaks, favela outlines). Roads and buildings for the full
  area are downloaded. When reference arrives: `clip_osm.py --aoi test_leblon_arpoador`, rebuild and re-import
  (fixes the Cantagalo / Pavao-Pavaozinho slopes), then `validate.py` / `reference_map.py --aoi full`.
* Next (user request 2026-11-17): **beach blockout generator** for the hero beach, procedural from the
  beachfront road centreline so it regenerates in the compressed map: sand (OSM natural=beach, or derived from
  coastline + Delfim Moreira / Vieira Souto until the reference layer exists), calcadao strips at real widths,
  ciclovia, kiosks, Postos 7-12 at real positions, palm rows, Jardim de Alah mouth. Flat blockout materials only;
  the black-and-white wave paving (different per Leblon / Ipanema / Copacabana) is a later material/decal pass.
* Then: Phase 3, compressed road-graph proposal, protecting the hero beach.
