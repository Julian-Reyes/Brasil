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

# Session 6 (2026-11-24, MacBook): hub praca, hub stairs, stepped OSM stairs

Branch `macbook/vidigal-hub` (from `macbook/vidigal`). **Nothing seen in the editor.** Steps 1-3 of the plan in
`HANDOFF.md`. Checked with the new pipeline 3D renderer (`scripts/render_view.py`); renders in
`Docs/Map/screenshots/vidigal_hub_*.png`, `vidigal_osm_steps.png`. Spots to fly to: `UnrealDebugGuide.md` ->
"Vidigal hub, layout 2".

## Hub layout 2 (`config -> places.hubs[0]`)

* **Praca** 34 x 22 m (was 18 x 12) at 40.5 m, centre E680848 N7456049, on the saddle between the avenue's east
  leg (45.6 m at the Rua Nuno Alvares junction, 38.7 m at the bend) and Caminho da Boa Vista (38.4 -> 41.6 m):
  level with the street at its NE and SE corners, retaining walls on the west (to the field) and along the
  avenue's climbing west part. Trees every ~9 m around the edge, benches between, kiosk (new element type
  `kiosk`), boteco on the east end facing the praca, mototaxi stand at the NE corner.
* **Shops** moved from the avenue's upper leg to a row facing the praca across the avenue: Padaria, Mercado,
  Farmacia, Barbearia, + new Lanchonete (`shop_snack`). The old shop sites went back to the real OSM buildings.
