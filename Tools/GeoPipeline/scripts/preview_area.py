"""Top-down preview of a built AOI, from the exported files (no Unreal needed).

Panels: hillshaded generated terrain with the exported water meshes, road ribbons by class and building
footprints; then height profiles along named roads (spot bumps / spikes in the road solve).

Output: GeoData/exports/<aoi>/preview.png

Usage:  .venv/bin/python scripts/preview_area.py --aoi test_lagoa \
            --roads "Avenida Epitácio Pessoa" "Avenida Borges de Medeiros"
"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.common import CONFIG, EXPORTS_DIR, RAW_DIR, aoi_bbox_utm  # noqa: E402

ROAD_STYLE = {"major": ("#222222", 1.2), "minor": ("#555555", 0.6), "path": ("#a08a5a", 0.3),
              "steps": ("#d0731f", 0.3)}
CLASS_GROUP = {"motorway": "major", "trunk": "major", "primary": "major", "secondary": "major",
               "motorway_link": "major", "trunk_link": "major", "primary_link": "major", "secondary_link": "major",
               "steps": "steps", "footway": "path", "path": "path", "cycleway": "path", "pedestrian": "path",
               "track": "path"}


def obj_triangles(path, pivot):
    v, f = [], []
    for ln in open(path):
        if ln.startswith("v "):
            v.append([float(x) for x in ln.split()[1:3]])
        elif ln.startswith("f "):
            f.append([int(t.split("/")[0]) - 1 for t in ln.split()[1:4]])
    v = np.array(v) / 100.0 + pivot
    return v[np.array(f)] if f else np.zeros((0, 3, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--roads", nargs="*", default=[], help="road names to profile")
    args = ap.parse_args()
    aoi_cfg = CONFIG["aois"][args.aoi]
    bbox = aoi_bbox_utm(args.aoi)
    exp = EXPORTS_DIR / args.aoi
    manifest = json.loads((exp / "manifest.json").read_text())
    graph = json.loads((exp / "road_graph_source.json").read_text())
    report = json.loads((exp / "report.json").read_text())
    terrain = ter.load_terrain_from_manifest(manifest, bbox, aoi_cfg["terrain_grid_m"])

    n_prof = len(args.roads)
    fig = plt.figure(figsize=(15, 15 + 2.6 * n_prof))
    gs = fig.add_gridspec(1 + n_prof, 1, height_ratios=[6] + [1] * n_prof)
    ax = fig.add_subplot(gs[0])
    W, S, E, N = bbox
    z = terrain.z
    gy, gx = np.gradient(z, terrain.step)
    shade = np.clip(0.6 + (-gx * 0.6 + gy * 0.6) / np.sqrt(1 + gx ** 2 + gy ** 2) * 0.8, 0, 1)
    # terrain colormap from its green part upward, so low land is never drawn in water blue; below 0 m = grey-blue
    t = np.clip(z / max(z.max(), 1), 0, 1) ** 0.6
    rgb = plt.cm.terrain(0.25 + 0.7 * t)[..., :3]
    rgb[z < 0] = (0.55, 0.6, 0.65)
    rgb = rgb * shade[..., None]
    ax.imshow(rgb, origin="lower", extent=(W, E, S, N), interpolation="bilinear")
    water = [a for a in manifest["actors"] if a["kind"] == "Water" and "pivot_utm" in a]
    for a in water:
        tris = obj_triangles(a["obj"], np.array(a["pivot_utm"]))
        ax.add_collection(PolyCollection(tris, facecolor="#2f6db5", edgecolor="none", alpha=0.85))
    osm = RAW_DIR / "osm" / args.aoi
    b = gpd.read_file(osm / "buildings.gpkg", bbox=bbox)
    b.plot(ax=ax, color="#f3efe6", edgecolor="#8a8170", linewidth=0.15)
    for e in graph["edges"]:
        p = np.asarray(e["points"])
        col, lw = ROAD_STYLE[CLASS_GROUP.get(e["class"], "minor")]
        ax.plot(p[:, 0], p[:, 1], color=col, lw=lw, solid_capstyle="round")
    for e in graph["edges"]:
        if e["name"] in args.roads:
            p = np.asarray(e["points"])
            ax.plot(p[:, 0], p[:, 1], color="#d62728", lw=1.6, alpha=0.7)
    ax.set_xlim(W, E)
    ax.set_ylim(S, N)
    ax.set_aspect("equal")
    wb = ", ".join(f"{w['name'] or w['kind']} {w['level_m']} m" for w in
                   sorted(report.get("water_bodies", []), key=lambda w: -w["area_m2"])[:4])
    ax.set_title(f"{args.aoi}: generated terrain (hillshade), water meshes (blue), roads, buildings; "
                 f"profiled roads in red\nwater: {wb}", fontsize=9)

    for k, name in enumerate(args.roads):
        ax = fig.add_subplot(gs[1 + k])
        es = [e for e in graph["edges"] if e["name"] == name]
        if not es:
            ax.set_title(f"{name}: not found")
            continue
        merged = linemerge([LineString(np.asarray(e["points"])[:, :2]) for e in es])
        spine = max(getattr(merged, "geoms", [merged]), key=lambda g: g.length)
        for e in es:
            p = np.asarray(e["points"])
            s = [spine.project(Point(x, y)) for x, y in p[:, :2]]
            ground = terrain.sample(p[:, 0], p[:, 1])
            ax.plot(s, p[:, 2], ".", color="k", ms=1.5)
            ax.plot(s, ground, ".", color="#6aa84f", ms=0.8, alpha=0.5)
        ax.set_title(f"{name}: road z (black) and carved terrain (green) along its longest chain "
                     f"({spine.length:.0f} m)", fontsize=9)
        ax.set_ylabel("z (m)")
    fig.tight_layout()
    out = exp / "preview.png"
    fig.savefig(out, dpi=95)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
