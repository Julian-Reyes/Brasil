# Unreal debug guide: road heights (written on the MacBook, 2026-11-18)

Two road-height problems that need eyes in the editor before choosing a fix. Written so you and Claude can work
through them on the Mac mini. Everything here was diagnosed from pipeline numbers only; **nobody has looked at
these spots in Unreal yet.**

## Tools (new, untested: written without Unreal)

From `Tools/GeoPipeline`, with the editor open on the right level:

```sh
# fly the viewport camera to a spot (UE centimetres, or UTM metres)
.venv/bin/python unreal/ue_remote.py unreal/goto.py '{"ue": [87600, 355300, 5090]}'
.venv/bin/python unreal/ue_remote.py unreal/goto.py '{"utm": [685376, 7457447, 50.9], "dist_m": 60}'

# measure what is actually in the level: vertical line traces at UTM points
.venv/bin/python unreal/ue_remote.py unreal/trace_heights.py '{"points": [[685376, 7457447], [685400, 7457453]]}'
```

If `trace_heights.py` fails on the `HitResult` unpacking (`to_tuple()` indices), print `hit.to_tuple()` once and
fix the two indices; that is the only part not checked against the UE 5.8 Python API. `preview_area.py` (no
Unreal needed) gives the same profiles top-down: `--aoi <aoi> --roads "<road name>"`.

Rebuild-and-look loop for any experiment below:

```sh
.venv/bin/python scripts/build_area.py --aoi <aoi>          # after changing config/project.json
.venv/bin/python scripts/build_beach.py --aoi test_leblon_arpoador   # beach area only
.venv/bin/python scripts/build_shore.py --aoi <aoi>         # areas containing the lagoon
.venv/bin/python unreal/ue_remote.py unreal/import_area.py '{"aoi": "<aoi>"}'
```

---

## Problem 1: SE corner of the Lagoa (Corte do Cantagalo / Humaita)

Level: `L_Rio_Test_Lagoa` (AOI `test_lagoa`). Roads 80-215 m from the water, so outside the flat-shore cap.

| What | UTM | UE (cm) | Pipeline z | DSM |
|---|---|---|---|---|
| trunk_link ramp top | E684695 N7457951 | (19500, 304900, 1570) | 15.7 m | 7.0 m |
| same ramp, other end | 66 m away | | **-0.1 m** | |
| trunk_link | E684652 N7457763 | (15200, 323700, 1210) | 12.1 m | 16.2 m |
| Av. Epitacio Pessoa | E684356 N7458273 | (-14400, 272700, 910) | 9.1 m | 19.8 m |
| Av. Epitacio Pessoa | E684503 N7457706 | (300, 329400, 610) | 6.1 m | 9.5 m |
| reference: east shore, already fixed | E683890 N7458659 | (-61000, 234100, 300) | ~3 m (was 11.6) | 13.2 m |

**What to look at:**
* Does the ramp (15.7 -> -0.1 m over 66 m = 24%) look like a ramp to nowhere / dive under the terrain? The
  -0.1 m end is certainly wrong: it is land below sea level.
* Real-world check (Google Earth / Street View at the Corte do Cantagalo): which of these roads are on
  viaducts, which climb into the cut, which are at lagoon level? OSM has no `bridge` tag on these edges, so the
  pipeline treats them as on the ground.
* Is the terrain next to them a believable slope, or a wall / pit around the road corridor?

**Fix options:**
* `roads.clamp_min_height: true` (new switch, default false): clamps every solved road to >= 1.0 m. Fixes the
  -0.1 m end; does not fix the 15.7 m top.
* If those edges really are viaducts: add them to a per-OSM-id bridge override (not implemented; small change in
  `rio_geo/roads.py prepare_edges`).
* If they are on the ground at lagoon level: extend the flat-shore cap there, or cap these named roads to a max
  height (not implemented).

## Problem 2: Cantagalo / Pavao-Pavaozinho grades (Copacabana side)

Level: `L_Rio_Test_LeblonArpoador` (AOI `test_leblon_arpoador`), east edge. Note: since the Vidigal session, the
favela infill adds ~526 houses in Pavao-Pavaozinho here, which slightly changes the ground model; numbers below
are from before that.

| What | UTM | UE (cm) | Pipeline z |
|---|---|---|---|
| Junction Rua Sa Ferreira x Rua Saint Roman | E685376 N7457447 | (87600, 355300, 5090) | **50.9 m** (DSM 7.2) |
| Rua Sa Ferreira, the "172%" drop | E685400 N7457453 | (90000, 354700, 2700) | 50.9 -> 4.0 m in 53 m |
| Rua Saint Roman, the real climb | E685100 N7457300 | (60000, 370000, 4000) | |

**Cause (from MacBookSessionNotes.md section 1):** `roads.max_grade.minor = 0.30`. Saint Roman really climbs
steeper than 30%. The solver keeps raising the slope weight (up to 1e4, vs 1 for the ground data) and lifts the
bottom junction about 44 m instead of letting the street be steep.

**What to look at:**
* The junction should show as a road on a 40-50 m embankment above flat Copacabana streets, with a cliff-like
  drop on Sa Ferreira. Confirm it's that and not something else.
