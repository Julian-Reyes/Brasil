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


## Vidigal street details (2026-11-25): doors, wires, lanes, lookouts

Renders: `Docs/Map/screenshots/vidigal_details_{street,beco,aerial,lookout_view}.png`. Same tools as above
(`fly_to`, line traces). Generated by `scripts/build_details.py` (kinds `Details`, `Wires`), lookouts sited by
`build_area.py` (`GeoData/exports/test_vidigal/lookouts.json`, markers kind `lookout`).

| What | UTM | UE (cm) |
|---|---|---|
| Street view: Rua Dr Olinto de Magalhaes (now a 4.2 m lane), eye height | E680495 N7456072 z108 | (-400541, 492750, 10750) |
| Beco stair (cable tangle), eye height | E680680 N7455840 z45 | (-382050, 515950, 4540) |
| Mirante 1: Av. Pres. Joao Goulart, facing 182 deg | E680252 N7455708 z124 | (-424826, 529191, 12421) |
| Mirante 2: Rua Doutor Sobral Pinto, facing 153 deg | E680442 N7455824 z124 | (-405783, 517561, 12350) |
| Mirante 3: Av. Pres. Joao Goulart (high), facing 164 deg | E679849 N7455783 z226 | (-465080, 521698, 22576) |
| Mirante 4: stair near the bottom, facing 149 deg | E680715 N7455788 z15 | (-378461, 521243, 1493) |

What to look for:

* **Doors:** at the right height (bottom at the ground just outside, up to 5 cm below), not floating on steep
  slopes, not opening into a neighbour's wall; the small step in front not blocking a narrow beco. Doors are
  boxes 7 cm proud of the wall with collision: the character should not snag on them.
* **Lanes:** can a car drive Rua Dr Olinto / Rua Quatro / Rua Dioneia (4.2 m) and the services (3.0 m)? Houses
  touching the lane (infill keeps 1 m from the kerb); `build_area.py` reports 20 carriageway conflicts in this
  AOI (footprints over a drivable street, from OSM-mapped buildings).
* **Poles:** on the shoulder, never on the carriageway, a footway or a stair; a pole inside a house wall would be
  a bug. Since Session 10 every pole has a power span (see "Turnarounds and pole lines" below).
* **Wires:** no collision (kind `Wires`, `collision: none`). Check they clear head height over becos (>= 2.4 m),
  do not pass through roofs, and how 1.2 cm cables look at distance (aliasing / shimmer: may need a min width or
  a cable material with opacity fade later). Frame cost: ~176 k triangles in Wires tiles.
* **Lookouts:** the view (sea, Leblon / Ipanema) from the bench; railing height 1.05 m; the deck block reaching
  the ground on the downhill side; the house(s) removed for it (7 in total) leave no hole in a terrace wall.

## Vidigal signature escadarias (2026-11-25)

Renders: `Docs/Map/screenshots/vidigal_escadaria_{padre_italo,niemeyer_bar,top}.png`,
`vidigal_escadarias_aerial.png`. Routes and landings: `GeoData/exports/test_vidigal/escadarias.json` (written by
`build_area.py`); treads and furniture are in the `Details` tiles (`build_details.py`); markers kind `escadaria`
and `bar`.

| What | UTM | UE (cm) |
|---|---|---|
| Escadaria do Niemeyer: base (Av. Niemeyer) | E680884 N7455907 z1 | (-361622, 509346, 81) |
| Escadaria do Niemeyer: bar | E680787 N7455912 z18 | (-371319, 508772, 1787) |
| Escadaria do Niemeyer: topo (street below the hub) | E680762 N7455995 z29 | (-373783, 500451, 2876) |
| Escadaria Padre Ítalo: base (fork off the Niemeyer stair) | E680775 N7455932 z22 | (-372491, 506814, 2218) |
| Padre Ítalo: rainbow run on the old pedestrian lane, eye height | E680656 N7455951 z50 | (-384407, 504918, 5039) |
| Padre Ítalo: bar 1 | E680628 N7455944 z58 | (-387199, 505602, 5791) |
| Padre Ítalo: sharp turn onto the upper stairs, eye height | E680560 N7455970 z77 | (-393977, 502996, 7655) |
| Padre Ítalo: bar 2 | E680539 N7455933 z93 | (-396130, 506684, 9340) |
| Padre Ítalo: topo | E680394 N7455870 z138 | (-410554, 512981, 13842) |

