# Handoff: picking the project up on another machine / in a new session

Read this first, then `README.md`, `DesignPriorities.md` and `Tools/GeoPipeline/README.md`.

## START HERE ON THE MAC MINI (written 2026-11-24, updated 2026-11-25 on the MacBook)

Branch: **`macbook/centro`** (= `macbook/beach-life` + the Centro area and the landmark registry, Sessions 16-17;
extra steps: `clip_osm.py --aoi test_centro`, `fetch_beach.py --aoi test_centro --set centro`, then build_area / build_places /
build_details for `test_centro`, new level `L_Rio_Test_Centro`). Before that: **`macbook/beach-life`** (contains everything: `macbook/vidigal` + the offline OSM source + the
hub work in `macbook/vidigal-hub` + the favela infill in `macbook/vidigal-infill` + the step 5 street details in
`macbook/vidigal-details` + the step 6 signature escadarias in `macbook/vidigal-escadarias` + turnarounds and
linked poles in `macbook/vidigal-lanes-poles` + beach life, formal facades, beach terrain fixes and the new
Copacabana area).
`main` is still untouched. Nothing below has been opened in Unreal yet; the laptop checked it with
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
* Street details (Session 8): doors on every favela house, poles + overhead wire tangles, one-car lanes inside
  the favela, four lookouts (mirantes). New script `build_details.py`, new kinds `Details` / `Wires`. Renders:
  `screenshots/vidigal_details_*.png`.
* Signature escadarias (Session 9): two long, 3 m wide painted stairs (Escadaria do Niemeyer: Av. Niemeyer to
  below the hub; Escadaria Padre Ítalo: up to 138 m) with landings, a handrail, lamps, benches, murals and three
  bars. Built by `build_area.py` (route, corridor, landings) + `build_details.py` (meshes). Renders:
  `screenshots/vidigal_escadaria*.png`.
* Turnarounds and pole lines (Session 10): turning bulbs at the 16 dead ends of narrow favela lanes; every pole
  now has a power line (poles raised or added at bends). Renders: `screenshots/vidigal_turnaround_*.png`,
  `vidigal_poles_linked.png`.
* Beach life, facades, Copacabana (Session 12): barracas / umbrellas / chairs / towels / volleyball and
  footvolley courts on both beaches (new kind `BeachLife`, no collision); formal buildings get facade colours,
  a dark ground-floor band, parapets and roof machine rooms; beach terrain fixed (no fake land strip in the surf,
  no pit under the sand); beachfront avenues flattened; new AOI `test_copacabana` (Posto 6 to Leme). Renders:
  `screenshots/beach_life_*.png`, `ipanema_facades_aerial.png`, `copacabana_*.png`.
* Arpoador and the Forte (Session 13): the Pedra do Arpoador (missing from Copernicus) rebuilt from OSM to 14 m;
  coastal headlands keep their relief; the Forte de Copacabana as a complex (fortress with walkable roof,
  cupolas and guns, colonial museum buildings, wall, lawns, Posto 6 pier). Renders: `screenshots/arpoador_rock.png`,
  `forte_*.png`.
* Copacabana area extended (Session 14): now to the Leme point, Babilonia / Chapeu Mangueira (now favelas), Praia
  Vermelha, Morro da Urca and the Pao de Acucar (E690000 / N7461000). 9.2 M triangles. Render:
  `screenshots/copacabana_leme_urca.png`.

### Checklist

