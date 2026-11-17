"""Derive an AOI's raw OSM inputs by clipping an already-downloaded larger AOI (default: full).

Avoids a new Overpass request when the parent extract already covers the AOI. Writes the same files as
fetch_osm.py (roads.gpkg edges/nodes, buildings.gpkg, reference.gpkg if the parent has one, metadata.json).

    .venv/bin/python scripts/clip_osm.py --aoi test_leblon_arpoador [--parent full]
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import CONFIG, RAW_DIR, aoi_bbox_utm, write_metadata  # noqa: E402

MARGIN_M = 200.0  # keep edges/buildings that straddle the edge; build_area clips exactly


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    ap.add_argument("--parent", default="full", choices=list(CONFIG["aois"]))
    args = ap.parse_args()
    src, out = RAW_DIR / "osm" / args.parent, RAW_DIR / "osm" / args.aoi
    out.mkdir(parents=True, exist_ok=True)
    W, S, E, N = aoi_bbox_utm(args.aoi)
    box = (W - MARGIN_M, S - MARGIN_M, E + MARGIN_M, N + MARGIN_M)

    edges = gpd.read_file(src / "roads.gpkg", layer="edges", bbox=box)
    nodes = gpd.read_file(src / "roads.gpkg", layer="nodes")
    node_col = "osmid" if "osmid" in nodes.columns else nodes.columns[0]
    nodes = nodes[nodes[node_col].isin(set(edges["u"]) | set(edges["v"]))]
    edges.to_file(out / "roads.gpkg", layer="edges", driver="GPKG")
    nodes.to_file(out / "roads.gpkg", layer="nodes", driver="GPKG")

    buildings = gpd.read_file(src / "buildings.gpkg", bbox=box)
    buildings.to_file(out / "buildings.gpkg", driver="GPKG")

    counts = {"edges": len(edges), "nodes": len(nodes), "buildings": len(buildings)}
    if (src / "reference.gpkg").exists():
        for layer in ("points", "lines", "polygons"):
            ref = gpd.read_file(src / "reference.gpkg", layer=layer, bbox=box)
            ref.to_file(out / "reference.gpkg", layer=layer, driver="GPKG")
            counts[f"reference_{layer}"] = len(ref)
    else:
        print(f"[clip] {args.parent} has no reference.gpkg yet; favela flags will be missing until re-clipped")

    parent_meta = src / "metadata.json"
    write_metadata(out / "metadata.json", **{
        "source": f"clipped from raw/osm/{args.parent}",
        "parent_metadata": json.loads(parent_meta.read_text()) if parent_meta.exists() else None,
        "bbox_utm": [W, S, E, N], "margin_m": MARGIN_M, "counts": counts,
        "licence": "ODbL 1.0", "attribution": "(c) OpenStreetMap contributors"})
    print(f"[clip] {args.aoi}: {counts}")


if __name__ == "__main__":
    main()