What to look for:

* **Walkability:** 17 cm risers everywhere; next to each landing the steps are up to ~27% steeper for 3 m (the
  landing is carved out of the slope; shortest tread 25 cm on Padre Ítalo, 30 cm on Niemeyer). The upper half of
  Rua Padre Ítalo Coelho (33%) and the stairs above it (35-46%) are the steepest parts.
* **Terrain:** the ground is lowered 30 cm under the treads and 30 cm beyond their edges (`stairs.apply_ramp`);
  nothing should poke through a tread. Treads are solid 60 cm below their lowest point.
* **Corridor:** 3 m between the house fronts (7 buildings removed). Most houses lining it are painted; half of
  the walls facing it carry a mural panel (flat colour for now). A mural may cover part of a door.
* **Handrail:** on the left going up, 95 cm, posts every 2 m (collision). Lamps (3.8 m posts with an arm) on the
  right every 18 m, none at a bar. Benches on every 2nd landing, back to the right side.
* **Bars:** counter, red awning (2.45 m high), sign and a yellow plastic table with 2 stools on the landing:
  check the table leaves a free path (~1.3 m) and the awning clears heads.
* Lookout 4 (z15) is next to the lower stair; the first ~25 m above Av. Niemeyer are nearly flat (long green
  tread / ramp).

## Turnarounds and pole lines (2026-11-25, Session 10)

Renders: `Docs/Map/screenshots/vidigal_turnaround_{flat,slope,steep}.png`, `vidigal_poles_linked.png`. Pads:
`GeoData/exports/test_vidigal/turnarounds.json` (16 in the Vidigal AOI), meshed in the `Roads` tiles
(`M_RoadMinor`, same collision as the roads).

| What | UTM | UE (cm) |
|---|---|---|
| Turnaround, level (grade 0%) | E680169 N7455680 z132 | (-433060, 531970, 13150) |
| Turnaround, 19% | E679743 N7455783 z221 | (-475740, 521740, 22050) |
| Turnaround, 33% (steepest) | E679687 N7456330 z151 | (-481270, 466990, 15050) |
| Turnaround, -24% (downhill end) | E680244 N7455869 z134 | (-425590, 513150, 13440) |

What to look for:

* **Turning:** drive a car to the end of a 3-4.2 m lane and turn in the 11 m bulb (radius 5.5 m, reaching 1.5 m
  past the end of the lane). The bulb is tilted like the lane's last 6 m (level across): the steep ones (19-33%)
  may be too steep to turn on.
* **Seams:** the bulb sits 3 mm under the lane ribbon where they overlap; look for z-fighting or a lip where the
  lane ribbon ends. The ground is graded to the bulb and blends out over 2.5 m.
* **Clearing:** informal houses on the bulb (+0.3 m) were dropped (75 in the AOI); the open space around a bulb
  should read as a small largo, not a hole.
* **Poles:** 395 poles, all with a power span. 24 poles are taller (up to 12.5 m) so a span clears a roof, 61
  poles were added at street bends; check the tall ones don't look odd and no added pole stands in a doorway.

## Beach life, facades, Copacabana (2026-11-25, Session 12)

Renders: `Docs/Map/screenshots/beach_life_posto9_aerial.png`, `beach_life_posto9_sand.png`,
`ipanema_facades_aerial.png`, `copacabana_aerial.png`, `copacabana_posto3.png`. Ground z below is the terrain;
the sand / calcadao meshes sit up to ~1 m above it, so add ~2 m for a PlayerStart.