```sh
git fetch && git checkout macbook/beach-life && git pull
cd Tools/GeoPipeline
uv pip install --python .venv/bin/python -r requirements.txt          # adds osmium (pyosmium)
mkdir -p ../../GeoData/raw/osm/extract && curl -fL -o ../../GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf \
  https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf
P=.venv/bin/python
$P scripts/fetch_beach.py --aoi test_leblon_arpoador --set beach
$P scripts/fetch_beach.py --aoi test_lagoa --set lagoa
$P scripts/fetch_beach.py --aoi test_vidigal --set vidigal
$P scripts/clip_osm.py --aoi test_copacabana && $P scripts/fetch_beach.py --aoi test_copacabana --set copacabana
$P scripts/fetch_beach.py --aoi test_leblon_arpoador --set forte && $P scripts/fetch_beach.py --aoi test_copacabana --set forte
cp ../../GeoData/raw/osm/test_lagoa/lagoa.gpkg ../../GeoData/raw/osm/test_leblon_arpoador/
# builds (each area: build_area first, then its extra steps)
$P scripts/build_area.py --aoi test_vidigal && $P scripts/build_places.py --aoi test_vidigal && $P scripts/build_details.py --aoi test_vidigal
$P scripts/build_area.py --aoi test_lagoa && $P scripts/build_shore.py --aoi test_lagoa && $P scripts/build_places.py --aoi test_lagoa
$P scripts/build_area.py --aoi test_leblon_arpoador && $P scripts/build_beach.py --aoi test_leblon_arpoador \
  && $P scripts/build_shore.py --aoi test_leblon_arpoador && $P scripts/build_places.py --aoi test_leblon_arpoador
$P scripts/build_details.py --aoi test_lagoa && $P scripts/build_details.py --aoi test_leblon_arpoador   # Pavao-Pavaozinho
$P scripts/build_area.py --aoi test_copacabana && $P scripts/build_beach.py --aoi test_copacabana \
  && $P scripts/build_places.py --aoi test_copacabana && $P scripts/build_details.py --aoi test_copacabana
```

Expected (Session 12): Leblon-Arpoador `beach life: {'barracas': 72, 'umbrellas': 1923, 'chairs': 2894, 'towels':
650, 'courts_osm': 2, 'courts_procedural': 18 ...}`, beach centreline `z 1.01..3.28 m`; Copacabana `geometry: 12
tiles, 5 postos, 47 kiosks, 279 palms`, `beach life: {'barracas': 52, 'umbrellas': 1976, ...}`, `buildings placed:
11002`, `favela infill: Babilônia: +1578 houses ...; Chapéu Mangueira: +623 houses ...`, `landmark peaks raised to
surveyed height: Pão de Açúcar +93.1 m, Morro de São João +60.5 m, Agulha do Inhangá +36.7 m, Morro do Leme +41.8 m`,
`wrote 194 meshes, 9.20 M triangles`. New level for Copacabana: `$P unreal/ue_remote.py unreal/create_test_level.py '{"level":
"/Game/Rio/Maps/L_Rio_Test_Copacabana", "player_start": {"name": "PlayerStart_Copacabana", "location_cm": [192000,
232000, 300], "yaw": 0}}'`, then `import_area.py '{"aoi": "test_copacabana"}'`.

Expected log lines for Vidigal: `hub stairs: praca to campinho 40.5->38.7 m, 11 steps ...`, `escadaria Escadaria do
Niemeyer: 0.8 -> 28.8 m, 6 edges, 8 landings`, `escadaria Escadaria Padre Ítalo: 22.2 -> 138.4 m, 15 edges, 34
landings`, `stepped stairs: 159 steps edges, 4.7 km, 6685 steps` (the escadarias' treads are counted by
`build_details.py`, not here), `turnarounds: {'dead_ends': 16, 'built': 16, 'skipped': 0, 'houses_dropped': 75}`, `favela lanes narrowed: living_street 0.6 km, residential 7.1 km, service 1.6 km`, `favela infill: Rocinha: +5700
houses ... Vidigal: +5141 houses, coverage 8% -> 55%`, `lookouts: {... 'built': 4, 'houses_removed': 7}`, and
from `build_details.py`: `doors: {'doors': 12155, 'shutters': 487, ...}`, `poles and wires: {'poles': 395, ..., 'poles_removed': 2, 'poles_unlinked': 0}`. If the mini's numbers differ a lot, suspect the laptop's older geo packages
(`MacBookSessionNotes.md` -> "Laptop setup").

