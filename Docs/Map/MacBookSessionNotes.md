# MacBook session notes (2026-11-18)

Work done on the 2017 MacBook Pro (Intel, macOS 13.7), where Unreal cannot run. **Nothing here has been imported
into or checked in the Unreal editor.** Branch: `macbook/beach-blockout`. Read this on the Mac mini before
merging.

## TL;DR for the Mac mini

1. Pull the branch. In `Tools/GeoPipeline`: `.venv/bin/python scripts/fetch_osm.py --aoi full`. Roads and buildings
   come from the Overpass cache; this fills in the missing **reference layer** (`GeoData/raw/osm/full/reference.gpkg`,
   1,867 features: coastline, beaches, peaks, favela outlines). Or copy that file from the MacBook.
2. `scripts/clip_osm.py --aoi test_leblon_arpoador`, then `scripts/fetch_beach.py --aoi test_leblon_arpoador`.
3. `scripts/build_area.py --aoi test_leblon_arpoador`, then `scripts/build_beach.py --aoi test_leblon_arpoador`.
4. Look at `GeoData/exports/test_leblon_arpoador/beach/beach_preview.png` and `beach_report.json`.
5. In the editor: import `GeoData/exports/test_leblon_arpoador/beach/SM_BeachPalette.obj` **with materials** into
   `/Game/Rio/Materials/Blockout/` (one time, like `SM_BlockoutPalette.obj`).
6. Open `L_Rio_Test_LeblonArpoador`, run
   `.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_leblon_arpoador"}'`.
7. Go through the "What to check in the editor" list below.

Rebuild on the mini rather than copying exports from the laptop: the laptop uses older geo packages (see
"Laptop setup"), and `GeoData/` is not in git anyway.

## 1. Cantagalo / Pavao-Pavaozinho slopes: the cause is the grade limit, not the missing favela layer

`BeachTestReport.md` known issue 1 expected the reference layer to fix the 70-166% grades. I re-ran the area with
the reference layer present: **no change** (Rua Sa Ferreira 172%, Rua Saint Roman 139%, Travessa Hortalicia 104%).
The favela flag only changes building heights; it does not touch the road solve.

What actually happens (from `road_graph_source.json` vs the raw DSM):

* The junction of Rua Sa Ferreira and Rua Saint Roman at **UTM E685376 N7457447** is solved to **z = 50.9 m**. The
  DSM there is 7.2 m (flat Copacabana street).
* Rua Saint Roman really climbs Cantagalo steeply. `roads.max_grade.minor = 0.30` is enforced by iteratively
  raising the slope weights up to 1e4, which outweighs the data term (weight 1). The solver cannot keep the
  climb under 30% while following the ground, so it lifts the bottom junction tens of metres. The next 53 m of
  Sa Ferreira then drops 50.9 -> 4.0 m: that is the "172%".

Experiment (scratch rebuild, nothing committed): `max_grade.minor = null`

| | limit 0.30 (current) | no limit |
|---|---|---|
| junction z (DSM 7.2 m) | 50.9 m | -0.7 m |
| Rua Sa Ferreira max grade | 172% | 29% |
| Rua Saint Roman | 139% | 36% |
| Travessa Hortalicia | 104% | 56% |
| minor edges > 30% | 19 | 76 |
| minor edges > 50% | 9 | 27 |
| road vs terrain within 0.5 m | 99.16% | 99.14% |

So simply removing the limit is wrong too: it lets DSM noise through on 57 more streets. Options (not
implemented, needs your call and an editor check on Vidigal as well):

* Cap the slope weight (e.g. 1e2 instead of 1e4) so the limit smooths noise but can never move a node by tens of
  metres; or clamp each node to within N m of the ground estimate as a hard bound.
* Relax `minor` to null only where the *natural* slope is steep (ground gradient > ~0.25 in a 30 m window) or
  inside favela / irregular_settlement polygons.
* Separately: the -0.7 m junction in the no-limit run is below sea level on land. `min_height_m` only clamps the
  data term, not the solution. Worth a final `np.maximum(z, min_height_m)` on non-bridge samples.

