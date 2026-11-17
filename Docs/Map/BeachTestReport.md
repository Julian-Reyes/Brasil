# Hero beach test: Leblon to Arpoador (1:1 source)

Level: `/Game/Rio/Maps/L_Rio_Test_LeblonArpoador` (World Partition, georeferenced, spawn `PlayerStart_Posto9`
on Av. Vieira Souto facing west toward Dois Irmaos). AOI `test_leblon_arpoador`: UTM E 681500-686000,
N 7456000-7458500 (4.5 x 2.5 km, 45 tiles of 500 m, terrain grid 2.5 m).

Inputs were clipped from the full-area download (`scripts/clip_osm.py`), so no Overpass request was needed.

| Item | Result |
|---|---|
| Content | 118 meshes, 4.38 M triangles; 11,682 buildings; 3,253 surface road edges (42 tunnel edges skipped) |
| Beachfront heights | Av. Delfim Moreira median 1.4 m, Av. Vieira Souto 1.0 m, Ataulfo de Paiva 4.8 m, Visconde de Piraja 3.5 m |
| Road vs terrain (pipeline) | 99.2% of samples within +/-0.5 m |
| Engine traces (12 on 6 named roads) | 10 within 14 cm; Francisco Otaviano +21 cm and -50 cm (investigate) |
| Actors | 118, unique labels, all non-spatially-loaded; blockout materials bound; Nanite off; complex collision |
| Import time | about 10 min via `unreal/import_area.py` |

## Known issues

1. **No favela outlines yet** (the OSM reference layer is still pending), so Cantagalo / Pavao-Pavaozinho at the
   east edge is built as formal city. The ground model there is wrong: drivable grades of 70-166% on
   Rua Sa Ferreira, Rua Saint Roman, Travessa Hortalicia and Av. Pavaozinho; paths float up to 8.7 m.
   Fix: once `GeoData/raw/osm/full/reference.gpkg` exists, re-run `clip_osm.py`, `build_area.py` and
   `import_area.py` for this AOI.
2. Av. Atlantica (the Copacabana edge) shows a 67% grade over 21 m at the AOI boundary.
3. Delfim Moreira peaks at 9.6 m near the Niemeyer end; check against ground truth.
