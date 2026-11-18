"""Step 1b - download beach furniture for an AOI (lifeguard posts, kiosks, showers, pitches).

Queries only a strip around config beach.beach_names (+150 m) when reference.gpkg exists.

Kept separate from fetch_osm.py so the large full-area download does not have to be repeated. Point-like
features only; the beach polygons, coastline and promenade paths already come from fetch_osm.py.

Output: GeoData/raw/osm/<aoi>/beach.gpkg (layer 'features', project CRS, one point per feature; areas are
reduced to their representative point and keep their footprint area in `area_m2`).

Usage:  .venv/bin/python scripts/fetch_beach.py --aoi test_leblon_arpoador
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd
import osmnx as ox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import CONFIG, PROJECTED_CRS, RAW_DIR, aoi_bbox_lonlat, utm_to_lonlat, write_metadata  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm import flatten_for_gpkg, with_overpass_retry  # noqa: E402

BEACH_TAGS = {
    "emergency": ["lifeguard", "lifeguard_tower", "lifeguard_base", "lifeguard_platform"],
    "shop": ["kiosk"],
    "amenity": ["fast_food", "bar", "cafe", "kiosk", "shower", "toilets", "drinking_water", "lifeguard"],
    "leisure": ["pitch", "fitness_station", "playground"],
    "man_made": ["tower", "lifeguard_tower"],
}
KEEP = ["name", "emergency", "shop", "amenity", "natural", "leisure", "man_made", "sport", "species",
        "genus", "leaf_type", "ref", "operator", "tower:type"]


def beach_bbox_lonlat(osm_dir, pad_m=150.0):
    """Query only a strip around the configured beaches: the whole-AOI query times out on the public servers."""
    ref = osm_dir / "reference.gpkg"
    if not ref.exists():
        return None
    polys = gpd.read_file(ref, layer="polygons")
    sand = polys[polys["name"].isin(CONFIG["beach"]["beach_names"])]
    if sand.empty:
        return None
    w, s, e, n = sand.total_bounds
    lons, lats = zip(*[utm_to_lonlat(x, y) for x, y in ((w - pad_m, s - pad_m), (e + pad_m, n + pad_m))])
    return (min(lons), min(lats), max(lons), max(lats))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    args = ap.parse_args()
    out = RAW_DIR / "osm" / args.aoi
    out.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osm" / "_overpass_cache")
    ox.settings.requests_timeout = 300
    ox.settings.log_console = False

    bbox = beach_bbox_lonlat(out) or aoi_bbox_lonlat(args.aoi)
    print(f"[beach] query bbox (lon/lat) {tuple(round(v, 5) for v in bbox)}")
    f = with_overpass_retry(ox.features_from_bbox, bbox, tags=BEACH_TAGS)
    f = f.to_crs(PROJECTED_CRS)
    f["osm_type"] = [i[0] for i in f.index]
    f["osm_id"] = [i[1] for i in f.index]
    f["area_m2"] = f.geometry.area.round(1)
    f["length_m"] = f.geometry.length.round(1)
    f["geometry"] = f.geometry.representative_point()
    keep = [c for c in KEEP if c in f.columns] + ["osm_type", "osm_id", "area_m2", "length_m", "geometry"]
    f = flatten_for_gpkg(f[keep].reset_index(drop=True))
    f.to_file(out / "beach.gpkg", layer="features", driver="GPKG")
    print(f"[beach] {len(f)} features -> {out / 'beach.gpkg'}")
    write_metadata(out / "beach_metadata.json", aoi=args.aoi, bbox_lonlat=bbox, crs=PROJECTED_CRS, tags=BEACH_TAGS,
                   count=len(f), license=CONFIG["osm"]["license"], attribution=CONFIG["osm"]["attribution"])


if __name__ == "__main__":
    main()