Also: `build_area.py` only treats `residential=favela` as informal. The full-area reference layer also has 8
`residential=irregular_settlement` polygons (Tavares Bastos, Morro do Telegrafo, Mangueira, ...). Cantagalo itself
is not mapped as either; only Pavao-Pavaozinho is.

## 2. Hero beach blockout generator (new)

New files: `Tools/GeoPipeline/rio_geo/beach.py`, `scripts/build_beach.py`, `scripts/fetch_beach.py`; new
`beach` section in `config/project.json`; README section "Hero beach". Small change in `unreal/import_area.py`
(raises a clear error when a material slot has no asset instead of silently binding None). **Untested in the
editor.**

What it generates (one `SM_T<i><j>_Beach` mesh per 500 m tile, kind `Beach`, same tags/folders as other tiles):

* Ciclovia (red), at road height + 0.10 m, only where OSM has a cycleway along the front (3.46 km of 3.85 km; it
  leaves the front at Arpoador). Width from the measured cycleway position, clamped 2.2-3.2 m (median ~2.9 m).
* Calcadao (light grey), at road height + 0.15 m, from the ciclovia to the measured sand edge, 3-12 m
  (median ~5.8 m). Flat material only; the wave paving is the later material/decal pass.
* Sand graded from 0.5 m below the calcadao to 0 m at the measured waterline and on to -1 m under the sea plane;
  draped over the generated terrain where that is higher (~4% of vertices).
* Canal mouths cut through the sand with a water strip (M_Sea): Jardim de Alah (lines up with the OSM canal
  polygon at E682911-682929) and the Visconde de Albuquerque outlet at the far west end.
* Palms every ~12 m (jittered, deterministic) along the sea edge of the calcadao, one every ~12 m, 8-12 m tall,
  kept clear of kiosks, Postos and canals (290 after placing Postos and kiosks).
* Kiosks and Postos: see section 3.

Measured cross-section (metres seaward of the sea-side carriageway centreline; median):

| | Leblon | Ipanema W | Ipanema E |
|---|---|---|---|
| ciclovia centre | 6.9 | 6.8 | 6.9 |
| calcadao (OSM footway) centre | 10.1 | 10.6 | 10.2 |
| sand edge | 15.9 | 12.4 | 12.4 |
| waterline | 76 | 107 | 95 |

Design for the compressed map: geometry depends only on a centreline + `beach_profile.json` (stations
normalised 0..1). Phase 3 can call `rio_geo.beach.build_geometry` with the compressed map's beachfront centreline
and the saved profile.

Verification done on the laptop: top-down preview vs OSM sand and coastline (matches along the whole arc after
fixes), OBJ check (no NaNs; no down-facing triangles; walkable faces up; box faces outward), terrain clearance
(terrain never above the calcadao: worst -0.16 m).

### What to check in the editor

* Material slots bind after the palette import (no "missing material" error).
* Calcadao / ciclovia edge against the road ribbon: there should be no visible terrain gap between the road and
  the ciclovia. The road ribbon width comes from `lanes x 3.3 + 1`, so the kerb may not match reality.
* Sand vs terrain: look for green terrain poking through the sand near the waterline (draping covers vertices,
  not every triangle).
* Jardim de Alah: the water strip is a flat quad at z = 0.02; check it against the sea plane and the canal
  inland (no canal geometry upstream of the beach yet).
* Palm and kiosk scale from `PlayerStart_Posto9`.
* Collision: beach meshes use complex-as-simple like the other tiles, palm fronds included. You may want palms
  without collision later.

### Known issues / follow-ups

* **Beachfront road has bumps** (from `build_area.py`, the promenade follows them). Real road is ~flat 1-3 m.
  Peaks along the centreline:

  | station | z | UTM | UE (cm) |
  |---|---|---|---|
  | 0 m (west end, Mirante) | 5.0 m | E681656 N7456639 | (-284443, 436076, 502) |
  | 260 m | 3.0 m | E681894 N7456742 | (-260596, 425792, 299) |
  | 824 m | 2.3 m | E682450 N7456835 | (-205028, 416466, 228) |
  | 1486 m (east of Jardim de Alah) | 3.8 m | E683108 N7456901 | (-139212, 409861, 378) |
  | 2841 m | 2.0 m | E684460 N7456808 | (-3973, 419182, 197) |
  | 3783 m (Arpoador) | 3.8 m | E685355 N7456559 | (85482, 444076, 380) |

  Probably residual rooftop signal from the tall beachfront buildings in the ground model. A fix could pin
  beachfront roads to a smooth low profile, e.g. a `roads.max_height_m` per named road, or a stronger lowland
  opening near the coast.
