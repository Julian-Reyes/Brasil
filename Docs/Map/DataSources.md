# Data sources, licences and known limitations

## In use

| Data | Source | Licence | Attribution required |
|---|---|---|---|
| Roads, buildings, favela areas, peaks, coastline | OpenStreetMap via Overpass (OSMnx 2.1) | **ODbL 1.0** | "(c) OpenStreetMap contributors" |
| Elevation | Copernicus DEM GLO-30 (AWS open data, tiles S23/S24 W044) | Copernicus DEM licence: free worldwide use incl. commercial | "Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved." |

**Licensing notes for a commercial game (have counsel confirm before shipping):**

* **OSM / ODbL.** The rendered game world is a *Produced Work*, which needs attribution (credits screen). If you
  publicly distribute the *derived database* itself (e.g. the road graph JSON), it must be released under ODbL.
  Keep extracted OSM-derived data out of shipped builds in database form, or plan to publish it.
* **Copernicus GLO-30.** Commercial use is permitted with the attribution text above.

## Coverage found (validation reports in `validation/`)

* **Test area:** 4,179 buildings, 92% with an OSM `height` tag. These are per-part heights (towers and their
  3-5 m podiums are separate polygons), apparently from a municipal 3D import, so massing is realistic; 1,214 road edges including 334 staircase
  and 204 footway segments (Vidigal's becos are well mapped). Favela areas are mapped as
  `landuse=residential + residential=favela` (Vidigal, Chacara do Ceu, Rocinha).
* Tunnels (Zuzu Angel / Acustico) are tagged and excluded from surface geometry.

## Known limitations

1. **Copernicus is a surface model (DSM), not bare ground.** It includes rooftops and tree canopy.
   Mitigation: subtract the mapped building volume (full-region buildings across the whole DEM window), then
   on the coastal lowland (roof-free surface < 15 m) take the minimum with a 150 m morphological opening.
   Residual: Av. Ataulfo de Paiva reads about 6.3-6.6 m (real about 3-4 m); the beachfront is about 2.5-4.7 m.
   Forest canopy (about 10-20 m) remains on the hills. Rio municipal DTM data would remove both residuals.
2. **Steep granite peaks are under-represented** at 30 m: Pao de Acucar reads 329 m against a surveyed 396 m,
   Corcovado 632 m against 710 m, Irmao Maior about 386-469 m against 539 m. Plan: a landmark-summit
   correction step that raises iconic peaks to surveyed heights with a smooth falloff.
3. **Narrow features are below DEM resolution.** Av. Niemeyer's cliff shelf and favela terraces come from the
   road-carving solve, not from the DEM.

## Recommended upgrades (all need licence review first)

* **Rio municipal topography (IPP / Data.Rio):** 1:2,000 contour lines / DTM. Metre-level bare-ground accuracy
  in exactly our CRS. Best upgrade for favela terrain and the Leblon/Ipanema plain.
* **ANADEM** (UFRGS / ANA): a Copernicus-derived *bare-earth* DTM for South America with vegetation bias
  removed.
* **Data.Rio favela boundaries (Limite de Favelas, IPP/SABREN):** authoritative boundaries for every
  community in the map bounds, to replace partial OSM coverage outside Vidigal and Rocinha.
