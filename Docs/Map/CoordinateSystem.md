# Coordinate system

Single source of truth: `Tools/GeoPipeline/config/project.json`.

## Frames

| Frame | Definition | Used for |
|---|---|---|
| Geographic | EPSG:4674 SIRGAS 2000 (OSM's EPSG:4326 differs by < 1 m) | Input data, GeoReferencing geographic CRS |
| Projected | **EPSG:31983 SIRGAS 2000 / UTM zone 23S**, metres | All analysis and geometry generation. Official Brazilian CRS, same as Rio municipal (IPP / Data.Rio) data |
| Vertical | Metres above mean sea level (EGM2008, from Copernicus DEM) | Heights |
| **UE source frame** | origin E 684500, N 7461000, h 0. +X = East, +Y = South, +Z = Up, centimetres | 1:1 geographic levels (`L_Rio_Test_*`) |
| UE compressed frame | Defined by the compressed road graph (Phase 3) | `L_Rio_Blockout`, the playable map |

Conversion (source frame): `X = (E - 684500) * 100`, `Y = -(N - 7461000) * 100`, `Z = h * 100`.

The origin lies near Lagoa / Humaita, roughly central to the full AOI, so the whole real region stays within
about +/-9 km of the origin, which is fine for single-precision rendering. The UE axis convention matches the
GeoReferencing plugin's flat-planet ENU mapping.

## Verified behaviour

* **OBJ import axis.** A calibration mesh with vertices at file (1000, 0, 0), (0, 2000, 0) and (0, 0, 3000)
  imported with bounds X 0..1000, Y -2000..0, Z 0..3000. Unreal maps file (x, y, z) to UE (x, -y, z) with no
  unit scaling, so meshes are written as East/North/Up centimetre offsets from each tile's south-west corner.
* **In-engine placement.** Line traces at 10 road centreline points (Av. Niemeyer, Av. Pres. Joao Goulart,
  Av. Delfim Moreira, Av. Ataulfo de Paiva, Estrada do Vidigal) hit within 1.5-4.4 cm of the pipeline's
  predicted surface height. The residual is the deliberate per-class z-lift used to avoid z-fighting.

## GeoReferencing actor

`L_Rio_Test_LeblonVidigal` contains a `GeoReferencingSystem`: FlatPlanet, ProjectedCRS EPSG:31983,
GeographicCRS EPSG:4674, origin in projected CRS (684500, 7461000, 0).

`L_Rio_Blockout` is deliberately **not** georeferenced: it will hold the compressed map, where a real-world CRS
would report wrong coordinates everywhere except the anchor point.
