"""Step 5b - hero beach blockout (sand, ciclovia, calcadao, kiosks, Postos, palm rows, Jardim de Alah mouth).

Run AFTER build_area.py for the same AOI: it reads that step's solved road heights (road_graph_source.json),
its terrain tiles (to drape the sand) and its manifest, and appends the beach meshes to the manifest so the
normal unreal/import_area.py imports them (kind "Beach", outliner folder Generated/<Area>/Beach).

Everything is derived from the beachfront centreline plus a measured cross-section profile
(GeoData/exports/<aoi>/beach/beach_profile.json); see rio_geo/beach.py.

Inputs:  GeoData/raw/osm/<aoi>/{roads,reference}.gpkg, optional beach.gpkg (scripts/fetch_beach.py; without it
         there are no Postos and kiosks are procedural only), GeoData/exports/<aoi>/ from build_area.py
Outputs: GeoData/exports/<aoi>/beach/SM_T<i><j>_Beach.obj, SM_BeachPalette.obj + beach.mtl,
         beach_profile.json, beach_report.json, beach_preview.png; manifest.json (+ Saved/ mirror) updated.

Usage:  .venv/bin/python scripts/build_beach.py --aoi test_leblon_arpoador
"""
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import beach as bch  # noqa: E402
from rio_geo import beach_life as life  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.common import CONFIG, EXPORTS_DIR, PROJECT_DIR, RAW_DIR, aoi_bbox_utm, utm_to_ue, write_metadata  # noqa: E402
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402

