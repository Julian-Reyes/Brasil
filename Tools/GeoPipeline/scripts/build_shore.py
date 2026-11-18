"""Step 5c - lake shore promenade for config shore.water_name (Lagoa Rodrigo de Freitas): walking + bike lanes,
benches, trees / palms, kiosks.

Run AFTER build_area.py for the AOI (it reads that step's terrain tiles, road graph and manifest) and appends the
meshes to the manifest as kind "Shore", so unreal/import_area.py imports them. Geometry outside the AOI is
dropped, so it can also run on an AOI that only contains part of the lagoon (test_leblon_arpoador).

Inputs:  GeoData/raw/osm/<aoi>/{roads,reference,buildings}.gpkg, optional <feature_set>.gpkg
         (scripts/fetch_beach.py --set lagoa); GeoData/exports/<aoi>/ from build_area.py
Outputs: GeoData/exports/<aoi>/shore/SM_T<i><j>_Shore.obj, SM_ShorePalette.obj + shore.mtl, shore_profile.json,
         shore_report.json, shore_preview.png; manifest.json (+ Saved/ mirror) updated.

Usage:  .venv/bin/python scripts/build_shore.py --aoi test_lagoa
"""
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import shore as shr  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.beach import TiledMesh, _runs  # noqa: E402
from rio_geo.common import CONFIG, EXPORTS_DIR, PROJECT_DIR, RAW_DIR, aoi_bbox_utm, utm_to_ue, write_metadata  # noqa: E402
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402

# Only materials that are new here; M_Ciclovia, M_Kiosk(Roof) and M_Palm* come from the beach palette.
PALETTE = {"M_WalkLane": (0.74, 0.71, 0.64), "M_Bench": (0.42, 0.28, 0.16),
           "M_TreeTrunk": (0.36, 0.27, 0.18), "M_TreeCrown": (0.18, 0.40, 0.14)}
