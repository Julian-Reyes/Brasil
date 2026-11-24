"""Step 1b - download point features (furniture) around named anchors for an AOI.

Feature sets are defined in config `feature_sets`:
  beach  lifeguard posts, kiosks, showers, pitches around the hero beaches     -> beach.gpkg
  lagoa  kiosks, benches, trees, pitches, piers around Lagoa Rodrigo de Freitas -> lagoa.gpkg

Only a strip around the set's anchor polygons (from reference.gpkg) is queried: whole-AOI queries time out on
the public Overpass servers. `optional_tags` (e.g. trees, which can be thousands of nodes) are fetched in a
separate request whose failure is logged but not fatal.

Output: GeoData/raw/osm/<aoi>/<set>.gpkg: layer 'features' (project CRS, one point per feature; areas are reduced
to their representative point and keep their footprint area in `area_m2`) and layer 'areas' (the real outlines
of the polygon features, e.g. pitches).

Usage:  .venv/bin/python scripts/fetch_beach.py --aoi test_leblon_arpoador            (set beach)
        .venv/bin/python scripts/fetch_beach.py --aoi test_lagoa --set lagoa
"""
import argparse
import math
import sys
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import (CONFIG, PROJECTED_CRS, RAW_DIR, aoi_bbox_lonlat, lonlat_to_utm,  # noqa: E402
                            utm_to_lonlat, write_metadata)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm import flatten_for_gpkg, with_overpass_retry  # noqa: E402

KEEP = ["name", "emergency", "shop", "amenity", "natural", "leisure", "man_made", "sport", "species",
        "genus", "leaf_type", "ref", "operator", "tower:type", "backrest", "denotation"]


def anchor_bbox_lonlat(osm_dir, names, pad_m):
    """Bounding box (lon/lat) of the named reference polygons plus pad_m, or None."""
    ref = osm_dir / "reference.gpkg"
    if not ref.exists():
        return None
    polys = gpd.read_file(ref, layer="polygons")
    sel = polys[polys["name"].isin(names)]
    if sel.empty:
        return None
    w, s, e, n = sel.total_bounds
    lons, lats = zip(*[utm_to_lonlat(x, y) for x, y in ((w - pad_m, s - pad_m), (e + pad_m, n + pad_m))])
    return (min(lons), min(lats), max(lons), max(lats))


def chunk_bboxes(bbox, chunk_m):
    """Split a lon/lat bbox into a grid of cells about chunk_m wide (in UTM metres)."""
    w, s, e, n = bbox
    (x0, y0), (x1, y1) = lonlat_to_utm(w, s), lonlat_to_utm(e, n)
    nx, ny = max(1, math.ceil((x1 - x0) / chunk_m)), max(1, math.ceil((y1 - y0) / chunk_m))
    lons = [w + (e - w) * i / nx for i in range(nx + 1)]
    lats = [s + (n - s) * j / ny for j in range(ny + 1)]
    return [(lons[i], lats[j], lons[i + 1], lats[j + 1]) for j in range(ny) for i in range(nx)]


def fetch_chunked(bbox, tags, chunk_m, label):
    """One small Overpass request per cell instead of one large one (large ones time out on the public servers).
    Each cell's response lands in the OSMnx cache, so a re-run after a failure only re-requests missing cells.
    Features crossing cell borders come back from several cells and are de-duplicated by OSM id."""
    cells = chunk_bboxes(bbox, chunk_m)
    parts = []
    for k, cell in enumerate(cells, 1):
        try:
            part = with_overpass_retry(ox.features_from_bbox, cell, tags=tags)
        except ox._errors.InsufficientResponseError:  # nothing with these tags in this cell
            part = None
        print(f"[{label}] cell {k}/{len(cells)}: {0 if part is None else len(part)} features", flush=True)
        if part is not None and len(part):
            parts.append(part)
    if not parts:
        raise ox._errors.InsufficientResponseError(f"no features for {list(tags)} in {bbox}")
    f = pd.concat(parts)
    return gpd.GeoDataFrame(f[~f.index.duplicated()], crs=parts[0].crs)


def fetch(bbox, tags, chunk_m, label, polygons_out=None):
    f = fetch_chunked(bbox, tags, chunk_m, label)
    f = f.to_crs(PROJECTED_CRS)
    if polygons_out is not None:  # keep the real outlines of area features (e.g. pitches) in a second layer
        areas = f[f.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        areas["osm_type"] = [i[0] for i in areas.index]
        areas["osm_id"] = [i[1] for i in areas.index]
        polygons_out.append(areas[[c for c in KEEP if c in areas.columns] + ["osm_type", "osm_id", "geometry"]]
                            .reset_index(drop=True))
    f["osm_type"] = [i[0] for i in f.index]
    f["osm_id"] = [i[1] for i in f.index]
    f["area_m2"] = f.geometry.area.round(1)
    f["length_m"] = f.geometry.length.round(1)
    f["geometry"] = f.geometry.representative_point()
    keep = [c for c in KEEP if c in f.columns] + ["osm_type", "osm_id", "area_m2", "length_m", "geometry"]
    return f[keep].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    ap.add_argument("--set", default="beach", choices=list(CONFIG["feature_sets"]))
    ap.add_argument("--chunk-m", type=float, default=1000.0,
                    help="query cell size in metres (default 1000; smaller if Overpass still times out)")
    args = ap.parse_args()
    fs = CONFIG["feature_sets"][args.set]
    out = RAW_DIR / "osm" / args.aoi
    out.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osm" / "_overpass_cache")
    ox.settings.requests_timeout = 120  # small cells answer quickly; fail over to the next mirror sooner
    ox.settings.log_console = False

    bbox = anchor_bbox_lonlat(out, fs["anchor_names"], fs["pad_m"]) or aoi_bbox_lonlat(args.aoi)
    print(f"[{args.set}] query bbox (lon/lat) {tuple(round(v, 5) for v in bbox)}, "
          f"{len(chunk_bboxes(bbox, args.chunk_m))} cells of ~{args.chunk_m:.0f} m")
    areas = []
    parts, failed = [fetch(bbox, fs["tags"], args.chunk_m, args.set, areas)], []
    if fs.get("optional_tags"):
        try:
            parts.append(fetch(bbox, fs["optional_tags"], args.chunk_m, args.set + ":optional"))
        except Exception as e:  # noqa: BLE001 - optional layer, the main set is still useful
            failed = list(fs["optional_tags"])
            print(f"[{args.set}] WARNING: optional tags {failed} failed ({str(e).splitlines()[0][:80]}); continuing")
    f = flatten_for_gpkg(gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=PROJECTED_CRS))
    f = f.drop_duplicates(subset=["osm_type", "osm_id"])
    f.to_file(out / f"{args.set}.gpkg", layer="features", driver="GPKG")
    if areas and len(areas[0]):
        flatten_for_gpkg(gpd.GeoDataFrame(areas[0], crs=PROJECTED_CRS)).to_file(out / f"{args.set}.gpkg", layer="areas",
                                                                             driver="GPKG")
    print(f"[{args.set}] {len(f)} features -> {out / (args.set + '.gpkg')}")
    write_metadata(out / f"{args.set}_metadata.json", aoi=args.aoi, set=args.set, bbox_lonlat=bbox, crs=PROJECTED_CRS,
                   tags=fs["tags"], optional_tags=fs.get("optional_tags"), optional_failed=failed, count=len(f),
                   license=CONFIG["osm"]["license"], attribution=CONFIG["osm"]["attribution"])


if __name__ == "__main__":
    main()
