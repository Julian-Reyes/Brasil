"""Step 5e - favela street details: house doors, utility poles + overhead wire tangles, lookouts (rio_geo/details.py).

Run AFTER build_area.py (needs houses.gpkg, road_graph_source.json and the Terrain tiles) and build_places.py
(hub terraces / stairs, which poles and lookouts keep off). Appends kinds "Details" (doors, poles, lookout decks;
complex collision) and "Wires" (cables; no collision) to the manifest, plus lookout markers (kind "lookout").

One-time Unreal step: import details/SM_DetailsPalette.obj with materials into /Game/Rio/Materials/Blockout/.

Usage:  .venv/bin/python scripts/build_details.py --aoi test_vidigal
"""
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from shapely import wkt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import details as dt  # noqa: E402
from rio_geo import favela as fav  # noqa: E402
from rio_geo import places as plc  # noqa: E402
from rio_geo import stairs as st  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.beach import TiledMesh  # noqa: E402
from rio_geo.common import (CONFIG, EXPORTS_DIR, PROJECT_DIR, PROJECTED_CRS, RAW_DIR, aoi_bbox_utm,  # noqa: E402
                            utm_to_ue, write_metadata)
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402
from rio_geo.roads import CLASS_INFO  # noqa: E402

PALETTE = {
    "M_Door": (0.36, 0.24, 0.16), "M_DoorBlue": (0.22, 0.36, 0.52), "M_DoorGreen": (0.24, 0.42, 0.30),
    "M_DoorStep": (0.60, 0.59, 0.56), "M_Shutter": (0.50, 0.52, 0.54), "M_Pole": (0.66, 0.66, 0.64),
    "M_Transformer": (0.38, 0.42, 0.40), "M_Wire": (0.05, 0.05, 0.05), "M_LookoutDeck": (0.70, 0.68, 0.63),
    "M_Railing": (0.25, 0.27, 0.28),
}
# also used here, defined elsewhere: M_Bench (shore palette)


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    args = ap.parse_args()
    cfg, aoi_cfg = CONFIG["details"], CONFIG["aois"][args.aoi]
    bbox = aoi_bbox_utm(args.aoi)
    exp = EXPORTS_DIR / args.aoi
    out = exp / "details"
    out.mkdir(parents=True, exist_ok=True)
    osm = RAW_DIR / "osm" / args.aoi
    manifest = json.loads((exp / "manifest.json").read_text())
    manifest["actors"] = [a for a in manifest["actors"] if a["kind"] not in ("Details", "Wires")]
    manifest["markers"] = [m for m in manifest.get("markers", []) if m.get("source") != "details"]

    houses = dt.Houses(gpd.read_file(exp / "houses.gpkg"))
    circ = dt.Circulation(json.loads((exp / "road_graph_source.json").read_text())["edges"], CLASS_INFO)
    terrain = ter.load_terrain_from_manifest(manifest, bbox, aoi_cfg["terrain_grid_m"])
    log(f"inputs: {len(houses.geoms)} houses ({int(houses.informal.sum())} informal), {len(circ.edges)} edges")

    favelas = gpd.GeoDataFrame(geometry=[], crs=PROJECTED_CRS)
    if (osm / "reference.gpkg").exists():
        ref = gpd.read_file(osm / "reference.gpkg", layer="polygons")
        favelas = ref[ref.get("residential", "").astype(str).eq("favela")]
    zones = fav.infill_zones(favelas, CONFIG["places"]["hubs"], bbox)
    zone = shapely.union_all([z[1] for z in zones]) if zones else shapely.Polygon()
    # hub terraces, buildings and designed stairs: no poles or lookout decks on them
    block = []
    if (exp / "terraces.json").exists():
        block += [wkt.loads(t["polygon"]).buffer(0.5) for t in json.loads((exp / "terraces.json").read_text())]
    block += [e["polygon"].buffer(1.0) for e in plc.hub_elements(CONFIG["places"], bbox)]
    if (exp / "stairs.json").exists():
        block += [st.corridor(np.asarray(s["profile"])[:, :2], s["width"]).buffer(0.8)
                  for s in json.loads((exp / "stairs.json").read_text())]
    blockers = shapely.union_all(block) if block else None
    if blockers is not None:
        shapely.prepare(blockers)

    sites = []
    if (exp / "lookouts.json").exists():  # sited (and their lots cleared) by build_area.py
        sites = json.loads((exp / "lookouts.json").read_text())
        for s in sites:
            s["deck"] = wkt.loads(s["deck"])
        if sites:
            decks = shapely.union_all([s["deck"].buffer(0.8) for s in sites])
            blockers = decks if blockers is None else blockers.union(decks)
            shapely.prepare(blockers)

    tm = TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder, clip=True)
    wm = TiledMesh(bbox, aoi_cfg["tile_size_m"], MeshBuilder, clip=True)
    report = {"aoi": args.aoi}
    if cfg["doors"]["enabled"]:
        parts, report["doors"] = dt.doors(houses, circ, terrain, cfg["doors"])
        for mat, v, tr, anchor in parts:
            tm.add_object(mat, v, tr, anchor)
        log(f"doors: {report['doors']}")
    if cfg["wires"]["enabled"] and not zone.is_empty:
        parts, wires, _, report["wires"] = dt.poles_and_wires(houses, circ, terrain, zone, blockers, cfg["wires"])
        for mat, v, tr, anchor in parts:
            tm.add_object(mat, v, tr, anchor)
        for v, tr, anchor in wires:
            wm.add_object("M_Wire", v, tr, anchor)
        log(f"poles and wires: {report['wires']}")
    for k, s in enumerate(sites):
        for mat, v, tr, anchor in dt.lookout_parts(s, terrain):
            tm.add_object(mat, v, tr, anchor)
        x, y = np.asarray(s["inner"]) + np.asarray(s["n"]) * 1.5
        manifest["markers"].append({"name": f"Mirante {k + 1}", "kind": "lookout", "utm": [round(x, 2), round(y, 2),
                                    round(s["z"], 2)], "location_cm": list(utm_to_ue(x, y, s["z"])),
                                    "heading_deg": s["heading_deg"], "edge": s["edge"], "source": "details"})
    report["lookouts"] = [{k: s[k] for k in ("edge", "z", "score", "heading_deg", "houses_removed")} for s in sites]
    if sites:
        log("lookouts (sited by build_area.py): " + "; ".join(
            f"{s['edge']} @ {s['z']:.0f} m facing {s['heading_deg']:.0f} deg" for s in sites))

    write_mtl(out / "details.mtl", PALETTE)
    palette = MeshBuilder()
    for k, name in enumerate(PALETTE):
        palette.add(name, [(k, 0, 0), (k + 1, 0, 0), (k, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
    palette.write_obj(out / "SM_DetailsPalette.obj", "details.mtl")
    actors = []
    for kind, mesh, collision in (("Details", tm, "complex_as_simple"), ("Wires", wm, "none")):
        for (i, j), mb in sorted(mesh.tiles.items()):
            if not mb.triangle_count:
                continue
            name = f"SM_T{i}{j}_{kind}"
            mb.write_obj(out / f"{name}.obj", None)
            pe, pn = mesh.pivot((i, j))[:2]
            actors.append({"asset": name, "kind": kind, "obj": str(out / f"{name}.obj"), "label": f"GEN_T{i}{j}_{kind}",
                           "location_cm": list(utm_to_ue(pe, pn, 0.0)), "tile": [i, j],
                           "pivot_utm": [float(pe), float(pn)], "materials": sorted(mb.parts),
                           "triangles": mb.triangle_count, "collision": collision})
    manifest["actors"] += actors
    (exp / "manifest.json").write_text(json.dumps(manifest, indent=2))
    saved = PROJECT_DIR / "Saved" / "RioPipeline" / f"{args.aoi}_manifest.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(manifest, indent=2))
    report["triangles"] = {k: sum(a["triangles"] for a in actors if a["kind"] == k) for k in ("Details", "Wires")}
    write_metadata(out / "details_report.json", **report)
    log(f"wrote {len(actors)} meshes: details {report['triangles']['Details'] / 1e3:.0f} k, "
        f"wires {report['triangles']['Wires'] / 1e3:.0f} k triangles")


if __name__ == "__main__":
    main()