* West end (first ~40 m by Mirante do Leblon): the waterline flares out to ~120 m following the OSM sand corner.
  Harmless, but check it.
* The ciclovia has no separate kerb/median. The real layout has a low divider; that's a detail pass.
* Kiosk and Posto meshes are boxes (Posto = base + column + orange cabin).

## 3. Postos and kiosks

All six Postos come from OSM, but no lifeguard tags are used along Leblon/Ipanema. Postos 7, 8, 9, 10 and 12 are
`amenity=toilets` nodes named "Posto N"; Posto 11 exists only as a building. `build_beach.py` matches the name
`Posto <number>` in `beach.gpkg` and `buildings.gpkg` ("Subestacao Posto Seis", a substation, is ignored).

| Posto | OSM | UTM (generated position) |
|---|---|---|
| 12 | node 3738890405 | see `beach_report.json` -> `postos` |
| 11 | building 1446049120 | |
| 10 | node 1494285480 | |
| 9 | node 1494282707 | |
| 8 | node 3738890406 | |
| 7 | node 6350256966 | |

Order W->E 12, 11, 10, 9, 8, 7, about 740 m apart. Postos are snapped onto the sand just beyond the calcadao
(the OSM points are on the building/toilet). They are also in `manifest.json` -> `markers` with UE coordinates,
e.g. for checking `PlayerStart_Posto9`.

Kiosks: 21 from OSM (bars / cafes / snack bars / kiosks seaward of the road; restaurants on the building side
are dropped), plus 2 procedural fills where the gap was over 350 m. Spacing ends up roughly 120-150 m. All
kiosks are placed on the sand 1.5 m beyond the calcadao edge, not at the exact OSM point.

`fetch_beach.py` only queries a strip around the three beaches (+150 m). The whole-AOI query timed out on all
three Overpass mirrors six times in a row on 2026-11-18. Trees are not fetched; palms are procedural.

Preview of the final result: `Docs/Map/screenshots/beach_blockout_preview.png`.

## Laptop setup (for reference; the mini needs none of this)

* Clone with LFS smudge skipped and `lfs.fetchexclude '*'`: the laptop never downloads `.uasset`/`.umap`.
* Git LFS 3.8.1 from the prebuilt release (Homebrew on macOS 13 Intel tries to compile LLVM + Rust).
* Python 3.12 venv. pyproj 3.8.0 / rasterio 1.5.2 wheels need macOS 15 and pyogrio 0.13.0 has no Intel wheel, so
  the laptop uses pyproj 3.7.2, rasterio 1.4.3, pyogrio 0.12.1 via `.venv/intel-mac-overrides.txt`
  (`--only-binary :all: --override`). `requirements.txt` is unchanged. The laptop's build of
  test_leblon_arpoador matched the mini's report exactly on building count (11,682).
* GeoData re-downloaded on the laptop 2026-11-18 (Copernicus DEM + full OSM, ~600 MB).

---

# Session 2 (2026-11-18, MacBook): Lagoa Rodrigo de Freitas

Branch `macbook/lagoa`, built on top of `macbook/beach-blockout`. Again **nothing has been seen in the editor.**

## What changed in the pipeline (affects every area)

1. **Inland water** (`build_area.py`, `rio_geo/terrain.py`, config `water`). Before, the lagoon was a flat green
   field at 1.0 m: the sea detector only takes DSM <= 0.05 connected to the map edge, and the lagoon's DSM is
   1.0. This was **11% of the beach test area**. Lagoons, lakes and canals from the reference layer (>= 3,000 m2)
   now get a sloping bed (0.4 m at the shore, 6% slope, max 3 m deep) and flat water meshes per tile
   (`SM_T<i><j>_Water`, kind `Water`, material `M_Sea`, no collision). Islands are holes, so they stay land.
   * Level = median DSM inside the polygon (20 m inset). Lagoa: **1.0 m** (DSM). The real lagoon is probably
     ~0.3-0.7 m above sea level; override in `water.levels_m` if it looks wrong.
   * Narrow bodies take the level of a larger body within 60 m (`connect_m`). The Jardim de Alah canal read
     2.44 m from its banks; it now takes the lagoon's 1.0 m.
