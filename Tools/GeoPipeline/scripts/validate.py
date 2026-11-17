"""Step 3 - verify downloaded data before anything is built from it.

Checks projection, AOI coverage, geometry validity, road-graph connectivity, tag coverage,
DEM sanity against surveyed heights, and that licence/attribution metadata is present.
Writes Docs/Map/validation/<aoi>.md and exits non-zero if a blocking check fails.

Usage:  .venv/bin/python scripts/validate.py --aoi test_leblon_vidigal
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import (CONFIG, DOCS_DIR, PROCESSED_DIR, PROJECTED_CRS, RAW_DIR,  # noqa: E402
                            aoi_bbox_utm, lonlat_to_utm)


class Report:
    def __init__(self):
        self.rows, self.failed = [], False

    def check(self, name, ok, detail, blocking=True):
        status = "PASS" if ok else ("FAIL" if blocking else "WARN")
        self.failed |= (not ok and blocking)
        self.rows.append((status, name, detail))
        print(f"[{status}] {name}: {detail}")


def coverage(gdf, bbox):
    """Fraction of the AOI's width/height spanned by the data extent."""
    minx, miny, maxx, maxy = gdf.total_bounds
    w, s, e, n = bbox
    return min((min(maxx, e) - max(minx, w)) / (e - w), (min(maxy, n) - max(miny, s)) / (n - s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    args = ap.parse_args()
    osm_dir = RAW_DIR / "osm" / args.aoi
    bbox = aoi_bbox_utm(args.aoi)
    rep = Report()

    meta = json.loads((osm_dir / "metadata.json").read_text())
    rep.check("OSM licence recorded", "ODbL" in meta.get("license", ""), meta.get("attribution", "missing"))

    edges = gpd.read_file(osm_dir / "roads.gpkg", layer="edges")
    buildings = gpd.read_file(osm_dir / "buildings.gpkg")
    for name, gdf in (("roads", edges), ("buildings", buildings)):
        rep.check(f"{name} CRS", gdf.crs.to_string() == PROJECTED_CRS, gdf.crs.to_string())
        cov = coverage(gdf, bbox)
        rep.check(f"{name} AOI coverage", cov > 0.9, f"{cov:.1%} of AOI extent")
        invalid = int((~gdf.geometry.is_valid).sum() + gdf.geometry.is_empty.sum())
        rep.check(f"{name} geometry validity", invalid / max(len(gdf), 1) < 0.01,
                  f"{invalid} invalid/empty of {len(gdf)}", blocking=False)

    G = ox.load_graphml(osm_dir / "roads.graphml")
    drivable = set(CONFIG["osm"]["drivable_highway"])
    Gd = G.edge_subgraph([(u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
                          if (d["highway"][0] if isinstance(d["highway"], list) else d["highway"]) in drivable])
    comps = sorted(nx.weakly_connected_components(Gd), key=len, reverse=True)
    share = len(comps[0]) / Gd.number_of_nodes() if comps else 0
    # Small AOIs clip roads at the boundary, so many fragments are expected; the main component must dominate.
    rep.check("drivable network connectivity", share > 0.6,
              f"{len(comps)} components, largest holds {share:.1%} of {Gd.number_of_nodes()} nodes")
    hw = edges["highway"].astype(str)
    rep.check("pedestrian network present (favela becos/steps)", hw.str.contains("steps|footway").sum() > 0,
              f"{int(hw.str.contains('steps').sum())} steps, {int(hw.str.contains('footway').sum())} footway edges",
              blocking=False)
    for tag in ("tunnel", "bridge"):
        n = int(edges[tag].notna().sum()) if tag in edges else 0
        rep.check(f"{tag} tagging", True, f"{n} edges tagged {tag}", blocking=False)

    h_cov = buildings["height"].notna().mean() if "height" in buildings else 0.0
    l_cov = buildings["building:levels"].notna().mean() if "building:levels" in buildings else 0.0
    rep.check("building height data", h_cov + l_cov > 0.2,
              f"height {h_cov:.1%}, levels {l_cov:.1%} (rest use deterministic defaults)", blocking=False)

    ref_polys = gpd.read_file(osm_dir / "reference.gpkg", layer="polygons")
    favelas = ref_polys[ref_polys.get("residential", "").astype(str).eq("favela")]
    rep.check("favela areas mapped", len(favelas) > 0,
              ", ".join(sorted(favelas["name"].dropna().unique())) or "none", blocking=False)

    dem_path = PROCESSED_DIR / "dem" / "full_10m.tif"
    with rasterio.open(dem_path) as dem:
        rep.check("DEM CRS", dem.crs.to_string() == PROJECTED_CRS, dem.crs.to_string())
        db = dem.bounds
        rep.check("DEM covers AOI", db.left <= bbox[0] and db.bottom <= bbox[1] and db.right >= bbox[2]
                  and db.top >= bbox[3], f"DEM {tuple(round(v) for v in db)} vs AOI {tuple(round(v) for v in bbox)}")
        checks = [(lm["name"], lonlat_to_utm(*lm["lonlat"]), lm["expected_m"], lm["tolerance_m"])
                  for lm in CONFIG["verification_landmarks"]]
        pts = gpd.read_file(osm_dir / "reference.gpkg", layer="points")
        for _, p in pts[pts.get("natural", "").astype(str).eq("peak") & pts["ele"].notna()].iterrows():
            checks.append((f"OSM peak {p.get('name') or p['osmid']}", (p.geometry.x, p.geometry.y),
                           float(str(p["ele"]).split(";")[0]), 60))
        for name, (e, n), expected, tol in checks:
            if not (db.left < e < db.right and db.bottom < n < db.top):
                continue
            # 30 m DEM under-samples sharp summits: compare against the local max within 60 m.
            r, c = dem.index(e, n)
            win = dem.read(1, window=((r - 6, r + 7), (c - 6, c + 7)))
            got = float(win.max()) if expected > 50 else float(dem.read(1, window=((r, r + 1), (c, c + 1)))[0, 0])
            rep.check(f"DEM vs {name}", abs(got - expected) <= tol, f"DEM {got:.0f} m, reference {expected:.0f} m",
                      blocking=False)
    dem_meta = json.loads((dem_path.parent / "full_metadata.json").read_text())
    rep.check("DEM licence recorded", "Copernicus" in dem_meta.get("attribution", ""), dem_meta["license"])

    out = DOCS_DIR / "validation" / f"{args.aoi}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Data validation - `{args.aoi}`", "",
             "Generated by `Tools/GeoPipeline/scripts/validate.py`. Do not edit by hand.", "",
             "| Status | Check | Detail |", "|---|---|---|"]
    lines += [f"| {s} | {n} | {d} |" for s, n, d in rep.rows]
    lines += ["", "Known caveat: Copernicus GLO-30 is a surface model; built-up flats read high and are "
              "ground-corrected in `build_test_area.py` (see Docs/Map/DataSources.md)."]
    out.write_text("\n".join(lines) + "\n")
    print(f"report -> {out}")
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