PALETTE = {
    "M_Sand": (0.86, 0.78, 0.58), "M_Calcadao": (0.86, 0.86, 0.83), "M_Ciclovia": (0.55, 0.12, 0.10),
    "M_Kiosk": (0.18, 0.42, 0.33), "M_KioskRoof": (0.93, 0.93, 0.90), "M_Posto": (0.92, 0.92, 0.90),
    "M_PostoCabin": (0.88, 0.32, 0.10), "M_PalmTrunk": (0.45, 0.35, 0.22), "M_PalmLeaf": (0.22, 0.48, 0.16),
    # beach life (rio_geo/beach_life.py)
    "M_UmbrellaRed": (0.85, 0.18, 0.15), "M_UmbrellaYellow": (0.95, 0.80, 0.15), "M_UmbrellaBlue": (0.15, 0.40, 0.80),
    "M_UmbrellaGreen": (0.20, 0.62, 0.30), "M_UmbrellaWhite": (0.95, 0.95, 0.92), "M_UmbrellaOrange": (0.95, 0.50, 0.12),
    "M_ChairFrame": (0.75, 0.76, 0.78), "M_Barraca": (0.55, 0.40, 0.25), "M_CourtLine": (0.10, 0.35, 0.85),
    "M_NetPost": (0.30, 0.30, 0.32), "M_Net": (0.12, 0.12, 0.12),
}


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    args = ap.parse_args()
    aoi_cfg = CONFIG["aois"][args.aoi]
    cfg = {**CONFIG["beach"], **aoi_cfg.get("beach", {})}  # per-AOI overrides: front_roads, beach_names, feature_set
    bbox = aoi_bbox_utm(args.aoi)
    exp = EXPORTS_DIR / args.aoi
    out = exp / "beach"
    out.mkdir(parents=True, exist_ok=True)
    osm = RAW_DIR / "osm" / args.aoi
    manifest = json.loads((exp / "manifest.json").read_text())
    graph = json.loads((exp / "road_graph_source.json").read_text())

    polys = gpd.read_file(osm / "reference.gpkg", layer="polygons")
    lines = gpd.read_file(osm / "reference.gpkg", layer="lines")
    sand = polys[polys["name"].isin(cfg["beach_names"])]
    missing = set(cfg["beach_names"]) - set(sand["name"])
    if missing:
        log(f"WARNING: beach polygons not found: {sorted(missing)}")
    sand = sand.geometry.union_all()
    coast = lines[lines["natural"].astype(str).eq("coastline")].geometry.union_all()
    is_water = polys["natural"].astype(str).eq("water") | polys["water"].notna()
    water = polys[is_water & polys.intersects(sand.buffer(30))].geometry.union_all()
    edges = gpd.read_file(osm / "roads.gpkg", layer="edges")
    cycleways = edges[edges["highway"].astype(str).str.contains("cycleway")].geometry.union_all()
    features = None
    fset = cfg.get("feature_set", "beach")
    if (osm / f"{fset}.gpkg").exists():
        features = gpd.read_file(osm / f"{fset}.gpkg", layer="features")
    else:
        log(f"WARNING: no {fset}.gpkg (run scripts/fetch_beach.py --set {fset}): no Postos, procedural kiosks only")
    # Postos are mapped inconsistently in OSM: 7-10 and 12 as amenity=toilets named "Posto N", Posto 11 only as a
    # building. Add named Posto buildings ("Posto <number>" only, so fuel stations etc. are ignored).
    blds = gpd.read_file(osm / "buildings.gpkg")
    posto_blds = blds[blds["name"].astype(str).str.fullmatch(r"(?i)posto\s*\d+")].copy()
    if len(posto_blds):
        posto_blds["geometry"] = posto_blds.geometry.representative_point()
        posto_blds["osm_type"], posto_blds["osm_id"] = "building", posto_blds.get("id", posto_blds.index)
        posto_blds = posto_blds[["name", "osm_type", "osm_id", "geometry"]]
        features = posto_blds if features is None else gpd.GeoDataFrame(
            pd.concat([features, posto_blds], ignore_index=True), crs=features.crs)

    cl, used = bch.build_centreline(graph["edges"], cfg["front_roads"], coast, cfg,
                                    CONFIG["roads"]["surface_offset_m"])
    cl, trimmed = cl.trimmed_to(sand, cfg["trim_margin_m"])
    log(f"centreline: {cl.length:.0f} m from {len(used)} sea-side edges (trimmed W/E ends {trimmed} m); "
        f"z {cl.z.min():.2f}..{cl.z.max():.2f} m")
    prof = bch.measure_profile(cl, sand, coast, cycleways, water, cfg)
    prof.update({"aoi": args.aoi, "front_roads": cfg["front_roads"], "frame": "metres seaward of the centreline",
                 "centreline_utm": np.round(cl.xy[::5], 2).tolist()})
    (out / "beach_profile.json").write_text(json.dumps(prof))
    log(f"profile measured: {prof['measured']}; canal spans {prof['canal']}")

    postos, kiosks = bch.place_features(cl, prof, features, cfg)
    terrain = ter.load_terrain_from_manifest(manifest, bbox, aoi_cfg["terrain_grid_m"])
    tm = bch.TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder)
    stats = bch.build_geometry(cl, prof, terrain, tm, cfg, postos, kiosks)
    dbg = stats.pop("_debug")
    log(f"geometry: {len(tm.tiles)} tiles, {len(postos)} postos, "
        f"{sum('xyz' in k for k in kiosks)} kiosks, {stats['palms']} palms")
    tm_life = bch.TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder)
    life_stats = {}
    if cfg.get("life", {}).get("enabled"):
        V, off = dbg["V"], dbg["off"]
        life_stats = life.build_life(cl, dbg, lambda i, o: bch._sand_z(V, off, i, o), tm_life, cfg["life"],
                                     postos, kiosks, features)
        dbg["life"] = life_stats.pop("_debug")
        log(f"beach life: {life_stats}")

    # ---- write meshes + palette
    write_mtl(out / "beach.mtl", PALETTE)
    palette = MeshBuilder()
    for k, name in enumerate(PALETTE):
        palette.add(name, [(k, 0, 0), (k + 1, 0, 0), (k, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
    palette.write_obj(out / "SM_BeachPalette.obj", "beach.mtl")
    actors = []
    for (i, j), mb in sorted(tm.tiles.items()):
        if not mb.triangle_count:
            continue
        name = f"SM_T{i}{j}_Beach"
        mb.write_obj(out / f"{name}.obj", None)
        pe, pn = tm.pivot((i, j))[:2]
        actors.append({"asset": name, "kind": "Beach", "obj": str(out / f"{name}.obj"), "label": f"GEN_T{i}{j}_Beach",
                       "location_cm": list(utm_to_ue(pe, pn, 0.0)), "tile": [i, j], "pivot_utm": [float(pe), float(pn)],
                       "materials": sorted(mb.parts), "triangles": mb.triangle_count, "collision": "complex_as_simple"})
    for (i, j), mb in sorted(tm_life.tiles.items()):
        if not mb.triangle_count:
            continue
        name = f"SM_T{i}{j}_BeachLife"
        mb.write_obj(out / f"{name}.obj", None)
        pe, pn = tm_life.pivot((i, j))[:2]
        actors.append({"asset": name, "kind": "BeachLife", "obj": str(out / f"{name}.obj"),
                       "label": f"GEN_T{i}{j}_BeachLife", "location_cm": list(utm_to_ue(pe, pn, 0.0)), "tile": [i, j],
                       "pivot_utm": [float(pe), float(pn)], "materials": sorted(mb.parts),
                       "triangles": mb.triangle_count, "collision": "none"})
    manifest["actors"] = [a for a in manifest["actors"] if a["kind"] not in ("Beach", "BeachLife")] + actors
    manifest["markers"] = [m for m in manifest.get("markers", []) if m.get("kind") != "Posto"] + [{"name": p["name"], "kind": "Posto", "utm": p["xyz"], "location_cm": list(utm_to_ue(*p["xyz"])),
                            "source": p["source"]} for p in postos]
    (exp / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved = PROJECT_DIR / "Saved" / "RioPipeline" / f"{args.aoi}_manifest.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(manifest, indent=2))

    report = {"aoi": args.aoi, "centreline_m": round(cl.length, 1), "centreline_edges": len(used), "trimmed_ends_m": trimmed,
              "profile_measured_fraction": prof["measured"], "canal_spans_u": prof["canal"], **stats, "life": life_stats,
              "postos": [{k: p[k] for k in ("name", "source", "xyz")} for p in postos],
              "kiosks": {"osm": sum(k["source"] != "procedural" and "xyz" in k for k in kiosks),
                         "procedural": sum(k["source"] == "procedural" and "xyz" in k for k in kiosks),
                         "skipped_canal": sum("skipped" in k for k in kiosks)},
              "triangles": {a["asset"]: a["triangles"] for a in actors},
              "triangles_total": sum(a["triangles"] for a in actors)}
    write_metadata(out / "beach_report.json", **report)
    preview(out / "beach_preview.png", cl, prof, sand, coast, postos, kiosks, dbg)
    log(f"wrote {len(actors)} beach meshes ({report['triangles_total'] / 1e3:.0f} k triangles) -> {out}")
    print(json.dumps({k: v for k, v in report.items() if k not in ("triangles",)}, indent=1))


def preview(path, cl, prof, sand, coast, postos, kiosks, dbg):
    """Top-down check against the real geography: OSM sand outline + coastline vs generated strips, with
    close-ups of the Jardim de Alah mouth and the Arpoador end, then the profile and the road height."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(16, 17))
    gs = fig.add_gridspec(4, 2, height_ratios=[1.0, 1.3, 0.9, 0.7])

    def draw(ax, detail):
        for g in getattr(sand, "geoms", [sand]):
            ax.fill(*g.exterior.xy, color="#efe2b8", lw=0)
        for g in getattr(coast, "geoms", [coast]):
            ax.plot(*g.xy, color="#2a6fb0", lw=1)
        ax.plot(*cl.xy.T, color="k", lw=0.8)
        if detail:
            for lo, hi, col in ((dbg["kerb"], dbg["cic_out"], "#b3261e"), (dbg["cic_out"], dbg["calc_out"], "#9a9a94")):
                ring = np.vstack([cl.at(lo), cl.at(hi)[::-1]])
                ax.fill(*ring.T, color=col, lw=0, alpha=0.9)
            ax.plot(*dbg["palms"].T, "o", color="#3a7d24", ms=3)
            if "life" in dbg:
                ax.plot(*dbg["life"]["umbrellas"].T, ".", color="#d9452b", ms=2)
                ax.plot(*dbg["life"]["barracas"].T, "^", color="#7a5230", ms=5)
                ax.plot(*dbg["life"]["courts"].T, "s", color="#1f5ad6", ms=6, mfc="none")
        ax.plot(*cl.at(dbg["calc_out"]).T, color="#b07b2a", lw=0.8)
        ax.plot(*cl.at(dbg["water"]).T, color="#2a6fb0", lw=0.8, ls="--")
        for a, b in bch._runs(dbg["canal"]):
            ring = np.vstack([cl.at(dbg["calc_out"])[a:b + 1], cl.at(dbg["water"] + 15)[a:b + 1][::-1]])
            ax.fill(*ring.T, color="#2a6fb0", alpha=0.5, lw=0)
        k = np.array([q["xyz"][:2] for q in kiosks if "xyz" in q]).reshape(-1, 2)
        ax.plot(*k.T, "s", color="#2e6b55", ms=5 if detail else 3)
        for p in postos:
            ax.plot(*p["xyz"][:2], "o", color="#e0521a", ms=7)
            ax.annotate(p["name"], p["xyz"][:2], xytext=(0, 8), textcoords="offset points", ha="center", fontsize=7)
        ax.set_aspect("equal")

    ax = fig.add_subplot(gs[0, :])
    draw(ax, False)
    (x0, y0), (x1, y1) = cl.xy.min(0), cl.xy.max(0)
    ax.set_xlim(x0 - 100, x1 + 100)
    ax.set_ylim(y0 - 200, y1 + 60)
    ax.set_title("Hero beach blockout, top down. Fill = OSM sand, blue = OSM coastline; brown = generated sand "
                 "edge, dashed = generated waterline, squares = kiosks, orange = Postos", fontsize=9)
    spans = [(a + b) // 2 for a, b in bch._runs(dbg["canal"])]
    jda = max(spans, key=lambda i: min(i, len(cl.s) - i)) if spans else len(cl.s) // 3  # away from the ends
    for col, (i, title) in enumerate(((jda, "Jardim de Alah mouth"), (len(cl.s) - 60, "Arpoador end"))):
        ax = fig.add_subplot(gs[1, col])
        draw(ax, True)
        cx, cy = cl.xy[i]
        ax.set_xlim(cx - 160, cx + 160)
        ax.set_ylim(cy - 190, cy + 60)
        ax.set_title(f"{title}: red = ciclovia, grey = calcadao, green = palms", fontsize=9)
    ax = fig.add_subplot(gs[2, :])
    st = cl.s
    ax.plot(st, dbg["kerb"], label="kerb (road half-width)")
    ax.plot(st, dbg["cic_out"], label="ciclovia outer edge")
    ax.plot(st, dbg["calc_out"], label="calcadao outer edge = sand start")
    ax.plot(st, dbg["water"], label="waterline")
    for a, b in bch._runs(dbg["canal"]):
        ax.axvspan(st[a], st[b], color="#2a6fb0", alpha=0.25)
    ax.set_ylabel("m seaward of centreline")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title("Cross-section along the beach, W (Leblon) -> E (Arpoador); blue bands = canal mouths", fontsize=9)
    ax = fig.add_subplot(gs[3, :])
    ax.plot(st, cl.z, color="k")
    ax.set_ylabel("road surface z (m)")
    ax.set_xlabel("station along centreline (m)")
    ax.set_title("Beachfront road height from build_area.py (the promenade follows it)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


if __name__ == "__main__":
    main()