In the editor (one time, with materials, into `/Game/Rio/Materials/Blockout/`): re-import
`SM_BlockoutPalette.obj` (has `M_WaterTank`, the house facades `M_HouseRender`, `M_HousePaint*` and, new in
Session 12, `M_Facade*`, `M_GroundFloor`, `M_RoofConcrete`; Session 13 `M_FortConcrete`, `M_FortColonial`;
Session 17 `M_Landmark`, `M_LandmarkConcrete`, `M_ManualPlaceholder`), import `beach/SM_BeachPalette.obj` (Session 12 adds
`M_Umbrella*`, `M_ChairFrame`, `M_Barraca`, `M_CourtLine`, `M_NetPost`, `M_Net`), `shore/SM_ShorePalette.obj`,
**re-import `places/SM_PlacesPalette.obj`** (new `M_Stair`; Session 13 adds `M_FortTurret`, `M_FortGun`, `M_FortWall`,
`M_Lawn`, `M_Pier`), and **import `details/SM_DetailsPalette.obj`** (new:
doors, shutter, pole, transformer, wire, lookout deck, railing, and the escadaria paints, lamp, murals, bar). Then for each level (`L_Rio_Test_Vidigal`,
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
4c. Street details (spots: `UnrealDebugGuide.md` -> "Vidigal street details"): door heights on slopes, drive a
    car up the 4.2 m lanes, poles off the carriageway, wires clear of heads (no collision), how thin cables look
    at distance, the four lookouts' views.
4d. Escadarias (spots: `UnrealDebugGuide.md` -> "Vidigal signature escadarias"): walk both end to end (17 cm
    risers, landings, the steeper steps next to a landing), the handrail and lamps in the way or not, bars and
    tables blocking the stair, does each read as a landmark from the street / from a distance.
4e. Turnarounds and poles (spots: `UnrealDebugGuide.md` -> "Turnarounds and pole lines"): turn a car in a
    level and a steep bulb, seams with the lane, tall / added poles.
4f. Beaches and Copacabana (spots: `UnrealDebugGuide.md` -> "Beach life, facades, Copacabana"): umbrella / chair
    scale, frame rate at Posto 9, courts, facade colours, sand edge vs calcadao, the Posto 6 hump (decide option A
    together with Problem 2), the Tunel Novo slope.
4g. Arpoador and the Forte (spots: `UnrealDebugGuide.md` -> "Arpoador and the Forte"): rock silhouette and paths,
    fortress roof / cupolas / wall vs photos; Morro do Leme summit (Forte Duque de Caxias, road notch) at
    E688419 N7459373 = UE (391900, 162700, 15000).
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
(Session 7). Step 5 **done** on `macbook/vidigal-details` (Session 8). Step 6 **done** on
`macbook/vidigal-escadarias` (Session 9). Next: the walk-around test on the mini.

1. ~~Hub praca~~: 34 x 22 m praca at the centre, shops facing it, boteco / kiosk / mototaxi on its east side.
2. ~~Hub stairs~~: four designed stairs (street, praca, campinho) plus a gate; fence and walls open at them.
3. ~~Stairways everywhere~~: OSM `highway=steps` are solid treads (4.4 km / 7,244 steps in the Vidigal AOI).
4. ~~Less grid-like infill~~ (done, Session 7). Was: varied lot sizes / heights, merged blocks, colour variation (painted houses).
5. ~~Details~~ (done, Session 8): house doors / entrances, overhead wire tangles, narrow car access, lookout points.
6. ~~Signature escadarias~~ (done, Session 9, before the walk-around test at the user's request): Escadaria do
   Niemeyer + Escadaria Padre Ítalo; judge them in the walk-around.

Still open from earlier: Unreal verification of everything (no editor on the MacBook), `UnrealDebugGuide.md`
problems 1-2, the restaurants across the Lagoa ring road (decide after the drive / walk test), then the
compressed map.

### Next on the MacBook (no Unreal needed; noted 2026-11-25)

Session 12 did the beach / building / Copacabana work below the list (beach life, facades, terrain, Copacabana).

Most remaining work waits on the walk-around (stairs / lanes / turnarounds feel, cable shimmer, frame rate,
problems 1-2). What the laptop can still do, most useful first:

1. **Plan the compressed map** (the next big milestone): which areas go in (Vidigal, Leblon-Arpoador beach,
   Lagoa, Pavao-Pavaozinho), how they connect, what gets cut or squeezed, and how the scale rules in
   `DesignPriorities.md` become pipeline settings. Either a written proposal first or straight to a top-down
   prototype layout. Recommended before the walk-around.
2. **`test_leblon_vidigal` ground 20-35 m too high:** old bug. `test_vidigal` replaces it for Vidigal, but the
   Leblon side only exists in the old area; find the cause so bigger areas don't inherit it.
3. **Small polish, better judged after the walk-around:** a water channel (canaleta) along the escadarias; cap
   on how steep a turnaround may be (6 follow lanes > 19%, up to 33%); a short hub stair straight onto the
   escadarias (now ~90 m along the avenue).

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