2. **Flat-shore cap** (`terrain.cap_flat_shores`, config `water.shore_cap`). Fixes ground contaminated by tall
   waterfront buildings / trees / hillsides in the 10 m DSM. Near lagoons/lakes/canals and large sand beaches
   (>= 20,000 m2: Leblon, Ipanema, Copacabana) ground may rise at most `level + 1.0 + 0.04 x distance`, full
   within 35 m of the water, fading out by 70 m. Land in that zone is also kept at least 0.6 m above the water
   (so a water mesh never floats over its own bank). Plain coastline and small coves (Praia do Diabo, Arpoador)
   are not anchors, so cliffs and rocks are untouched.
   * First try used 60 m + 100 m fade and cut up to 30 m into the hill foot on the Lagoa east shore (Fonte da
     Saudade). Tightened to 35 + 35 m; the DSM there climbs 15 m -> 28 m between 32 and 62 m from the water.
3. New tool **`scripts/preview_area.py --aoi <aoi> --roads "<name>" ...`**: top-down hillshade + water + roads +
   buildings from the exported files, and road height profiles. Output `GeoData/exports/<aoi>/preview.png`.
   `load_terrain_from_manifest` moved from `build_beach.py` into `rio_geo/terrain.py`.

Effect on existing areas (re-import both on the mini):

| | before | after |
|---|---|---|
| Beach area: lagoon | green field at 1.0 m | water at 1.0 m, bed down to -2 m |
| Beachfront road peaks | 5.0 / 3.0 / 2.3 / 3.8 / 2.0 / 3.8 m | 3.9 / 2.2 / 2.2 / 2.3 / 2.0 / 3.8 m |
| Lagoa east shore, Epitacio Pessoa | 11-12 m | ~3 m |
| Lagoa ring roads near water | 1.0 m (= water level) | 1.6 m |
| Vidigal | - | same 46 meshes / 3,947 buildings; ground lowered up to 4.8 m near the Leblon west end |

The beach blockout was re-run on the new terrain: terrain stays below the calcadao everywhere (worst -0.18 m).

## New test area `test_lagoa`

AOI E682000-685000, N7457000-7460000 (3 x 3 km, 36 tiles, 2.5 m grid): the lagoon with both islands, the ring,
Corte do Cantagalo, Jardim de Alah canal, Parque dos Patins, edges of Humaita / Jardim Botanico / the racecourse.
123 meshes, 3.54 M triangles, 8,224 buildings, 2,748 surface road edges. Steepest drivable streets ~44%
(Rua Frei Veloso and Humaita side streets on real hills). No spikes like Cantagalo.

On the mini: create level `/Game/Rio/Maps/L_Rio_Test_Lagoa` (World Partition + georeferencing, same as the
other test levels), then

```sh
.venv/bin/python scripts/clip_osm.py --aoi test_lagoa
.venv/bin/python scripts/build_area.py --aoi test_lagoa
.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_lagoa"}'
```

Preview: `Docs/Map/screenshots/lagoa_preview.png`.

## Open issues (Lagoa)

* **SE corner (Corte do Cantagalo / Humaita), roads 80-215 m from the water, outside the cap:**
  * trunk_link ramp from 15.7 m down to **-0.1 m** in 66 m (24%, below sea level on land), top at
    E684695 N7457951 (DSM 7.0). Clearly wrong.
  * Epitacio Pessoa trunk_link 12.1 m at E684652 N7457763 (DSM 16.2), trunk 9.1 m at E684356 N7458273
    (DSM 19.8). Possibly partly real (the roads climb toward the cut); check against the real site.
  * A 15 m spike in the Epitacio Pessoa profile near a viaduct.
  The road solver does not clamp its *result* to `min_height_m` (only the data term), hence the -0.1 m.
* Borges de Medeiros has small V-dips (1.6 -> 1.0 m) at side-street junctions where the side street lies
  outside the cap zone.
