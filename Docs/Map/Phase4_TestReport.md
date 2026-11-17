# Phase 4: first Unreal test (Leblon / Av. Niemeyer / Vidigal)

Level: `/Game/Rio/Maps/L_Rio_Test_LeblonVidigal` (World Partition, 1:1 source frame, georeferenced).
AOI `test_leblon_vidigal`: UTM E 680000-682000, N 7455500-7457500 (2 x 2 km, 16 tiles of 500 m).

## Contents

| Layer | Detail |
|---|---|
| Terrain | 2 m grid, road-carved ground model, 16 tiles x 125k triangles, per-triangle collision |
| Roads | 703 surface edges (arterials, local streets, footways, 300+ staircase segments) as ribbons at real widths, e.g. Av. Delfim Moreira 3 lanes per carriageway, Av. Niemeyer 1-3 lanes, Av. Pres. Joao Goulart about 7 m |
| Buildings | 3,947 extruded footprints: 2,875 with OSM heights, 4 from levels, 1,068 deterministic defaults; favela houses (orange) vs formal city (grey) |
| Sea | Plane at z = 0, no collision |
| Excluded | Tunel Zuzu Angel / Tunel Acustico (tunnel=yes); not modelled as surface roads |
| Gameplay | `PlayerStart_VidigalEntrance` at the Av. Niemeyer / Av. Pres. Joao Goulart junction, facing uphill |

## Verification

| Check | Result |
|---|---|
| OBJ axis/unit calibration | file (x, y, z) -> UE (x, -y, z), 1:1 cm. Verified by importing a calibration mesh |
| Tile bounds in UE | 50,000 x 50,000 cm, north = -Y as designed |
| Road surface vs terrain (pipeline, all centreline samples) | 99.2% within +/-0.5 m; median +0.16 m (intended road lift); extremes -1.9 / +2.2 m at corridor overlaps |
| Road height in engine (10 line traces on 5 named roads) | within 1.6-4.1 cm of prediction (re-verified after re-import via editor Python) |
| World Partition loading | Generated actors are non-spatially-loaded (`always_loaded`), so the level never opens empty |
| Collision | Complex-as-simple on terrain, roads and buildings; auto-generated convex hulls removed |
| Third Person character (PIE) | Spawns at the Vidigal entrance and stands on Av. Niemeyer at z = 32.81 m (road 31.89 m + capsule), stable over 3 s. See `screenshots/test_04_pie_third_person.png` |
| Top-down comparison | `screenshots/test_02_top_down.png` matches `reference/test_leblon_vidigal.png` (Leblon grid, Niemeyer curve, Vidigal, coastline) |
| Building/carriageway overlap | 16 of 3,947 footprints overlap a carriageway by more than 25% (OSM width/geometry noise; acceptable) |
| Third Person template | `Content/ThirdPerson/` unmodified |

## Known issues to address next

1. **Leblon ground is still slightly high.** Fixed from about 10-11 m to 6.3-6.6 m on Av. Ataulfo de Paiva
   (real about 3-4 m); the beachfront is 2.5-4.7 m. Remaining error is the DSM's resolution; see DataSources.md
   (upgrade path: Rio municipal DTM).
2. **Some drivable grades slightly exceed the 30% limit:** Av. Pres. Joao Goulart and one service lane reach
   about 38% over 10 m, where junction heights are fixed by a higher tier. Real Vidigal streets are
   very steep; check against ground truth before tightening.
3. Steps are modelled as ramps (walkable). Real stair geometry is a later art pass.
4. The test area is cut at its AOI edge (visible cliff walls). The sea plane extends 3 km beyond it.
5. Dois Irmaos summits are low (DSM limitation, see `DataSources.md`).

## Screenshots

`screenshots/test_01_sea_view.png`, `test_02_top_down.png`, `test_03_vidigal_entrance.png`,
`test_04_pie_third_person.png`.
