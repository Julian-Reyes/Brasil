"""Step 5d - places: pitch surfaces / goals / fences, retaining walls, the designed hub buildings (Vidigal central
hub) and gameplay markers (hub elements + named real places).

Run AFTER build_area.py for the AOI: build_area flattens the terraces (GeoData/exports/<aoi>/terraces.json) and
clears houses from them; this step builds the meshes on the final terrain and appends kind "Places" to the
manifest, plus markers (manifest.json -> markers, kinds such as player_home, crew_hangout, shop_*, police_base).

Usage:  .venv/bin/python scripts/build_places.py --aoi test_vidigal
"""
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio
from shapely import wkt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import places as plc  # noqa: E402
from rio_geo import stairs as st  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.beach import TiledMesh  # noqa: E402
from rio_geo.common import CONFIG, EXPORTS_DIR, PROJECT_DIR, RAW_DIR, aoi_bbox_utm, utm_to_ue, write_metadata  # noqa: E402
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402

PALETTE = {
    "M_Pitch": (0.30, 0.52, 0.30), "M_Concrete": (0.62, 0.62, 0.60), "M_Praca": (0.80, 0.76, 0.68),
    "M_Goal": (0.95, 0.95, 0.95), "M_Fence": (0.35, 0.37, 0.38), "M_RetainingWall": (0.55, 0.53, 0.50),
    "M_Shop": (0.88, 0.80, 0.62), "M_Workshop": (0.55, 0.57, 0.60), "M_PlayerHouse": (0.30, 0.55, 0.75),
    "M_Hangout": (0.70, 0.30, 0.45), "M_Parapet": (0.75, 0.73, 0.70), "M_Awning": (0.85, 0.85, 0.85),
    "M_AwningRed": (0.80, 0.15, 0.12), "M_AwningGreen": (0.15, 0.55, 0.25), "M_AwningYellow": (0.95, 0.78, 0.15),
    "M_AwningBlue": (0.15, 0.35, 0.75), "M_Stair": (0.70, 0.68, 0.64),
    # Forte de Copacabana (rio_geo/fort.py); the fortress body M_FortConcrete is in the blockout palette
    "M_FortTurret": (0.36, 0.40, 0.36), "M_FortGun": (0.20, 0.21, 0.22), "M_FortWall": (0.90, 0.86, 0.74),
    "M_Lawn": (0.36, 0.58, 0.30), "M_Pier": (0.55, 0.45, 0.33),
}
# also used here, defined elsewhere: M_WaterTank (blockout palette), M_Bench / M_TreeTrunk / M_TreeCrown (shore palette)


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    args = ap.parse_args()
    cfg, aoi_cfg = CONFIG["places"], CONFIG["aois"][args.aoi]
    bbox = aoi_bbox_utm(args.aoi)
    exp = EXPORTS_DIR / args.aoi
    out = exp / "places"
    out.mkdir(parents=True, exist_ok=True)
    osm = RAW_DIR / "osm" / args.aoi
    manifest = json.loads((exp / "manifest.json").read_text())
    terr = []
    if (exp / "terraces.json").exists():
        for t in json.loads((exp / "terraces.json").read_text()):
            t["polygon"] = wkt.loads(t["polygon"])
            t["pitch"] = wkt.loads(t["pitch"]) if t.get("pitch") else None
            terr.append(t)
    elems = plc.hub_elements(cfg, bbox)
    stairs = []
    if (exp / "stairs.json").exists():  # designed stairs, resolved by build_area.py
        for s in json.loads((exp / "stairs.json").read_text()):
            s["profile"] = np.asarray(s["profile"])
            s["corridor"] = st.corridor(s["profile"][:, :2], s["width"])
            stairs.append(s)
    terrain = ter.load_terrain_from_manifest(manifest, bbox, aoi_cfg["terrain_grid_m"])
    tm = TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder, clip=True)
    streets = None
    if stairs and (exp / "road_graph_source.json").exists():  # carriageways: no stair side walls on them
        from shapely.geometry import LineString  # noqa: PLC0415
        import shapely  # noqa: PLC0415
        from rio_geo.roads import CLASS_INFO  # noqa: PLC0415
        edges = json.loads((exp / "road_graph_source.json").read_text())["edges"]
        streets = shapely.union_all([LineString(np.asarray(e["points"])[:, :2]).buffer(e["width_m"] / 2, cap_style="flat")
                                     for e in edges if CLASS_INFO.get(e["class"], (0, ""))[1] in ("major", "minor")])
    stats = plc.build(terr, elems, terrain, tm, cfg, stairs=stairs, streets=streets)
    fort_stats = None
    if (osm / "forte.gpkg").exists():  # landmark complex: Forte de Copacabana
        from rio_geo import fort  # noqa: PLC0415
        lines = gpd.read_file(osm / "reference.gpkg", layer="lines")
        coast = lines[lines["natural"].astype(str).eq("coastline")].geometry.union_all() if len(lines) else None
        fort_stats = fort.build(gpd.read_file(osm / "forte.gpkg", layer="areas"),
                                gpd.read_file(osm / "forte.gpkg", layer="features"),
                                gpd.read_file(osm / "buildings.gpkg"), coast, terrain, tm, CONFIG["headlands"],
                                registry={str(e["osm_id"]): e for e in CONFIG.get("landmarks", {}).get("buildings", [])})
        log(f"Forte de Copacabana: {fort_stats if fort_stats else 'not in this AOI'}")
    log(f"terraces {stats['terraces']} ({stats['pitches']} with pitches), retaining walls ~{stats['walls_m']:.0f} m, "
        f"hub buildings {stats['hub_buildings']}, stairs {len(stats['stairs'])}")
    for s in stats["stairs"]:
        log(f"  stair {s['name'].split(': ')[-1]}: {s['steps']} steps, rise {s['rise_m'] * 100:.0f} cm, "
            f"tread {s['tread_m']} m, ends {s['ends']}")

    # named real places: feature sets (points), their areas, and named buildings
    wanted = {k: v for k, v in cfg["landmarks"].items() if not k.startswith("_")}
    found, feats = set(), []
    sources = []
    for fs in CONFIG["feature_sets"]:
        path = osm / f"{fs}.gpkg"
        if path.exists():
            for lyr, _ in pyogrio.list_layers(path):
                sources.append((gpd.read_file(path, layer=lyr), f"osm:{fs}/{lyr}"))
    sources.append((gpd.read_file(osm / "buildings.gpkg"), "osm:buildings"))
    for df, src in sources:
        if "name" not in df:
            continue
        for _, r in df[df["name"].isin(wanted)].iterrows():
            if r["name"] in found:
                continue
            p = r.geometry.representative_point() if r.geometry.geom_type != "Point" else r.geometry
            feats.append((r["name"], wanted[r["name"]], p.x, p.y, src))
            found.add(r["name"])
    missing = sorted(set(wanted) - found)
    if missing:
        log(f"landmarks not found in this AOI: {missing}")
    marks = plc.markers(elems, feats, terrain, utm_to_ue)
    keep = [m for m in manifest.get("markers", []) if m.get("source") not in ("hub",) and not
            str(m.get("source", "")).startswith(("osm:vidigal", "osm:buildings", "osm:lagoa/", "osm:beach/", "osm:forte/", "osm:centro/"))
            or m.get("kind") == "Posto"]
    manifest["markers"] = keep + marks

    write_mtl(out / "places.mtl", PALETTE)
    palette = MeshBuilder()
    for k, name in enumerate(PALETTE):
        palette.add(name, [(k, 0, 0), (k + 1, 0, 0), (k, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
    palette.write_obj(out / "SM_PlacesPalette.obj", "places.mtl")
    actors = []
    for (i, j), mb in sorted(tm.tiles.items()):
        if not mb.triangle_count:
            continue
        name = f"SM_T{i}{j}_Places"
        mb.write_obj(out / f"{name}.obj", None)
        pe, pn = tm.pivot((i, j))[:2]
        actors.append({"asset": name, "kind": "Places", "obj": str(out / f"{name}.obj"), "label": f"GEN_T{i}{j}_Places",
                       "location_cm": list(utm_to_ue(pe, pn, 0.0)), "tile": [i, j], "pivot_utm": [float(pe), float(pn)],
                       "materials": sorted(mb.parts), "triangles": mb.triangle_count, "collision": "complex_as_simple"})
    manifest["actors"] = [a for a in manifest["actors"] if a["kind"] != "Places"] + actors
    (exp / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved = PROJECT_DIR / "Saved" / "RioPipeline" / f"{args.aoi}_manifest.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(manifest, indent=2))
    report = {"aoi": args.aoi, **stats, "fort": fort_stats,
              "terraces": [{"name": t["name"], "kind": t["kind"], "z": t["z"], "area_m2": round(t["polygon"].area)}
                           for t in terr],
              "hub_elements": [{"label": e["label"], "type": e["type"], "z": e.get("z")} for e in elems],
              "markers": marks, "landmarks_missing": missing,
              "triangles_total": sum(a["triangles"] for a in actors)}
    write_metadata(out / "places_report.json", **report)
    for k, hub in enumerate(cfg["hubs"]):
        if plc.hub_elements({"hubs": [hub]}, bbox):
            preview_hub(out / f"hub_preview_{k}.png", hub, manifest, terrain, marks)
    log(f"wrote {len(actors)} places meshes ({report['triangles_total'] / 1e3:.0f} k triangles), {len(marks)} markers")


def up_faces(obj_path, pivot, wanted=None):
    """Upward-facing triangles of an exported OBJ, grouped by material, in UTM (for top-down previews)."""
    V, out, mat = [], {}, None
    faces = []
    for ln in open(obj_path):
        if ln.startswith("v "):
            V.append([float(x) for x in ln.split()[1:4]])
        elif ln.startswith("usemtl"):
            mat = ln.split()[1]
        elif ln.startswith("f "):
            faces.append((mat, [int(t.split("/")[0]) - 1 for t in ln.split()[1:4]]))
    V = np.array(V) / 100.0
    for m, (a, b, c) in faces:
        if wanted and m not in wanted:
            continue
        n = np.cross(V[b] - V[a], V[c] - V[a])
        if n[2] > 0.3 * np.linalg.norm(n) + 1e-9:
            out.setdefault(m, []).append(V[[a, b, c], :2] + pivot)
    return out


def preview_hub(path, hub, manifest, terrain, marks, radius=110.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    cx, cy = hub["center"]
    W, S, E, N = cx - radius * 1.4, cy - radius, cx + radius * 1.4, cy + radius
    fig, ax = plt.subplots(figsize=(14, 10))
    xs, ys = terrain.coords()
    ix, iy = (xs >= W) & (xs <= E), (ys >= S) & (ys <= N)
    z = terrain.z[np.ix_(iy, ix)]
    ax.imshow(z, origin="lower", extent=(W, E, S, N), cmap="Greys", alpha=0.35)
    cs = ax.contour(xs[ix], ys[iy], z, levels=range(0, 600, 2), colors="k", linewidths=0.2, alpha=0.6)
    ax.clabel(cs, levels=range(0, 600, 10), fontsize=6)
    colours = {"M_BuildingInformal": "#c9653a", "M_BuildingFormal": "#e8e3d8", "M_WaterTank": "#1f5fbf",
               "M_RoadMajor": "#222222", "M_RoadMinor": "#444444", "M_Path": "#d9c08a", "M_Steps": "#e0a040",
               "M_TreeCrown": "#2f7d32", "M_Bench": "#6b4a2a"}
    colours.update({k: tuple(v) for k, v in PALETTE.items()})
    order = ["Roads", "Buildings", "Places"]
    for kind in order:
        for a in manifest["actors"]:
            if a["kind"] != kind or "pivot_utm" not in a:
                continue
            px, py = a["pivot_utm"]
            if px > E or px + 500 < W or py > N or py + 500 < S:
                continue
            for m, tris in up_faces(a["obj"], np.array(a["pivot_utm"])).items():
                ax.add_collection(PolyCollection(tris, facecolor=colours.get(m, "#999999"), edgecolor="none"))
    for m in marks:
        x, y = m["utm"][:2]
        if W <= x <= E and S <= y <= N:
            ax.plot(x, y, "o", color="#d62728", ms=4, zorder=5)
            ax.annotate(f"{m['name']} ({m['kind']})", (x, y), fontsize=7, xytext=(4, 4), textcoords="offset points",
                        zorder=6, bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.75, lw=0))
    ax.set_xlim(W, E)
    ax.set_ylim(S, N)
    ax.set_aspect("equal")
    ax.set_title(f"{hub['name']}: top-down of the exported meshes (roofs / surfaces) with gameplay markers; "
                 "2 m contours", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


if __name__ == "__main__":
    main()