* Canal (1.0 m) meets the beach-mouth water strip (0.02 m) with a 1 m step at the calcadao. A weir there is
  plausible, but check how it looks.
* ~~Lagoa shore details~~: done in Session 3 (rowing stadium / clubs still not modelled).
* Water has no collision: the player falls through to the bed. Fine for a blockout.

---

# Session 3 (2026-11-18, MacBook): Lagoa shore promenade + debug guide

Branch `macbook/lagoa`. **Not seen in the editor.**

## Road-height debugging: see `Docs/Map/UnrealDebugGuide.md`

Problems 1 (SE corner of the Lagoa) and 2 (Cantagalo grades): exact UTM / UE coordinates, what to look for,
fix options and experiment numbers. New untested editor helpers: `unreal/goto.py` (fly the viewport to a spot)
and `unreal/trace_heights.py` (line-trace heights to compare with the pipeline). New switches in
`config/project.json -> roads`, defaults unchanged: `max_slope_weight` (10000) and `clamp_min_height` (false).
On the laptop, option A (`max_slope_weight: 100` + `clamp_min_height: true`) took the Cantagalo junction from
50.9 to 14.3 m and Sa Ferreira from 172% to 45% with almost no extra noise. Recommended, pending a look in Unreal.

## Shore promenade generator (new): `scripts/build_shore.py`, `rio_geo/shore.py`

Runs after `build_area.py` (and after `build_beach.py` in the beach area). Kind `Shore`, one mesh per tile.

* Loop = the lagoon's outer shoreline (7,343 m), islands excluded. Every 5 m a ray to the land measures the OSM
  promenade path ("Ciclovia da Lagoa..." footways, median 9 m from the water), the first drivable road edge
  (median 17 m) and the first building. Saved as `shore/shore_profile.json` with normalised stations, like the
  beach, so the compressed map can reuse it.
