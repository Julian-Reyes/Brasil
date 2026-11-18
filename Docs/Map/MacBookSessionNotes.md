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