* Campinho, player house, oficina, crew hangout unchanged. Per-item `rotation_deg` now allowed (praca and shops
  follow the avenue, -3 / -5 deg, instead of the hub's 19 deg).

## Stairs (`rio_geo/stairs.py`)

* **Hub stairs** (`places.hubs[].stairs`, path + width): praca -> campinho (5 m wide, 11 x 17 cm), junction ->
  praca (21 x 16 cm), Caminho da Boa Vista -> campinho (4 x 14 cm), avenue -> campinho (43 x 17 cm, runs down
  along the field's north wall). End heights are resolved in `build_area.py` after the road solve (terrace /
  road surface / terrain), the terrain is ramped under each stair, profiles go to `stairs.json`, and
  `build_places.py` builds treads (`M_Stair`, new places palette entry: **re-import `SM_PlacesPalette.obj`**),
  stepped cheek walls and retaining walls where the stair cuts into the slope. Campinho fence and walls open
  only where a stair enters (last 2.5 m) and at the new west `gates` entry toward the player house.
* **OSM `highway=steps`** are now solid treads along the solved profile instead of flat ribbons
  (`roads.stepped_stairs`, 17 cm risers, hysteresis so near-flat runs do not flicker): Vidigal AOI 153 edges,
  4.4 km, 7,244 steps; 14 edges have a tread under 25 cm (DSM-steep). Same `M_Steps` material, in the Roads tiles.

## Terrace fixes (affect every area)

1. **Hills inside pitches** (pre-existing since Session 4): the road carve ran after the terraces were flattened
   and its blend zone pulled the ground back up inside them; the campinho had terrain from 36.7 to 45.0 m
   poking through the pitch. Terraces are now re-flattened after the road carve (`reflatten_terraces`).
2. **Streets cut out of pitch aprons:** OSM pitch `way/1449902183` (E681210 N7456549, 98 m) had its 2 m apron
   over a service road 13 m lower (the slab floated over the road). Drivable corridors are subtracted from
   terrace outlines first (`trim_terraces`).
3. **Uphill edges:** on a 2 m grid one cell at the boundary is always a slope; it used to sit inside the terrace
   as a wedge over the apron. The ring of nodes just outside a terrace is now lowered to the terrace level (not
   under streets / paths), retaining walls sample the ground beyond it and get a 4 m top ledge covering the
   trench. Where a stair runs along a terrace wall, the wall top follows the stair line instead.

## Open / to check in the editor

* Walkability of all stairs with the template character (step height 45 cm); flush ends.
* Wall ledges next to streets: gaps or ledges floating above the road where the ground beyond the street is
  higher (the ledge height is sampled 4.2 m out).
* The long avenue -> campinho stair: the strip between it and the avenue is jagged terrain (2 m grid) behind
  a retaining wall; may want a cleaner wall / planter later.
* Steep OSM steps (tread < 25 cm): fine as blockout; flag any that are unwalkable.

## Regression (all four test areas rebuilt on the laptop)

`test_lagoa` (+ shore, places), `test_leblon_arpoador` (+ beach, shore, places), `test_leblon_vidigal` (+ places)
all build. Street corridors trimmed out of 18 pitches (Lagoa) / 13 (beach area), terrace corrections < 1 m there;
786 / 855 OSM steps converted. **Issue found:** the old `test_leblon_vidigal` area's ground is 20-35 m higher
than `test_vidigal` at the same points (campinho 62.0 vs 38.7 m, a point 250 m NW 148.7 vs 112.8 m), so the hub
stairs there come out absurd (127 steps at 9 cm run; `build_area.py` now logs a WARNING for that). The
difference predates this session (its own ground model; `GeoData/raw/osm/test_leblon_vidigal/` has no
`vidigal.gpkg` and an older clip, likely no favela infill subtracted). Treat `test_vidigal` as the Vidigal area;
consider retiring `test_leblon_vidigal` or re-clipping it.

# Session 7 (2026-11-24, MacBook): favela infill v2 (less grid-like)

Branch `macbook/vidigal-infill` (from `macbook/vidigal-hub`). Step 4 of the Vidigal plan. **Nothing seen in the
editor.** Before / after renders (same viewpoints): `Docs/Map/screenshots/vidigal_infill_before_*.png`,
`vidigal_infill_after_*.png` (+ `_after_top.png`); spots in `UnrealDebugGuide.md` -> "Vidigal housing infill v2".

What changed (`rio_geo/favela.py`, config `favela_infill`, rewritten):

* **Placement:** frontage first: houses line both sides of every street, beco and stair in the zone, aligned
  with it, from a size mix (small 3.5-5 m / medium 5-7 m / wide merged 7-11 m along the street; 42 / 43 / 15%),
  half of them at the kerb, the rest set back up to 1.2 m, wall to wall (0.2-0.35 m) with a 0.9-1.6 m passage
  12% of the time. Then the interior is filled from a jittered 3 m candidate grid, contour-aligned. Trimmed
  against the free space and neighbours; slivers under 2.5 m dropped. The old 7 m lot grid is gone.
* **Heights:** 2-4 storeys base (skewed low), +-1 from a smooth 45 m noise field (neighbouring blocks differ),
  +1 for half the houses on car streets, ~3% 5-7 storey "predios" (footprint >= 30 m2), slivers <= 3.
  Result: ~86% at 2-4 storeys, ~6% at 5. Around the hub: max 3, no towers (`infill_max_levels`).
* **Looks:** facade material per house: bare brick 52%, grey render 24%, painted 24% (white, yellow, blue,
  green, pink, teal) -> **new blockout palette materials `M_HouseRender`, `M_HousePaint*`: re-import
  `SM_BlockoutPalette.obj` with materials.** Laje extras: parapet 35%, small roof room (puxadinho) 20%, water
  tank 35% (on the roof room if there is one, otherwise toward a corner instead of dead centre). OSM-mapped
  favela houses get the same looks (`style_mapped_informal`).
* **Hub:** designed hub spaces keep a 4 m house-free margin (`places.hub_clear_margin_m`; a tall house had
  squeezed into the strip between the avenue and the praca).

Numbers (test_vidigal): Vidigal +5,170 houses, now reaches 55% coverage (was 49%: the 7 m grid ran out of
lots); Rocinha +5,717; Chacara do Ceu +382; hub surroundings +242. 2.49 M triangles total (was 2.37 M).

Regression: `test_lagoa` (+ shore, places) and `test_leblon_arpoador` (+ beach, shore, places) rebuild. Pavao-
Pavaozinho gets +119 houses in the Lagoa area and +512 in the beach area (was +526), both now at 55% coverage.

# Session 8 (2026-11-25, MacBook): Vidigal street details (step 5)

Branch `macbook/vidigal-details` (from `macbook/vidigal-infill`). Step 5 of the Vidigal plan. **Nothing seen in
the editor.** Renders: `Docs/Map/screenshots/vidigal_details_{street,beco,aerial,lookout_view}.png`; spots in
`UnrealDebugGuide.md` -> "Vidigal street details".

What changed:

* **Narrow car access** (`roads.favela_lanes`, `rio_geo/roads.py narrow_in_zones`, called at the top of
  `build_area.py`): drivable streets mostly (>= 50%) inside an OSM favela polygon with no mapped `width` get a
  `width` tag: residential / unclassified 4.2 m, living street 3.4 m, service 3.0 m; Av. Pres. Joao Goulart 6.0 m
  (by name; it was 7 m like a city street). Streets within 70 m of a hub centre keep their width. Vidigal AOI:
  7.1 km residential, 1.6 km service, 0.6 km living street narrowed. Everything downstream (infill clearance, road
  carve, poles) uses the new widths.
* **New script `scripts/build_details.py`** (run after `build_places.py`; `rio_geo/details.py`, config `details`):
  reads `houses.gpkg` (new `build_area.py` output: every placed footprint with ground / roof heights, informal
  flag, frontage), `road_graph_source.json` and the Terrain tiles. Writes `details/SM_T*_Details.obj` (complex
  collision) and `details/SM_T*_Wires.obj` (no collision), `details/SM_DetailsPalette.obj` (**one-time import
  with materials**), `details/details_report.json`, and `lookout` markers in the manifest.
  * **Doors:** each favela house gets one on the wall facing its nearest street / beco / stair whose outside is
    not another house (probe 0.25 and 0.7 m out); 0.9 x 2.1 m, bottom at the ground 0.4 m outside (clamped to the
    wall's ground range), 7 cm proud, with a concrete step. On car streets 30% of the houses with a >= 3.4 m wall
    get a 2.6 m roller shutter instead. Vidigal AOI: 12,155 doors, 487 shutters, 354 houses enclosed (no free
    wall, no door).
  * **Poles and wires:** concrete poles (0.24 m, 8.5-9.5 m, crossarm, 20% with a transformer) every ~22 m on one
    side of each favela car street, on the shoulder (never on a carriageway, a footway / stair, within 0.25 m of
    a house, or on hub terraces / stairs / lookout decks), >= 11 m apart. Each pole links to up to 3 neighbours
    within 42 m (shortest first) with 3 power lines on the crossarm and 2-5 sagging telecom cables 3 m lower, plus
    0-2 short loops hanging off the pole. Spans are dropped if a cable would pass through a roof or dip below 4 m
    (power) / 2.6 m (telecom) over the ground. Service drops: 60% of the favela houses within 24 m of a pole get a
    cable to their wall (max 12 per pole). Becos / stairs: anchors on the house walls every ~7 m, alternating
    sides, 3-4.6 m above the path; 1-3 cables between consecutive anchors; the chain is tied to the nearest pole
    (<= 30 m). Cables are 2.4 cm square tubes, 8 segments per span. Vidigal AOI: 344 poles (35 without a power
    span), 286 spans, 858 power + 898 telecom cables, 460 drops, 1,195 beco cables; 176 k triangles in Wires.
  * **Lookouts:** sited in `build_area.py` (`lookout_sites`, after the road solve, before the building meshes):
    samples every 6 m along becos / stairs / lanes in the favela zones, on the side where the ground falls > 2 m
    within 4 m; scores a 70 deg fan of 9 rays from the deck's outer edge over terrain + roofs (`obstacle_surface`):
    a ray counts if nothing rises > 1 deg above the eye (1.6 m) and it ends on low ground (< 8 m: sea / beach).
    Best first, >= 150 m apart, not within 90 m of a hub centre; a 5 x 3 m deck may replace up to 2 informal houses
    (they are dropped from the meshes) or stand over lajes lower than the path, never a formal building, another
    street or a hub element. Writes `lookouts.json`; `build_details.py` builds the deck (solid down to the ground),
    a 1.05 m railing on the three outer sides and a bench facing the view. Vidigal AOI: 4 lookouts (7 houses
    removed): Av. Pres. Joao Goulart 124 m and 226 m, Rua Doutor Sobral Pinto 123 m, a stair near the bottom 15 m.
* `render_view.py`: draws the kinds Details / Wires, and the sea (empty pixels below the horizon).
* `rio_geo/favela.py infill_zones()`: the favela + hub-surroundings zone list, now shared by `build_area.py` and
  `build_details.py`.

Numbers (test_vidigal): `build_area.py` 13,895 buildings, 2.49 M triangles (unchanged); details 256 k + wires
176 k triangles. Infill: Vidigal +5,141 houses (was +5,170; narrower lanes, same 55% coverage).

Tuning notes: the first lookout pass only scored views and found 1 site: almost every downhill edge is lined with
frontage houses whose roofs stand 3-12 m above the path, so a deck never fitted. Hence siting in `build_area.py`
with house removal. Pole spacing 30 m left 19% of the poles without a span (chords across bends hit houses);
22 m brings it to 10%.

## Open / to check in the editor (step 5)

* Door heights on steep slopes; doors on houses whose free wall faces a 2 m passage, not the street.
* Cars on the 4.2 m lanes (and the 3.0 m services): passing, turning at dead ends (no turning heads yet).
* Thin cables at distance (aliasing); maybe a minimum on-screen width or a fade.
* Lookout views, and whether the four sites are good gameplay spots (they're picked by view only).

## Regression (step 5)

`test_lagoa`, `test_leblon_arpoador` (build_area + build_places + build_details; beach / shore not re-run here)
and `test_leblon_vidigal` all build. Pavao-Pavaozinho: lanes 1.1 / 1.6 km narrowed (living streets), +118 / +511
houses (55%); Lagoa area 120 doors, 9 poles, no lookout; beach area 733 doors + 105 shutters, 55 poles of which
**27 without a power span** (steep, dense, few car streets: chords hit taller houses; they still feed 85 drops),
2 lookouts (76 m and 62 m, 4 houses removed). `test_leblon_vidigal`: 4.8 km narrowed, 2 lookouts, 183 poles.
Carriageway conflicts 21 / 32 / 10 (OSM-mapped buildings).

# Session 9 (2026-11-25, MacBook): Vidigal signature escadarias (step 6)

Branch `macbook/vidigal-escadarias` (from `macbook/vidigal-details`). Step 6 of the Vidigal plan, done before the
walk-around test at the user's request (they will judge it in the walk-around). **Nothing seen in the editor.**
Renders: `Docs/Map/screenshots/vidigal_escadaria_{padre_italo,niemeyer_bar,top}.png`,
`vidigal_escadarias_aerial.png`; spots in `UnrealDebugGuide.md` -> "Vidigal signature escadarias".

Choosing the routes: the Vidigal OSM steps form branching networks rather than single stairs (the longest is
933 m in 37 ways). The hub (44 m) has no stair climbing straight out of it; the stair networks start below it.
Searched the pedestrian graph for uphill routes from near the hub, then fixed the routes by OSM node id:

* **Escadaria do Niemeyer** (`nodes` 3209675931 -> 3209675879 -> 3209672838): Av. Niemeyer (0.8 m) to the
  street just below the hub (28.8 m), 200 m, all OSM steps. Brazil-flag paint: green / yellow alternating steps.
  1 bar.
* **Escadaria Padre Ítalo** (`nodes` 3209675879 -> 3197556361): forks off the Niemeyer stair at 22 m, climbs Rua
  Padre Ítalo Coelho (a pedestrian lane, 33% at its steepest; now steps), then the stairs above to 138.4 m.
  547 m (steps 306 m, pedestrian 218 m, footway 23 m). Rainbow paint in 1.02 m height bands (6 risers per
  colour). 2 bars.

Names are idealised (OSM has no names for these ways). New config `escadarias`; new module
`rio_geo/escadarias.py`.

How it's built:

* **Top of `build_area.py`** (`resolve_routes`, after the lane narrowing): cheapest path between the route nodes
  over the pedestrian graph (cost per metre: steps 1, footway / path / pedestrian 1.5, car streets 6-8); its ways
  get `highway=steps`, `width=3.0` and an `escadaria` tag. So the infill lines them at 3 m, the profile solve
  treats them as stairs and the carve covers the full width.
* **After the road solve (`build_area.py`):**
  * buildings intersecting the corridor (width / 2 + 0.2 m) are dropped (3 + 4);
  * 85% of the favela houses within 2 m get a painted facade (51 + 132);
  * landings: a 1.6 m flat run every 2.4 m of climb where the grade is >= 12%. The 3 m of profile on each side is
    stretched so heights meet the solved line again: up to ~27% steeper, shortest tread 25 cm. 8 + 34 landings;
  * `stairs.apply_ramp` sets the ground under the stair (+0.3 m each side) to the stair line - 0.3 m (terraces
    untouched): the first renders showed terrain through the tread edges on cross-slopes;
  * writes `escadarias.json` (edges oriented base -> top, profiles with landings, landings). The stair edges are
    no longer meshed in `build_area.py` (they are counted in `stepped stairs`, without treads).
* **`build_details.py`** (`escadarias.parts`), all in the `Details` tiles (complex collision):
  * solid treads 60 cm deep, painted per tread (alternating along the route) or by height band;
  * handrail on the left going up (posts every 2 m, 95 cm);
  * lamp posts on the right every 18 m (3.8 m, arm, lamp head `M_Lamp`), skipped near bars;
  * a bench on every 2nd landing;
  * murals: a flat panel on 50% of the >= 2.5 m house walls facing the stair (up to 2.8 m high, 5 colours);
  * bars at landings spread along the route: counter, red awning, sign on the house wall beside the landing, a
    plastic table with two stools;
  * markers kind `escadaria` (base, topo) and `bar`.
  * New palette materials (the one-time `SM_DetailsPalette.obj` import covers them): `M_Esc{Red,Orange,Yellow,
    Green,Blue,Purple}`, `M_Lamp`, `M_Mural1-5`, `M_BarCounter`, `M_Awning`, `M_BarSign`, `M_Plastic`.

Numbers (test_vidigal): Niemeyer 168 treads, 197 m rail, 10 lamps, 4 benches, 19 murals, 1 bar; Padre Ítalo 727
treads, 550 m rail, 29 lamps, 17 benches, 42 murals, 2 bars. Details 276 k triangles (was 256 k); buildings
13,890; area 2.48 M triangles.

## Open / to check in the editor (step 6)

* Walk both stairs end to end with the template character, especially the steep upper part (35-46% before the
  landings).
* The handrail runs along one side only, so the stair stays clear across its width; the bar tables narrow their
  landings to ~1.3 m.
* Murals are flat colour panels and can cover part of a door; real mural textures later.
* No water channel (canaleta) yet. Lamps don't light anything (blockout material only).
* The hub-to-stair link is the street (Av. Pres. Joao Goulart, ~90 m) from the Niemeyer stair's top: is that
  close enough, or should a hub stair connect directly?

## Regression (step 6)

`test_lagoa` and `test_leblon_arpoador` (build_area + build_places + build_details) build unchanged; both routes
are logged as skipped there (nodes not in the AOI). `test_leblon_vidigal` builds both escadarias, but on its
known 20-35 m higher ground model (Niemeyer 36.1 -> 62.6 m, Padre Ítalo 63.4 -> 133.0 m, 24 landings); use
`test_vidigal`.

# Session 10 (2026-11-25, MacBook): turnarounds at dead ends, every pole linked

Branch `macbook/vidigal-lanes-poles` (from `macbook/vidigal-escadarias`). Done before the walk-around test at the
user's request. **Nothing seen in the editor.** Renders: `Docs/Map/screenshots/vidigal_turnaround_{flat,slope,
steep}.png`, `vidigal_poles_linked.png`; spots in `UnrealDebugGuide.md` -> "Turnarounds and pole lines".

**Turnarounds** (new `rio_geo/turnarounds.py`, config `roads.turnarounds`, in `build_area.py` right after the hub
stairs, before the escadarias):

* Dead ends: nodes of degree 1 in the drivable graph, on residential / unclassified / living street / service
  edges <= 5 m wide and >= 15 m long, inside the favela polygons (+10 m), not within 20 m of the AOI edge (clipped
  edges), and only where the edge really ends at the node. Vidigal AOI: 39 drivable dead ends, of which 16 qualify
  (the rest are 7 m formal streets, short stubs or at the AOI edge).
* Bulb: a disc of radius 5.5 m (4.5 m if 5.5 does not fit) whose far edge is 1.5 m past the end node, on a plane
  through the end with the lane's grade over its last 6 m (level across). Not built where it would hit a formal
  building, a hub terrace / element / designed stair, an escadaria, or another street more than 0.4 m off the
  plane. Informal houses within 0.3 m are dropped.
* The heightfield is set to the plane minus the road sink under the bulb, blending out over 2.5 m (terraces
  protected). Mesh: a 24-sided fan in the Roads tiles, `M_RoadMinor`, 3 mm under the lane ribbon.
* `turnarounds.json` (bulb polygons, centre, z, grade); `build_details.py` adds them to the pole blockers.
* Vidigal AOI: 16 of 16 built (all 5.5 m), 75 houses dropped. Grades range from level to 33% (6 above 19%).

**Every pole linked** (`rio_geo/details.py poles_and_wires`, config `details.wires.poles.raise_steps_m`,
`max_height_m`). Before: each pole tried chords to its 4 nearest neighbours; chords across a bend hit a taller
house, so 35 / 344 poles (Vidigal) and 27 / 55 (Pavao-Pavaozinho) had no power line. Now spans are chosen first
and the cables built after (pole heights can still change):

1. along each street, consecutive poles in order (the street is clear of houses);
2. then nearest neighbours across junctions (shortest first, <= 3 spans per pole; a redundant span between two
   already-fed poles is only added if it fits as is);
3. a blocked span is retried with both poles raised by 1 / 2 / 3 m (max 12.5 m), then (same street, > 8 m apart)
   via a new pole on the shoulder at the street point halfway, recursively twice;
4. a pole still without a span tries its 5 nearest poles within 63 m; if none works it is removed.

Vidigal AOI: 395 poles (was 344), 390 spans, 24 raised, 61 added, 2 removed, **0 unlinked**; drops 448.

## Regression (Session 10)

All four areas rebuild (build_area + build_places + build_details; beach / shore not re-run). No unlinked poles
anywhere:

* `test_lagoa`: 3 turnarounds (16 houses), 8 poles (1 removed).
* `test_leblon_arpoador` (Pavao-Pavaozinho): 11 turnarounds (45 houses); 51 poles, 14 raised, 12 added, 8 removed.
  Before this session, 27 of its 55 poles had no power line.
* `test_leblon_vidigal`: 10 turnarounds (48 houses); 194 poles, 1 removed.

# Session 11 (2026-11-25, MacBook): beach area look check

Rendered the Leblon / Ipanema beachfront with `render_view.py` (no Unreal). Two small fixes:

* `build_beach.py` replaced **all** manifest markers with the Posto markers, so running it after
  `build_places.py` / `build_details.py` dropped hub and lookout markers. It now replaces only `kind == "Posto"`.
  The handoff build order (beach before places / details) was not affected.
* `render_view.py` now loads the beach and shore palettes (sand, ciclovia, palms, kiosks and Postos rendered in a
  fallback colour before).

Laptop-only state: the 2026-11-25 rebuild of `test_leblon_arpoador` (for the Pavao-Pavaozinho turnarounds) ran
`build_area` / `build_places` / `build_details` only, so its manifest had no Beach / Shore actors. Re-ran beach,
shore, places and details. On the mini, follow the full checklist order in `HANDOFF.md`.

What the beach area has now: grey extruded buildings (12,104; 73% with OSM heights, flat roofs, one colour);
sand, red ciclovia, grey calcadao, 290 palms, 23 kiosks, Postos 7-12. Missing: umbrellas / chairs / barracas,
footvolley nets, building facades, wave paving, Copacabana (outside every AOI; only the Arpoador edge is in).
Seen in the renders: terrain poking through the sand near the waterline and around Posto 9 (known issue).

# Session 12 (2026-11-25, MacBook): beach life, formal facades, beach terrain, Copacabana

Branch `macbook/beach-life` (on top of `macbook/vidigal-lanes-poles`). Nothing opened in Unreal. Renders:
`screenshots/beach_life_posto9_aerial.png`, `beach_life_posto9_sand.png`, `ipanema_facades_aerial.png`,
`copacabana_aerial.png`, `copacabana_posto3.png`. Spots: `UnrealDebugGuide.md` -> "Beach life, facades, Copacabana".

## 1. Beach life (new `rio_geo/beach_life.py`, `beach.life` in the config)

Written by `build_beach.py` as a new kind **`BeachLife`** (`beach/SM_T<i><j>_BeachLife.obj`, **no collision**):
barracas (vendor stall: counter, posts, pyramid roof in the stall's colour, chair stack) every ~36 m on a line
~22% of the way across the sand (>= 20 m from the calcadao); each owns a field of umbrellas (pole + tilted
octagonal canopy), 0-2 folding chairs facing the sea, and towel-only spots, mostly in the stall's colour;
volleyball / footvolley courts (tape lines, posts, net) on the open sand next to the calcadao: OSM
`sport=*volley*` pitches first (2 in Ipanema), then one every ~160 m. Density peaks at the busy Postos
(`posto_density`: Posto 9 0.95, Posto 10 0.85, Posto 8 0.75, ...; base 0.30). New materials (`M_Umbrella*` x6,
`M_ChairFrame`, `M_Barraca`, `M_CourtLine`, `M_NetPost`, `M_Net`) are in `SM_BeachPalette.obj`.

| area | barracas | umbrellas | chairs | towels | courts | BeachLife triangles |
|---|---|---|---|---|---|---|
| Leblon-Arpoador | 72 | 1923 | 2894 | 650 | 2 OSM + 18 | 97 k |
| Copacabana-Leme | 52 | 1976 | 2954 | 611 | 16 | 98 k |

## 2. Formal-city buildings (`rio_geo/buildings.py formal_style`, `buildings.formal_style`)

Non-favela buildings get a facade colour (`M_FacadeWhite/Cream/Beige/Grey/Concrete/Salmon/Blue`, weighted), a
3.5 m darker ground-floor band (`M_GroundFloor`, buildings >= 7 m), a 1 m parapet and, on buildings >= 12 m
with >= 120 m2 roofs, a machine room / reservoir box (`M_RoofConcrete`). New slots are in
`SM_BlockoutPalette.obj`. Also: `building=roof` canopies are 3 m (`roof_canopy_m`; were 6-12 m blocks on the
calcadao: Copacabana's kiosk roofs) and formal footprints < 25 m2 are one storey (`tiny_footprint_m2`).
Windows / balconies are left for the Unreal material pass (per-floor slabs would cost millions of triangles).

## 3. Beach terrain and beachfront roads

* **Bug found:** `cap_flat_shores` used the lagoon "bank floor" (level + 0.6 m) for beaches too. Outside a beach
  polygon is partly open sea, so a 30-40 m strip of fake land at +1.1 m stood in the surf (the "terrain poking
  through the sand near the waterline" from earlier notes). Meanwhile the sand itself was classed as sea by the
  DSM test: a 2-5 m pit under the beach. Now beaches get no floor; their sand is a ramp from `beach_top_m` 0.6
  (land side) to `beach_bottom_m` -0.4 (sea side). Seabed beyond the sand -5 m.
* **Beachfront avenues** (`roads.flat_fronts`): the ground the road solver follows is capped at 1.6 m for
  Delfim Moreira / Vieira Souto / Francisco Bhering / Atlantica. Ipanema-Leblon centreline was 1.0-3.9 m, now
  1.0-3.3 m (the 3.3 is the west end by the Mirante, where the hill foot is kept; the rest is ~1.0-1.6).
* **Beachfront ground cap** (`terrain.cap_front_roads`): behind Copacabana's towers the ground model kept 10-37 m
  of rooftop signal 30-80 m inland (flat streets in reality). Within 150 m (+60 m blend) of Delfim Moreira /
  Vieira Souto / Atlantica the ground may rise at most 1.6 m + 5% x distance, **scaled by formal-building
  coverage** (60 m window, full at 30%) and never inside natural-cover or favela polygons: a first version cut
  43 m off the foot of Dois Irmaos, 35 m off Morro do Leme and 30 m off Chapeu Mangueira; the coverage rule keeps
  those. `report.json -> shore_cap -> front_roads_largest_cuts` lists where it cut most.
* **Not fixed (needs your call):** Posto 6, Av. Atlantica x Rua Francisco Sa still humps to ~11 m. Same mechanism
  as Problem 2: Francisco Sa (secondary, 12% grade limit) ends at the Cantagalo foot at ~35 m and the solver
  lifts the junction. Option A (`max_slope_weight: 100`, `clamp_min_height: true`) makes Atlantica 1.0-3.7 m
  end to end (scratch run, not committed as default). Decide after checking Problem 2 in the editor.

## 4. Copacabana / Leme test area (new AOI `test_copacabana`)

`bbox_utm [685000, 7456500, 688500, 7460000]`: Posto 6 / Forte to Leme, back to Cantagalo, Pavao-Pavaozinho and
Barata Ribeiro. `build_beach.py` now takes per-AOI overrides (`aois.<aoi>.beach`: `front_roads`, `beach_names`,
`feature_set`); new feature set `copacabana` (`copacabana.gpkg`). Postos 1-5 from OSM (no Posto 6 in OSM);
unnamed lifeguard ways within 30 m of a numbered Posto are merged (was 9 Postos). 47 kiosks from OSM, 279 palms,
calcadao capped at 12 m wide (measured wider), sand 114 m median to the waterline. 4,293 buildings, 4.54 M
triangles in the area tiles. Pavao-Pavaozinho gets favela infill, doors and poles here too.

## Also

* `build_beach.py` keeps non-Posto markers (Session 11) and drops old `BeachLife` actors on re-run.
* `render_view.py` draws `BeachLife` and the facade / beach palettes.
* All four test areas rebuilt; Vidigal numbers unchanged except the formal-building styles.

## Open / to check in the editor

* Beach life: umbrella scale (r ~1.1 m, h ~2.15 m) next to the template character; chairs readable; is no
  collision OK (walking through umbrellas) or do barracas need simple collision; frame cost (~97 k triangles of props per area).
* Courts: net heights 2.43 m volley / 2.2 m footvolley; courts clear of palms / kiosks.
* Facades: colour mix from the street and from the sea; ground band reads as shopfronts / pilotis; machine rooms
  not poking through parapets.
* Sand edge: with the new ramp the terrain under the beach is lower; look for gaps under the calcadao skirt (0.6 m)
  where the calcadao is up to 3.9 m above the terrain in Copacabana (`terrain_above_calcadao_m` min -3.89).
* Copacabana: the Posto 6 hump (above), the Tunel Novo portal slope (cut 33 m to 7.6 m: real hill or residue?).

# Session 13 (2026-11-26, MacBook): Pedra do Arpoador and the Forte de Copacabana

Same branch `macbook/beach-life`. Renders: `screenshots/arpoador_rock.png`, `forte_copacabana.png`,
`forte_fortress_roof.png`. Spots: `UnrealDebugGuide.md` -> "Arpoador and the Forte".

## What was wrong (since the first beach build)

Both headlands were flat at ~1 m. Two causes:
* **Copernicus has no Pedra do Arpoador**: its water mask puts the whole rock at 0.0 m (the DSM is 0 over
  E685300-685560 N7456330-7456470). Nothing to recover from the data.
* **The lowland opening erased the Forte**: the DSM still had 12-18 m on the peninsula, but step 2 of
  `ground_from_dsm` (min with a 150 m morphological opening, meant for rooftop residue on the Leblon / Ipanema
  plain) removes anything narrower than 150 m. The hill between Arpoador and the Forte (up to 43 m in the DSM) was
  also cut.

## Fixes (`headlands` in the config)

* **Coastal headlands are protected**: natural cover (wood, scrub, bare rock, ...) from the reference layer plus
  military land / forts / museums from the feature sets, within 250 m of the coastline, skip the lowland opening,
  the flat-shore cap and the beachfront ground cap. 56 polygons in Leblon-Arpoador, 62 in Copacabana, 4 in Vidigal
  (Av. Niemeyer coast; Vidigal numbers unchanged), 0 in Lagoa.
* **Pedra do Arpoador rebuilt** (`terrain.rebuild_headland`) from its OSM `bare_rock` outline (+ the adjacent
  unnamed rock) with the OSM peak Ponta do Arpoador (16 m): steep face from the waterline, highest towards the
  peak; summit 14.0 m at E685440 N7456365 (the peak node itself is 16 m outside the mapped rock, in the water).
  The faces below sea level look stepped in the renders (no sea plane there); the sea covers them in Unreal.
* **Forte peninsula** keeps its DSM relief: 12-17.5 m along the spine, low shore paths, the fortress at the tip
  on ~1 m rock.

## The Forte de Copacabana complex (new `rio_geo/fort.py`, run by `build_places.py`)

New feature set `forte` (`fetch_beach.py --set forte`, both beach areas): fort, museum grounds, military land,
lawns, rock / wood / scrub, gates / sally port / entrances, pier, helipad, fire station, Clube dos Marimbas.
* **1914 fortress** (OSM way 371926003): 6 m massive block, `M_FortConcrete`, walkable roof (OSM has footways on
  it), 1 m parapet, cupolas (`M_FortTurret`) with guns (`M_FortGun`) aimed out to sea. Config has 4 cupolas;
  2 fit inside the footprint with the current spacing. Height / cupola layout are guesses: check photos.
* **Museum grounds buildings**: low colonial blocks (1-2 storeys, `M_FortColonial`) instead of random facades;
  the fire station and Clube dos Marimbas at the entrance are 2-3 storeys (were 40 m default towers).
* **Perimeter wall** (`M_FortWall`, 3.2 m) along the grounds outline, clear of the coastline, buildings and the
  gate / sally port / entrances: 833 m, reads as ramparts along both shores with the opening at Posto 6.
* **Lawns** (4, `M_Lawn`) and the **fishermen's pier** at Posto 6 (`M_Pier`).
* Markers: Forte de Copacabana (fort), Confeitaria Colombo, Cafe 18 do Forte (cafe), Pedra do Arpoador (lookout).
* The complete fort is in `test_copacabana`. `test_leblon_arpoador` ends at E686000, inside the fort: it has the
  west part only (no fortress; its fort stats report a meaningless fortress_top).

Also: `test_copacabana` now spans N7456000-7460000 (includes the Pedra); `build_area.py` no longer resets
building materials during the favela setup (landmark materials were lost).

## Open / to check in the editor

* Pedra do Arpoador: shape and height from Ipanema (sightline in `DesignPriorities.md`), walking up the paths.
* Forte: fortress height and cupolas vs photos; is the wall in the right places (real ramparts / sea walls);
  colonial buildings' size; the oval service road around the parade ground; pier height.

# Session 14 (2026-11-26, MacBook): Copacabana area extended to Leme point, Babilonia, Urca, Pao de Acucar

`test_copacabana` is now E685000-690000 x N7456000-7461000 (5 x 5 km; was E688500 / N7460000). Renders:
`screenshots/copacabana_leme_urca.png`, `babilonia_chapeu_mangueira.png`.

* Peaks vs OSM `ele` (model max within 40 m): Pao de Acucar 392.5 / 392 (summit correction +93 m), Morro da Urca
  219.9 / 220, Morro da Babilonia 238.1 / 238, Morro dos Urubus 124 / 126. **Short** (30 m DEM cuts summits):
  Morro do Leme 124 / 150, Morro de Sao Joao 206 / 242, Morro da Saudade 192 / 244, Agulha do Inhanga 152 / 164.
  They could join `landmark_peaks` (needs radii that keep the domes off houses; not done).
* **Babilonia and Chapeu Mangueira** are favelas but only `landuse=residential` in OSM: new
  `favela_infill.extra_favela_names` makes them favelas (informal houses, infill, doors, wires). Infill: Babilonia
  +1578 houses, Chapeu Mangueira +623 (0% -> 55% coverage).
* Area totals: 11,002 buildings (2,882 informal), 9.20 M triangles in the area tiles (was 4.5 M): the heaviest test
  level now; watch editor load / frame rate.
* Still outside: the north slopes of the Pao de Acucar / Morro da Urca (edge 115 m behind the summit), the Urca
  neighbourhood and Praia da Urca, Botafogo, the Corcovado (N7460682 but W of this area; inside none).

# Session 15 (2026-11-26, MacBook): short hills raised to their surveyed heights

`landmark_peaks` gained Morro de Sao Joao (r 200 m), Agulha do Inhanga (r 120 m) and Morro do Leme (r 180 m).
Radii stay under the nearest house (Sao Joao 215 m, Inhanga 131 m). Results (model vs OSM `ele`): Morro do Leme
151.3 / 150, Morro de Sao Joao 242.9 / 242, Agulha do Inhanga 164.5 / 164. Pao de Acucar unchanged 392.5 / 392.
Render: `screenshots/morro_do_leme.png`.

* **Morro do Leme**: the Forte Duque de Caxias (OSM way 462182014) is on its summit ridge, 113 m west of the OSM
  peak node. A dome on the node left the fort straddling a 35 m flank (a 40 m wall downhill), so this dome is
  centred on the fort (`landmark_peaks.centre_utm`; assumption: the fort marks the summit), and the fort is a 5 m
  `M_FortConcrete` landmark (was a 33 m default tower). Fort ground now 148.4-150.4 m. The access road cuts a
  notch into the raised top: check in the editor.
* Morro da Saudade is not short: 252 m in the Lagoa build (OSM 244). The earlier 192 m was sampled from outside
  the Copacabana area. Not added.
* Morro do Cantagalo (9 m short) skipped: favela houses 16 m from its summit.
* `test_copacabana`, `test_lagoa`, `test_leblon_arpoador` rebuilt (Agulha do Inhanga is also inside the Leblon
  area's DEM window: `+36.7 m` there too).

# Session 16 (2026-11-26, MacBook): Centro 1:1 measurement area

Branch **`macbook/centro`** (on top of `macbook/beach-life`). New AOI `test_centro`: E684500-689000 x N7463500-7467500
(Sambodromo to the airport / Ilha Fiscal, Catete / Gloria to Praca Maua / AquaRio). Purpose: real distances and heights
to compress from (GTA-style plan in `DesignPriorities.md`). Renders: `screenshots/centro_from_bay.png`,
`centro_praca_maua.png`.

* 24,736 buildings (4,201 informal), 7,720 road edges (161 tunnel edges skipped), 8.13 M triangles.
* Favelas: `residential=irregular_settlement` now counts as favela everywhere (only Tavares Bastos, in this area);
  `extra_favela_names` + Morro da Providencia, Ocidental Fallet, Morro Santos Rodrigues, Morro da Coroa, Mineira.
  Infill: Providencia +1685, Santos Rodrigues +478, Mineira +337, Coroa +215, Fallet +63.
* New feature set `centro` (whole AOI): 28 icon markers (museum / church / library / market / landmark / airport ...):
  Museu do Amanha, MAR, AquaRio, Pedra do Sal, Mural das Etnias, Sao Bento, Real Gabinete, Saara, Confeitaria Colombo,
  Paco Imperial, Arco do Teles, CCBB, Candelaria, Theatro Municipal, Belas Artes, Arcos da Lapa, Escadaria Selaron,
  Catedral, MAM, Bossa Nova Mall, Aeroporto Santos Dumont, Monumento aos Pracinhas, Marina da Gloria, Outeiro da
  Gloria, Museu da Republica, Sambodromo.
* Airport: runways 20L/02R and 20R/02L ~1.26 km (OSM), terrain under them 1.6-2.9 m (a 1.3 m hump: needs flattening
  for planes).

## Problem: icon heights

Icons **with** an OSM height are right: Theatro Municipal 20.6 m, Catedral 75 m, Real Gabinete 17.1 m, Biblioteca
Nacional 23.4 m. Most icons have **no** OSM height and got the office-block default (24-40 m boxes, random facade
colours, roof machine rooms): Museu do Amanha 24.5, MAR 27.5, Paco Imperial 24.5, Candelaria 30.5, CCBB 30.5, Belas
Artes 33.5, Sao Bento 39.5, Monumento aos Pracinhas 33.5, Outeiro da Gloria 33.5, Ilha Fiscal castle 36.5. The Arcos
da Lapa are a solid 27.5 m block (should be arches). Arco do Teles has an OSM height of 42.4 m (looks wrong).
Same issue as the forts' guessed heights (Session 15 follow-up). Next: a landmark registry with sourced dimensions.