* Then compare the experiment below.

**Fix options (switches are in `config/project.json` -> `roads`):**

| option | config | effect (pipeline numbers) |
|---|---|---|
| current | `max_slope_weight: 10000` | junction 50.9 m; Sa Ferreira 172%, Saint Roman 139% |
| A. cap the weight (+ `clamp_min_height: true`) | `max_slope_weight: 100` | junction 14.3 m (DSM 7.2, still ~7 m high); Sa Ferreira 45%, Saint Roman 42%, Hortalicia 46%; minor streets > 50%: 10 -> 4, > 30%: 20 -> 24; road vs terrain within 0.5 m 99.19% -> 99.50%; lowest road -3.2 m -> 1.0 m |
| B. no limit for minor streets | `max_grade.minor: null` | junction -0.7 m; Sa Ferreira 29%, Saint Roman 36%; but minor streets > 30%: 19 -> 76 (DSM noise) |

Option A was run on the laptop (scratch build, not committed as the default) for both areas. On `test_lagoa` it
also brings the SE ramp top from 15.8 m to 9.9 m and Av. Epitacio Pessoa's max grade from 31% to 16%; problem 1
should be re-checked with A on. Note: the *current* builds have a road solved to **-3.2 m** somewhere (lowest
road z); `clamp_min_height` removes that. Find it with `preview_area.py` or by searching `road_graph_source.json`
for the minimum z.

My recommendation is A plus `clamp_min_height: true`, if it looks right in the editor. Then check Vidigal
(`L_Rio_Test_LeblonVidigal`) as well: A changes every steep street.

## Vidigal hub, layout 2 (2026-11-24): praca, stairs, terrace edges

Walk these in `L_Rio_Test_Vidigal` after re-importing (third-person template character, default step height
45 cm, so 17 cm risers must work; also try walking *up* each stair). Pipeline renders of the same spots:
`Docs/Map/screenshots/vidigal_hub_3d.png`, `vidigal_hub_praca.png`, `vidigal_hub_long_stair.png`, `vidigal_osm_steps.png`.

| What | UTM | UE (cm) | Pipeline z |
|---|---|---|---|
| Praca centre | E680848 N7456049 | (-365150, 495100, 4050) | 40.5 m |
| Campinho centre | E680802 N7456038 | (-369800, 496200, 3870) | 38.7 m |
| Stair praca -> campinho (5 m wide, 11 steps) | E680827 N7456047 | (-367300, 495300, 3960) | 40.5 -> 38.7 m |
| Stair junction -> praca, top (21 steps) | E680835 N7456066 | (-366500, 493440, 4400) | 44.0 -> 40.5 m |
| Stair avenue -> campinho, top (43 steps, along the north wall) | E680785 N7456049 | (-371460, 495080, 4600) | 46.0 -> 38.7 m |
| same, bottom (enters the field) | E680806 N7456050 | (-369420, 495010, 3870) | |
| Stair Caminho da Boa Vista -> campinho (4 steps) | E680828 N7456034 | (-367200, 496650, 3840) | 38.1 -> 38.7 m |
| Shop row (Mercado) | E680842 N7456077 | (-365830, 492270, 4300) | |
| OSM steps (beco stair, 39 m climb) | E680527 N7455816 | (-397300, 518400, 8800) | |
| OSM pitch with a 10-13 m retaining wall over a service road | E681210 N7456549 | (-329050, 445100, 9810) | 98.1 m |

What to look for:

* Stairs: can the character walk up and down every one? Do the stair ends meet the street / praca / field flush
  (no lip > 45 cm)? Cheek walls in the way at the top landing?
* Terrain poking through stair treads (the terrain ramp is on a 2 m grid; the treads are solid 0.6 m+ blocks).
* Campinho fence gaps: at the three stair landings and the west gate (toward the player house).
* Terrace edges on the uphill side: wall top ledges (4 m wide) should hide the lowered ring behind the wall; on
  the praca's avenue side they step down like terraced seating. Look for gaps between ledge and street.
* OSM beco stairs: 7,244 steps in this AOI; 14 edges have a tread under 25 cm (very steep), see `report.json ->
  stepped_stairs`. Collision is complex-as-simple like the roads.

## Vidigal housing infill v2 (2026-11-24)

Same viewpoints as the before / after renders in `Docs/Map/screenshots/vidigal_infill_{before,after}_*.png`:

| What | UTM | UE (cm) |
|---|---|---|
| Aerial target (upper Vidigal) | E680480 N7455930 | (-402000, 507000, 10000) |
| Street view: Av. Pres. Joao Goulart, eye height | E680432 N7455978 | (-406800, 502150, 9090) |
| Beco stair, eye height | E680549 N7455975 | (-395110, 502520, 7850) |

What to look for: does it read as a favela from the street and from Leblon? Houses blocking becos or stairs
(they must keep 0.6 m clear), floating or buried house bases on steep slopes, parapets / roof rooms / tanks
intersecting, colour mix (bare brick vs render vs painted), triangle count / frame rate (Buildings tiles grew
~5%).

