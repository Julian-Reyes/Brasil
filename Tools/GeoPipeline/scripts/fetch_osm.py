"""Step 1 - download OpenStreetMap roads, buildings and reference features for an AOI.

Outputs (GeoData/raw/osm/<aoi>/):
  roads.graphml          full topological road graph (drivable + pedestrian), lon/lat
  roads.gpkg             layers 'edges' and 'nodes', reprojected to the project CRS
  buildings.gpkg         building footprints, project CRS
  reference.gpkg         coastline / water / beaches / peaks / neighbourhoods / landmarks, project CRS
  metadata.json          query, counts, licence and attribution
Raw Overpass responses are cached by OSMnx in GeoData/raw/osm/_overpass_cache, so re-runs are offline.

Usage:  .venv/bin/python scripts/fetch_osm.py --aoi test_leblon_vidigal
"""
import argparse
import sys
import time
from pathlib import Path

import osmnx as ox
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import CONFIG, PROJECTED_CRS, RAW_DIR, aoi_bbox_lonlat, write_metadata  # noqa: E402

REFERENCE_TAGS = {
    "natural": ["coastline", "water", "beach", "peak", "cliff", "bare_rock", "wood"],
    "water": True,
    "place": ["suburb", "neighbourhood", "quarter"],
    "boundary": ["administrative"],
    "landuse": ["residential"],
    "tourism": ["attraction", "viewpoint"],
    "leisure": ["park", "stadium"],
}


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.private.coffee/api",
]


def with_overpass_retry(fn, *args, attempts=6, **kwargs):
    """The public Overpass servers shed load with 429/502/504; rotate mirrors with exponential backoff."""
    delay = 15
    for i in range(attempts):
        ox.settings.overpass_url = OVERPASS_ENDPOINTS[i % len(OVERPASS_ENDPOINTS)]
        try:
            return fn(*args, **kwargs)
        except (ox._errors.ResponseStatusCodeError, requests.exceptions.RequestException) as e:
            if i == attempts - 1:
                raise
            print(f"[osm] {ox.settings.overpass_url} failed ({str(e).splitlines()[0][:80]}); retry in {delay}s")
            time.sleep(delay)
            delay *= 2


def flatten_for_gpkg(gdf):
    """GeoPackage cannot store list/dict cells (OSMnx merges tags into lists on simplification)."""
    gdf = gdf.copy()
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        if gdf[col].map(lambda v: isinstance(v, (list, dict, set))).any():
            gdf[col] = gdf[col].map(lambda v: ";".join(map(str, v)) if isinstance(v, (list, set)) else v)
    return gdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    args = ap.parse_args()

    out = RAW_DIR / "osm" / args.aoi
    out.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osm" / "_overpass_cache")
    ox.settings.requests_timeout = 600
    ox.settings.log_console = False
    ox.settings.useful_tags_way = sorted(set(ox.settings.useful_tags_way) | {
        "tunnel", "bridge", "layer", "surface", "incline", "sidewalk", "junction", "lit"})

    bbox = aoi_bbox_lonlat(args.aoi)
    highway = CONFIG["osm"]["drivable_highway"] + CONFIG["osm"]["pedestrian_highway"]
    custom_filter = f'["highway"~"^({"|".join(highway)})$"]["area"!~"yes"]'
    print(f"[osm] AOI {args.aoi} bbox(lon/lat)={bbox}")

    G = with_overpass_retry(ox.graph_from_bbox, bbox, custom_filter=custom_filter, simplify=True,
                           retain_all=True, truncate_by_edge=True)
    ox.save_graphml(G, out / "roads.graphml")
    nodes, edges = ox.graph_to_gdfs(G)
    nodes = flatten_for_gpkg(nodes.to_crs(PROJECTED_CRS).reset_index())
    edges = flatten_for_gpkg(edges.to_crs(PROJECTED_CRS).reset_index())
    edges.to_file(out / "roads.gpkg", layer="edges", driver="GPKG")
    nodes.to_file(out / "roads.gpkg", layer="nodes", driver="GPKG")
    print(f"[osm] roads: {len(nodes)} nodes, {len(edges)} edges")

    buildings = with_overpass_retry(ox.features_from_bbox, bbox, tags={"building": True})
    buildings = buildings[buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    keep = [c for c in ["building", "building:levels", "height", "roof:levels", "min_height",
                        "name", "amenity", "geometry"] if c in buildings.columns]
    buildings = flatten_for_gpkg(buildings[keep].to_crs(PROJECTED_CRS).reset_index())
    buildings.to_file(out / "buildings.gpkg", layer="buildings", driver="GPKG")
    print(f"[osm] buildings: {len(buildings)}")

    ref = with_overpass_retry(ox.features_from_bbox, bbox, tags=REFERENCE_TAGS)
    keep = [c for c in ["name", "natural", "water", "place", "boundary", "admin_level", "landuse",
                        "residential", "tourism", "leisure", "ele", "wikidata", "geometry"] if c in ref.columns]
    ref = flatten_for_gpkg(ref[keep].to_crs(PROJECTED_CRS).reset_index())
    for gtype, layer in (("Point", "points"), ("LineString", "lines"), ("Polygon", "polygons")):
        part = ref[ref.geometry.geom_type.str.replace("Multi", "") == gtype]
        if len(part):
            part.to_file(out / "reference.gpkg", layer=layer, driver="GPKG")
    print(f"[osm] reference features: {len(ref)}")

    write_metadata(
        out / "metadata.json", aoi=args.aoi, bbox_lonlat=bbox, crs=PROJECTED_CRS,
        highway_filter=custom_filter, reference_tags=REFERENCE_TAGS,
        counts={"nodes": len(nodes), "edges": len(edges), "buildings": len(buildings), "reference": len(ref)},
        highway_counts=pd.Series(edges["highway"]).value_counts().to_dict(),
        license=CONFIG["osm"]["license"], attribution=CONFIG["osm"]["attribution"],
        osmnx_version=ox.__version__)


if __name__ == "__main__":
    main()
