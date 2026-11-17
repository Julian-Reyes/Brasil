"""Step 4 - top-down reference map of the REAL geography for an AOI (Phase 3 design input).

Hillshaded ground model, roads coloured by class (tunnels dashed), buildings, favela areas,
neighbourhood labels, peaks and a 1 km UTM grid. Output: Docs/Map/reference/<aoi>.png

Usage:  .venv/bin/python scripts/reference_map.py --aoi full
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import LightSource  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import CONFIG, DOCS_DIR, PROCESSED_DIR, RAW_DIR, aoi_bbox_utm  # noqa: E402
from rio_geo.roads import CLASS_INFO, _first  # noqa: E402

STYLE = {"major": ("#d1495b", 1.6), "minor": ("#3d3d3d", 0.6), "path": ("#8d6e3f", 0.35), "steps": ("#ff8c00", 0.5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()
    osm = RAW_DIR / "osm" / args.aoi
    W, S, E, N = aoi_bbox_utm(args.aoi)
    span = max(E - W, N - S)
    fig, ax = plt.subplots(figsize=(14, 14 * (N - S) / (E - W)))

    with rasterio.open(PROCESSED_DIR / "dem" / "full_10m.tif") as dem:
        win = rasterio.windows.from_bounds(W, S, E, N, dem.transform)
        z = dem.read(1, window=win)
    ls = LightSource(azdeg=315, altdeg=40)
    sea = z <= 0.05
    rgb = ls.shade(np.where(sea, 0, z), cmap=plt.cm.gist_earth, vert_exag=2, dx=10, dy=10,
                   blend_mode="soft", vmin=-150, vmax=800)
    rgb[sea] = (0.70, 0.82, 0.92, 1)
    ax.imshow(rgb, extent=(W, E, S, N), origin="upper", zorder=0)

    ref = gpd.read_file(osm / "reference.gpkg", layer="polygons")
    fav = ref[ref.get("residential", "").astype(str).eq("favela")]
    fav.plot(ax=ax, facecolor="#ff7f0e", alpha=0.25, edgecolor="#c85a00", linewidth=0.8, zorder=1)
    b = gpd.read_file(osm / "buildings.gpkg")
    b.plot(ax=ax, color="#555555", alpha=0.45 if span > 5000 else 0.7, linewidth=0, zorder=2)

    e = gpd.read_file(osm / "roads.gpkg", layer="edges")
    e["cls"] = e["highway"].map(_first)
    e = e[e["cls"].isin(CLASS_INFO)]
    e["group"] = e["cls"].map(lambda c: CLASS_INFO[c][1])
    tunnel = e["tunnel"].map(_first).eq("yes") if "tunnel" in e else np.zeros(len(e), bool)
    scale = 1.0 if span < 5000 else 0.6
    for group, (color, lw) in STYLE.items():
        sel = e[(e["group"] == group) & ~tunnel]
        sel.plot(ax=ax, color=color, linewidth=lw * scale * (2 if span < 5000 else 1), zorder=3 + (group == "major"))
    e[tunnel].plot(ax=ax, color="#7b2cbf", linewidth=1.6, linestyle=(0, (2, 1.5)), zorder=5)

    labels = ref[ref.get("admin_level", "").astype(str).eq("10")]
    for _, r in labels.iterrows():
        p = r.geometry.representative_point()
        if W < p.x < E and S < p.y < N:
            ax.text(p.x, p.y, r["name"], fontsize=9 if span > 5000 else 11, weight="bold", ha="center",
                    color="#111", zorder=7, bbox=dict(facecolor="white", alpha=0.6, linewidth=0, pad=1.2))
    pts = gpd.read_file(osm / "reference.gpkg", layer="points")
    peaks = pts[pts.get("natural", "").astype(str).eq("peak") & pts["name"].notna()]
    for _, r in peaks.iterrows():
        ax.plot(r.geometry.x, r.geometry.y, "^", color="black", ms=5, zorder=8)
        ax.text(r.geometry.x + span * 0.004, r.geometry.y, f"{r['name']} {r.get('ele') or ''}", fontsize=7, zorder=8)

    grid = 1000 if span > 3000 else 250
    ax.set_xticks(np.arange(np.ceil(W / grid) * grid, E, grid))
    ax.set_yticks(np.arange(np.ceil(S / grid) * grid, N, grid))
    ax.tick_params(labelsize=6)
    ax.grid(color="white", alpha=0.35, linewidth=0.5)
    ax.set_xlim(W, E)
    ax.set_ylim(S, N)
    ax.set_title(f"Rio de Janeiro reference - {args.aoi} (EPSG:31983, {grid} m grid)\n"
                 "red: arterial | dark: local | brown: footway | orange: steps | purple dashed: tunnel | "
                 "orange areas: favelas", fontsize=10)
    fig.text(0.01, 0.005, CONFIG["osm"]["attribution"] + "  DEM: Copernicus GLO-30.", fontsize=6)
    out = DOCS_DIR / "reference" / f"{args.aoi}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=args.dpi, bbox_inches="tight")
    print(f"reference map -> {out}")


if __name__ == "__main__":
    main()
