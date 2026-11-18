# Handoff: picking the project up on another machine / in a new session

Read this first, then `README.md`, `DesignPriorities.md` and `Tools/GeoPipeline/README.md`.

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

## State on 2026-11-18, latest (MacBook, branch `macbook/lagoa`)

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