* **Walking lane** (2.5 m, water side, new material `M_WalkLane`) and **bike lane** (2.5 m, `M_Ciclovia`),
  centred on the OSM path. Where the road / a building is close they slide towards the water (min 0.8 m from it),
  then narrow (walk to 1.2 m, bike to 2.0 m), only then drop the walking lane. Result: 6.94 km bike /
  6.82 km walk of the 7.34 km loop. Three short gaps where a road or bridge touches the shore (stations about
  650-800, 3,550 and 6,100 m in the preview's profile panel): the real path uses the sidewalk there.
  Lanes sit on the highest terrain under them + 0.10 m, with skirts.
* **Benches**: 13 from OSM (of 26 mapped) + 170 procedural every 35 m, on the water side of the walking lane,
  facing the water (backrest on the land side).
* **Trees**: 110 OSM trees (of 1,216 mapped around the lagoon; the rest are outside the shore band or blocked)
  + 367 procedural in the green strip between the bike lane and the road, one row, two rows where the strip is
  >= 10 m wide; 20% palms (104). Broadleaf = hex trunk + faceted icosahedron crown, 7-13 m.
* **Kiosks**: 4 from OSM (snack bars / kiosks) + 10 procedural (one per gap > 600 m between food venues), land
  side of the lanes or water side when the land side has no room (7 are water side).
* **Lakeside restaurants**: OSM `restaurant` / `bar` on the lake side of the ring road become an 8 x 6 m
  building (`M_Restaurant`) with a 4 m raised deck facing the water (`M_Deck`) and a roof over the building and
  part of the deck (`M_RestaurantRoof`). Placed: **Mediterraneo** and **Caipirinha e File** (Parque dos Patins).
  Skipped: one unnamed restaurant on the north shore (no 10 m of room between the lanes and the road) and one
  point inside a mapped shore building (left to that building).
* Nothing is placed on a drivable road or inside a building (STRtree checks), and objects keep clear of each
  other.
* The beach area (`test_leblon_arpoador`) contains the southern half of the lagoon: run `build_shore.py` there
  too; geometry outside the AOI is dropped (3.97 km of lanes, 10 kiosks, 115 benches, 402 trees).

Needs once in Unreal: import `GeoData/exports/<aoi>/shore/SM_ShorePalette.obj` **with materials** into
`/Game/Rio/Materials/Blockout/` (M_WalkLane, M_Bench, M_TreeTrunk, M_TreeCrown, M_Restaurant, M_Deck,
M_RestaurantRoof). The beach palette must be imported
too (M_Ciclovia, M_Kiosk, M_KioskRoof, M_Palm*).

Order on the mini:

```sh
.venv/bin/python scripts/fetch_beach.py --aoi test_lagoa --set lagoa        # or copy lagoa.gpkg from the laptop
cp ../../GeoData/raw/osm/test_lagoa/lagoa.gpkg ../../GeoData/raw/osm/test_leblon_arpoador/
.venv/bin/python scripts/build_area.py  --aoi test_lagoa
.venv/bin/python scripts/build_shore.py --aoi test_lagoa
.venv/bin/python scripts/build_area.py  --aoi test_leblon_arpoador
.venv/bin/python scripts/build_beach.py --aoi test_leblon_arpoador
.venv/bin/python scripts/build_shore.py --aoi test_leblon_arpoador
# then import_area.py for both
```

Checks done on the laptop: preview (`Docs/Map/screenshots/lagoa_shore_preview.png`), OBJ check (no NaNs, no
down-facing walk/bike faces, tree crowns and trunks all faces outward).

### What to check in the editor (shore)

* Lanes vs the OSM path ribbons from `build_area.py` (2 m `M_Path` ribbons on the same line): the new lanes sit
  ~0.1-0.2 m above them; look for z-fighting or the old ribbon showing at the edges.
* Lanes vs the shore bank and the water at 1.0 m (lanes ~1.7 m).
* Tree/palm scale, density, and whether the faceted crowns read well from the road.
* Water-side kiosks: are they believable or in the way?
* Collision: trees, benches and kiosks have complex collision like everything else.

### Decision for after the drive / walk test: restaurants across the ring road

34 restaurants / bars around the lagoon are on the **far side of the ring road**, in the ground floors of the
apartment blocks (Bar Lagoa, Mr. Lam, Gula Gula, Capricciosa, Jack Daniels Rock Bar, Stravaganze, ...). They are
in `lagoa.gpkg` but not represented: their buildings exist, nothing marks the restaurant. Only about a third of
the points fall exactly inside a mapped building (the rest sit on the sidewalk / entrance). Options:

* markers only: snap each to its building, write name / type / storefront location to `manifest.json -> markers`
  (like the Postos), for gameplay later (entrances, interiors, missions, ambient crowds);
* markers + a simple awning on the building's road-facing side, so they read while driving.

### Known gaps

* Fetch note: `fetch_beach.py` is now general (`--set beach|lagoa`, sets in `config -> feature_sets`); the
  optional tree query is separate so a timeout does not lose the rest.
* No rowing stadium / club structures, piers (22 mapped), Parque dos Patins sports courts (91 pitches mapped) or
  the heliport yet. All are in `lagoa.gpkg` if wanted later.

---

# Session 4 (2026-11-18, MacBook): Vidigal starting area

Branch `macbook/vidigal`, on top of `macbook/lagoa`. **Nothing seen in the editor.** Design decisions are in
`DesignPriorities.md` -> "Starting area: Vidigal".

## New test area `test_vidigal`

AOI E679500-681500, N7455000-7457000 (2 x 2 km, 16 tiles, 2 m grid): the whole Vidigal favela, the Av. Niemeyer
entrance, Praia do Vidigal / Sheraton, the Trilha Dois Irmaos and both summits (plus Rocinha's south edge
beyond the ridge). Create level `/Game/Rio/Maps/L_Rio_Test_Vidigal` like the other test levels, then:

```sh
.venv/bin/python scripts/clip_osm.py     --aoi test_vidigal
.venv/bin/python scripts/fetch_beach.py  --aoi test_vidigal --set vidigal   # POIs + pitch outlines (or copy vidigal.gpkg)
.venv/bin/python scripts/build_area.py   --aoi test_vidigal
.venv/bin/python scripts/build_places.py --aoi test_vidigal
.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "test_vidigal"}'
```

One-time palette imports (with materials, into `/Game/Rio/Materials/Blockout/`): re-import
`SM_BlockoutPalette.obj` (new `M_WaterTank`), and import `places/SM_PlacesPalette.obj` (pitch, concrete, praca,
goal, fence, retaining wall, shop / workshop / player house / hangout, parapet, awnings). The shore palette is
needed too (benches, trees on the praca).

Previews: `GeoData/exports/test_vidigal/preview.png`, `vidigal_infill_preview.png` (scratch render),
`places/hub_preview_0.png`, `places/top_preview.png`; copies in `Docs/Map/screenshots/vidigal_*.png`.

## Pipeline changes (affect every area)

1. **Landmark-summit correction** (planned in `DataSources.md`, now built): `terrain.raise_landmark_peaks`,
   config `landmark_peaks.radius_m`. The 30 m DEM had Irmao Maior at 382 m (surveyed 539), Irmao Menor 292
   (439), Corcovado 527 (710), Pao de Acucar 301 (392). Each listed peak is raised at its OSM summit point to the
   surveyed height with a smooth dome, capped at that height, tallest first. Result: Irmao Maior 537.9 m, Irmao
   Menor 437.1 m. Radii keep the domes off houses (no buildings within 230 m of either Dois Irmaos summit;
   Corcovado / Pao de Acucar have their summit structures inside the dome, which rise with it).
2. **Favela housing infill** (`rio_geo/favela.py`, config `favela_infill`). OSM has 463 buildings in Vidigal;
   reality is thousands. Inside each favela polygon (and each hub's 110 m surroundings), procedural 2-5 storey
   houses on ~7 m lots oriented along the contours fill up to 55% coverage, keeping every street / beco / stair
   clear (1.0 / 0.6 m clearance) and off mapped buildings. 35% get a blue water tank on the laje (new
   `M_WaterTank`). Runs **before the ground model**, and the ground model subtracts the infill footprints too,
   because the DSM already contains the real (unmapped) roofs.
   Vidigal: +4,895 houses, 8% -> 49% coverage; Rocinha (south edge in this AOI): +6,089, 16% -> 55%;
   Chacara do Ceu +408; hub surroundings +230. Deterministic (same houses every build).
3. **Places** (`rio_geo/places.py`, `scripts/build_places.py`, config `places`): flat terraces under every OSM
   pitch outline and the hub's designed spaces (in `build_area.py`, before the road solve; houses and becos inside
   them are removed), then pitch surfaces, goals, 4 m fences, retaining walls, hub buildings and markers.
   Terraces are never below 1.0 m (Sheraton tennis courts read as seabed otherwise).
4. `fetch_beach.py` keeps polygon outlines in a second layer `areas` (pitches). New feature set `vidigal`.
5. `build_area.py` imports grew (shapely, pyogrio); `bbox_box_mesh` helper for the water tanks.

Regression: `test_leblon_vidigal`, `test_lagoa`, `test_leblon_arpoador` (+ beach and shore steps) all rebuild.
Side effects: the infill now also runs there. Pavao-Pavaozinho in the beach area +526 houses (32% -> 54%), the
Lagoa area +82, the old Vidigal test area gets the hub + infill too. This changes the Cantagalo corner from
`UnrealDebugGuide.md` problem 2 (more houses, slightly different ground model): re-check it after re-import.

## Central hub (idealised)

Lower Vidigal, inside the last hairpin of Av. Pres. Joao Goulart before Av. Niemeyer (~40 m). Found by
searching every point within 60 m of the avenue for the lowest relief over a 50 x 40 m box: 5.6 m here, versus
13 m+ of retaining wall at the first site tried (the civic core at ~100 m, 47% cross-slope; kept in config as
`_alternative_site`).

| element | where | z |
|---|---|---|
| Campinho (40 x 19 m pitch, goals, fence, retaining walls) | in the hairpin, below street level | 40.0 m |
| Praca (trees, benches) | in the bend | 43.4 m |
| Padaria, Mercado, Farmacia, Barbearia (2 storeys, coloured awnings) | north side of the avenue's upper leg | 42.6-48.2 m |
| Boteco | east end of the field | 40.8 m |
| Oficina (car workshop) | on Caminho da Boa Vista | 29.9 m |
| Mototaxi stand (canopy) | at the bend | 41.7 m |
| Player house (3 storeys, laje, water tank) | beside the field | 39.1 m |
| Crew hangout (2 storeys, rooftop) | tip of the bend, sea view | 33.2 m |

Markers in `manifest.json -> markers`: `player_home`, `crew_hangout`, `shop_*`, `mototaxi_stand`, plus 19 real
places (Vila Olimpica Adilio, Quadra do Alto, Bar da Laje, Ciosque, Anfitriao, Teatro do Vidigal, Igreja
Metodista, UPP (Sede) `police_base`, Associacao dos Moradores, clinic, Mototaxi Cooper Zona Sul, the three
Niemeyer motels, Sheraton, Bar Lacubaco, Pizzaria 3 Irmaos, Pastel do Ceu, Paladar Nordestino).

To move anything: edit `places.hubs[0]` in `config/project.json` (UTM metres; `rotation_deg` for the whole hub,
`face` = point an element's front looks at), re-run `build_area.py` + `build_places.py`. A layout must not overlap
roads: I used a small check against the road graph while laying it out.

## What to check in the editor (Vidigal)

* Walk the hub: distances, field below the street (retaining wall height between the avenue and the field),
  stairs between street / field / praca (there are **no stair meshes yet**, only the terrain slope + walls).
* Infill from street level: does it read as a favela? It is visibly grid-like from above (7 m lots, contour
  rotation); more irregularity (lot size, merged blocks, varied heights per street) is the obvious next pass.
* Becos: walk a few stairs; houses must not block them.
* Dois Irmaos from Leblon / Ipanema and from the hub: the summit dome shape (smooth, not granite-like).
* The Rocinha edge got infill too (16% -> 55%); check it is not overbuilt.
* Retaining walls (~526 m) and fences: scale.

## Known gaps

* No stair meshes / ramps between hub levels; no house doors; shop interiors none.
* Hortifruti from the first layout was dropped (no room in the new one).
* Lower Vidigal is outside OSM's favela polygon; only the hub's 110 m surroundings got infill there.
* Infill edges at the AOI border: favela parts outside the AOI are not infilled, so the ground model there keeps
  roof signal (only matters for other AOIs that overlap).

# Session 5 (2026-11-24, MacBook): offline OSM source for `fetch_beach.py`

Done on the laptop after the Mac mini hit the Overpass outage (see `HANDOFF.md` -> START HERE). No editor work.

* New `rio_geo/osm_pbf.py` + `fetch_beach.py --source auto|pbf|overpass` (default `auto`: local extract if present
  and covering the bbox, else Overpass). Extract: BBBike `RiodeJaneiro.osm.pbf` (38 MB, data 2026-11-21, extent
  lon -43.58..-42.92, lat -23.08..-22.58, covers `full`) in `GeoData/raw/osm/extract/`. Download command in
  `Tools/GeoPipeline/README.md` -> "Offline OSM extract". New dependency `osmium==4.3.2` (prebuilt wheel, installed
  on the laptop with `uv pip install --python .venv/bin/python osmium`, no override needed).
* Re-ran all three sets from the pbf (~35 s each): `beach` 88, `lagoa` 1,439, `vidigal` 118 features, and copied
  `lagoa.gpkg` into `test_leblon_arpoador/`. Compared with the 2026-11-18 Overpass files: **identical** OSM ids,
  columns, points (0 m shift) and areas.
* One real difference: `beach.gpkg` (22 outlines) and `lagoa.gpkg` (112) now have the `areas` layer. The 11-18 files
  predate it (Session 4 added it). So `build_area.py` / `build_places.py` for `test_leblon_arpoador` and
  `test_lagoa` will now also see pitch outlines there (flat terraces under pitches). Expected, but new: check the
  beach-side pitches (futevolei / volleyball on the sand) do not get odd terraces.
* Known gap vs Overpass (documented in `osm_pbf.py`): a multipolygon whose member ways all lie outside the bbox
  while the polygon covers it is missed. Not the case for any current set.

On the mini: `uv pip install --python .venv/bin/python osmium`, download the pbf (or copy it from the laptop),
then run `fetch_beach.py` for the three sets as in Sessions 3 and 4. It no longer needs Overpass.