PATH_CLASSES = {"footway", "path", "steps", "cycleway", "pedestrian", "track"}
KIOSK_AMENITY = {"kiosk", "bar", "cafe", "fast_food", "restaurant", "ice_cream"}


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    args = ap.parse_args()
    cfg, aoi_cfg = CONFIG["shore"], CONFIG["aois"][args.aoi]
    bbox = aoi_bbox_utm(args.aoi)
    exp = EXPORTS_DIR / args.aoi
    out = exp / "shore"
    out.mkdir(parents=True, exist_ok=True)
    osm = RAW_DIR / "osm" / args.aoi
    manifest = json.loads((exp / "manifest.json").read_text())
    graph = json.loads((exp / "road_graph_source.json").read_text())

    polys = gpd.read_file(osm / "reference.gpkg", layer="polygons")
    water = polys[polys["name"].astype(str).eq(cfg["water_name"])]
    if water.empty:
        raise SystemExit(f"{cfg['water_name']} not in {osm / 'reference.gpkg'}")
    water = water.geometry.iloc[0]
    edges = gpd.read_file(osm / "roads.gpkg", layer="edges")
    nm, hw = edges["name"].astype(str), edges["highway"].astype(str)
    path_lines = edges[nm.str.contains("|".join(cfg["path_names"]), regex=True)
                       & hw.str.contains("footway|cycleway|path|pedestrian")].geometry.union_all()
    road_polys = [LineString(np.asarray(e["points"])[:, :2]).buffer(e["width_m"] / 2, cap_style="flat")
                  for e in graph["edges"] if e["class"] not in PATH_CLASSES and len(e["points"]) >= 2]
    road_tree = STRtree(road_polys)
    road_union = gpd.GeoSeries(road_polys).union_all()
    blds = gpd.read_file(osm / "buildings.gpkg")
    blds = blds[blds.intersects(water.buffer(cfg["strip_max_m"] + 40))]
    building_geoms = list(blds.geometry)
    building_tree = STRtree(building_geoms)
    building_union = blds.geometry.union_all()

    features = {"kiosk": [], "bench": [], "tree": []}
    fpath = osm / f"{cfg['feature_set']}.gpkg"
    if fpath.exists():
        f = gpd.read_file(fpath, layer="features")
        for _, r in f.iterrows():
            rec = {"x": r.geometry.x, "y": r.geometry.y, "name": None if str(r.get("name")) == "nan" else r.get("name"),
                   "osm_id": f"{r['osm_type']}/{r['osm_id']}"}
            am, sh, nat = str(r.get("amenity")), str(r.get("shop")), str(r.get("natural"))
            if am == "bench":
                features["bench"].append(rec)
            elif nat == "tree":
                features["tree"].append(rec)
            elif sh == "kiosk" or am in KIOSK_AMENITY:
                features["kiosk"].append(rec)
        log(f"features: {', '.join(f'{len(v)} {k}' for k, v in features.items())} from {fpath.name}")
    else:
        log(f"WARNING: no {fpath.name} (scripts/fetch_beach.py --set {cfg['feature_set']}): procedural benches/trees only")

    loop = shr.make_loop(water, cfg)
    prof = shr.measure_profile(loop, path_lines, road_union, building_union, cfg)
    prof.update({"aoi": args.aoi, "water": cfg["water_name"], "frame": "metres from the shoreline towards land",
                 "loop_utm": np.round(loop.xy[::5], 2).tolist()})
    (out / "shore_profile.json").write_text(json.dumps(prof))
    log(f"loop {loop.length:.0f} m; profile measured {prof['measured']}")

    terrain = ter.load_terrain_from_manifest(manifest, bbox, aoi_cfg["terrain_grid_m"])
    tm = TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder, clip=True)
    stats = shr.build(loop, prof, terrain, tm, cfg, features, road_tree, road_polys, building_tree, building_geoms)
    dbg = stats.pop("_debug")
    log(f"lanes {stats['bike_lane_m']:.0f} m bike / {stats['walk_lane_m']:.0f} m walk; kiosks {stats['kiosks']} "
        f"(+{stats['kiosks_skipped']} OSM skipped); benches {stats['benches']}; trees {stats['trees']}")

    write_mtl(out / "shore.mtl", PALETTE)
    palette = MeshBuilder()
    for k, name in enumerate(PALETTE):
        palette.add(name, [(k, 0, 0), (k + 1, 0, 0), (k, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
    palette.write_obj(out / "SM_ShorePalette.obj", "shore.mtl")
    actors = []
    for (i, j), mb in sorted(tm.tiles.items()):
        if not mb.triangle_count:
            continue
        name = f"SM_T{i}{j}_Shore"
        mb.write_obj(out / f"{name}.obj", None)
        pe, pn = tm.pivot((i, j))[:2]
        actors.append({"asset": name, "kind": "Shore", "obj": str(out / f"{name}.obj"), "label": f"GEN_T{i}{j}_Shore",
                       "location_cm": list(utm_to_ue(pe, pn, 0.0)), "tile": [i, j], "pivot_utm": [float(pe), float(pn)],
                       "materials": sorted(mb.parts), "triangles": mb.triangle_count, "collision": "complex_as_simple"})
    manifest["actors"] = [a for a in manifest["actors"] if a["kind"] != "Shore"] + actors
    (exp / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved = PROJECT_DIR / "Saved" / "RioPipeline" / f"{args.aoi}_manifest.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(manifest, indent=2))

    report = {"aoi": args.aoi, "water": cfg["water_name"], "profile_measured_fraction": prof["measured"], **stats,
              "kiosk_list": [{k: q.get(k) for k in ("name", "osm_id", "source", "side", "xyz", "skipped")} for q in dbg["kiosks"]],
              "triangles": {a["asset"]: a["triangles"] for a in actors},
              "triangles_total": sum(a["triangles"] for a in actors)}
    write_metadata(out / "shore_report.json", **report)
    preview(out / "shore_preview.png", loop, prof, water, dbg, graph, building_geoms, bbox)
    log(f"wrote {len(actors)} shore meshes ({report['triangles_total'] / 1e3:.0f} k triangles) -> {out}")


def preview(path, loop, prof, water, dbg, graph, building_geoms, bbox):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    fig = plt.figure(figsize=(16, 19))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.4, 1.1, 0.6])

    def draw(ax, detail):
        for g in getattr(water, "geoms", [water]):
            ax.fill(*g.exterior.xy, color="#9cc3e6", lw=0)
            for h in g.interiors:
                ax.fill(*h.xy, color="white", lw=0)
        ax.add_collection(PolyCollection([np.asarray(b.exterior.coords) for b in building_geoms
                                          if b.geom_type == "Polygon"], facecolor="#ddd8cc", edgecolor="#9a9283",
                                         linewidth=0.2))
        for e in graph["edges"]:
            p = np.asarray(e["points"])
            if e["class"] in PATH_CLASSES:
                ax.plot(p[:, 0], p[:, 1], color="#b39b6b", lw=0.4)
            else:
                ax.plot(p[:, 0], p[:, 1], color="#333", lw=2.5 if detail else 0.8, alpha=0.5)
        for lo, hi, rows, col in ((dbg["green_lo"], dbg["green_hi"], dbg["green_hi"] - dbg["green_lo"] > 0, "#b9d8a5"),
                                  (dbg["inner"], dbg["walk_out"], dbg["walk_rows"], "#a8a294"),
                                  (dbg["walk_out"], dbg["bike_out"], dbg["has_lanes"], "#b3261e")):
            for a, b in _runs(rows):
                ring = np.vstack([loop.at(lo)[a:b + 1], loop.at(hi)[a:b + 1][::-1]])
                ax.fill(*ring.T, color=col, lw=0)
        pl = dbg["placed"]
        ax.plot(pl[:, 0], pl[:, 1], "o", color="#2f6b1f", ms=2.5 if detail else 0.8)
        k = np.array([q["xyz"][:2] for q in dbg["kiosks"] if "xyz" in q]).reshape(-1, 2)
        ax.plot(k[:, 0], k[:, 1], "s", color="#1f4d3d", ms=6 if detail else 3)
        ax.set_aspect("equal")

    ax = fig.add_subplot(gs[0, :])
    draw(ax, False)
    w, s, e, n = water.bounds
    ax.set_xlim(w - 120, e + 120)
    ax.set_ylim(s - 120, n + 120)
    ax.set_title("Lagoa shore promenade, top down: grey = walking lane, red = bike lane, light green = green strip, "
                 "dots = trees / palms / benches, squares = kiosks; lagoon light blue, roads dark", fontsize=9)
    for col, (cx, cy, title) in enumerate(((682780, 7458350, "West shore (Parque dos Patins)"),
                                           (683900, 7458650, "East shore (narrow strip under the hill)"))):
        ax = fig.add_subplot(gs[1, col])
        draw(ax, True)
        ax.set_xlim(cx - 140, cx + 140)
        ax.set_ylim(cy - 140, cy + 140)
        ax.set_title(title, fontsize=9)
    ax = fig.add_subplot(gs[2, :])
    st = loop.s
    ax.plot(st, dbg["inner"], label="walking lane inner edge")
    ax.plot(st, dbg["bike_out"], label="bike lane outer edge")
    ax.plot(st, dbg["green_hi"], label="green strip outer edge")
    ax.plot(st, dbg["limit"], label="limit (road / building - clearance)", color="k", lw=0.8)
    ax.set_ylim(0, 60)
    ax.set_ylabel("m from shoreline (land side)")
    ax.set_xlabel("station around the lagoon (m, counter-clockwise)")
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=95)
    plt.close(fig)


if __name__ == "__main__":
    main()