| What | UTM | UE (cm) | terrain z |
|---|---|---|---|
| Posto 9, sand by the umbrellas | E684076 N7456780 | (-42400, 422000, -2) | 0.0 m |
| Vieira Souto facades (street) | E683300 N7456905 | (-120000, 409500, 90) | 0.9 m |
| Delfim Moreira west end (Dois Irmaos foot kept) | E681560 N7456640 | (-294000, 436000, 514) | 5.1 m |
| Copacabana Posto 3, sand | E686389 N7458600 | (188900, 240000, 36) | 0.4 m |
| Calcadao at Posto 3 (suggested `PlayerStart_Copacabana`) | E686420 N7458680 | (192000, 232000, 93) | 0.9 m |
| Posto 6: Atlantica x Rua Francisco Sa hump (not fixed) | E685547 N7457247 | (104700, 375300, 1091) | 10.9 m |
| Block behind Posto 5 (ground cut ~30 m) | E685630 N7457920 | (113000, 308000, 605) | 6.0 m |
| Tunel Novo portal slope (ground cut 33 m) | E687360 N7459367 | (286000, 163300, 757) | 7.6 m |
| Leme end / Morro do Leme (kept) | E688232 N7459367 | (373200, 163300, 5768) | 57.7 m |

What to look at:
* **Beach life** (`BeachLife` tiles, no collision): umbrella / chair / barraca scale against the template
  character; do you want collision on barracas and nets; frame rate with ~2,000 umbrellas in view at Posto 9.
* **Courts:** nets at 2.43 m (volley) / 2.2 m (footvolley), tape lines flush on the sand (z-fighting?).
* **Sand vs terrain:** no grey ground strip in the surf any more; check the calcadao skirt (0.6 m) where the
  calcadao stands up to 3.9 m above the terrain in Copacabana (gaps under the edge?).
* **Facades:** colour mix, the dark ground-floor band, machine rooms; Copacabana kiosk canopies now 3 m.
* **Copacabana ground:** the block behind Posto 5 should be flat street level; the Tunel Novo slope may have
  been cut too much (real hill there?); Morro do Leme and Chapeu Mangueira must be intact.
* **Posto 6 hump:** Av. Atlantica rises to ~11 m at Rua Francisco Sa (Problem 2's mechanism). Option A fixes it in
  the pipeline (Atlantica 1.0-3.7 m); decide together with Problem 2.

## Arpoador and the Forte (2026-11-26, Session 13)

Level `L_Rio_Test_Copacabana` (complete fort and rock; `L_Rio_Test_LeblonArpoador` has the rock and only the west
part of the fort). Renders: `screenshots/arpoador_rock.png`, `forte_copacabana.png`, `forte_fortress_roof.png`.

| What | UTM | UE (cm) | z |
|---|---|---|---|
| Pedra do Arpoador summit (rebuilt) | E685440 N7456365 | (94000, 463500, 1400) | 14.0 m |
| Fort gate / sally port (Praca Cel. Eugenio Franco) | E685769 N7456860 | (126900, 414000, 350) | 3.5 m |
| Museum buildings (colonial blocks) | E685830 N7456890 | (133000, 411000, 22) | 0.2 m |
| Confeitaria Colombo | E685864 N7456905 | (136400, 409500, 13) | 0.1 m |
| Fortress roof (walkable, cupolas) | E686050 N7456900 | (155000, 410000, 720) | 7.2 m |
| Posto 6 fishermen's pier | E685762 N7456924 | (126200, 407600, 90) | 0.9 m |

What to look at: the rock's silhouette from Posto 9 and from the Arpoador beach; walk the rock paths (complex
terrain collision); fortress roof walkable with the parapet; cupola / gun scale; the wall line (gaps at the gate,
not through buildings); peninsula spine 12-17 m; nothing floating where the old flat ground was.
