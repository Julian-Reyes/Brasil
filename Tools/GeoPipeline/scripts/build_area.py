"""Step 5 - build a SOURCE-scale (1:1) blockout of an AOI as tiled meshes for Unreal.

Pipeline: DSM -> ground estimate -> 2 m grid -> road profiles -> road-carved terrain -> meshes.

Outputs (GeoData/exports/<aoi>/):
  tiles/T_<i>_<j>_{Terrain,Roads,Buildings}.obj   tile-local meshes, pivot at tile SW corner, z = 0
  Sea.obj                                          sea-level plane over the AOI
  blockout.mtl                                     placeholder colours (slot names match /Game/Rio/Materials/Blockout)
  road_graph_source.json                           3D centrelines + attributes (editable source graph)
  manifest.json                                    asset names, actor transforms and tags for the Unreal import
  report.json                                      verification numbers (grades, conflicts, height sources)

Usage:  .venv/bin/python scripts/build_area.py --aoi test_leblon_vidigal
"""
import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import buildings as bld  # noqa: E402
from rio_geo import roads as rd  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.common import (CONFIG, EXPORTS_DIR, PROCESSED_DIR, PROJECT_DIR, PROJECTED_CRS, RAW_DIR,  # noqa: E402
                            aoi_bbox_utm, utm_to_ue, write_metadata)
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402

PALETTE = {
    "M_Terrain": (0.30, 0.42, 0.22), "M_RoadMajor": (0.06, 0.06, 0.07), "M_RoadMinor": (0.16, 0.16, 0.17),
    "M_Path": (0.62, 0.55, 0.42), "M_Steps": (0.80, 0.45, 0.15), "M_BuildingFormal": (0.72, 0.72, 0.70),
    "M_BuildingInformal": (0.78, 0.40, 0.25), "M_Sea": (0.05, 0.20, 0.40),
}
GEN_TAG = "RioGenerated"


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.1f}s] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    args = ap.parse_args()
    aoi_cfg = CONFIG["aois"][args.aoi]
    rcfg, bcfg = CONFIG["roads"], CONFIG["buildings"]
    bbox = aoi_bbox_utm(args.aoi)
    W, S, E, N = bbox
    tile_m, step = aoi_cfg["tile_size_m"], aoi_cfg["terrain_grid_m"]
    ntx, nty = int(round((E - W) / tile_m)), int(round((N - S) / tile_m))
    per_tile = int(round(tile_m / step))
    assert abs(per_tile * step - tile_m) < 1e-6, f"terrain_grid_m {step} must divide tile_size_m {tile_m}"
    out = EXPORTS_DIR / args.aoi
    (out / "tiles").mkdir(parents=True, exist_ok=True)
    osm = RAW_DIR / "osm" / args.aoi

    # ---------------- inputs
    buildings = gpd.read_file(osm / "buildings.gpkg")
    edges = gpd.read_file(osm / "roads.gpkg", layer="edges")
    nodes = gpd.read_file(osm / "roads.gpkg", layer="nodes")
    if (osm / "reference.gpkg").exists():
        ref = gpd.read_file(osm / "reference.gpkg", layer="polygons")
        favelas = ref[ref.get("residential", "").astype(str).eq("favela")]
    else:
        log("WARNING: no reference.gpkg; all buildings treated as formal (re-run after the reference fetch)")
        favelas = gpd.GeoDataFrame(geometry=[], crs=PROJECTED_CRS)
    for g in (buildings, edges, nodes, favelas):
        assert g.crs.to_string() == PROJECTED_CRS
    favela_union = favelas.geometry.union_all() if len(favelas) else None
    buildings["informal"] = buildings.geometry.centroid.within(favela_union) if favela_union is not None else False
    log(f"inputs: {len(buildings)} buildings ({int(buildings.informal.sum())} in favela areas), {len(edges)} edges")

    # ---------------- terrain
    hs = [bld.building_height(r, bool(r["informal"]), bcfg) for _, r in buildings.iterrows()]
    buildings["height_m"], buildings["height_src"] = [h for h, _ in hs], [s for _, s in hs]
    margin = 1000.0
    dsm, dsm_tf = ter.load_dsm_window(PROCESSED_DIR / "dem" / "full_10m.tif", bbox, margin=margin)
    # The ground model needs buildings for the whole DEM window, not just the AOI; otherwise rooftops just
    # outside the AOI leak in through the blur and the lowland opening. Prefer the full-region extract.
    full_path = RAW_DIR / "osm" / "full" / "buildings.gpkg"
    if args.aoi != "full" and full_path.exists():
        ctx = gpd.read_file(full_path, bbox=(W - margin, S - margin, E + margin, N + margin))
        ctx["informal"] = ctx.geometry.centroid.within(favela_union) if favela_union is not None else False
        ctx_h = [bld.building_height(r, bool(r["informal"]), bcfg)[0] for _, r in ctx.iterrows()]
        log(f"ground model uses {len(ctx)} full-region buildings within the DEM window")
        ground10, _ = ter.ground_from_dsm(dsm, dsm_tf, ctx.geometry, ctx_h)
    else:
        log("WARNING: full-region buildings not available; ground model limited to AOI buildings")
        ground10, _ = ter.ground_from_dsm(dsm, dsm_tf, buildings.geometry, buildings["height_m"])
    ground = ter.resample_to_grid(ground10, dsm_tf, bbox, step)
    dsm_grid = ter.resample_to_grid(dsm, dsm_tf, bbox, step)
    log(f"terrain grid {ground.shape[1]}x{ground.shape[0]} @ {step} m; "
        f"ground {ground.z.min():.1f}..{ground.z.max():.1f} m")

    # ---------------- inland water (lagoons, lakes, canals). Carved before the roads so roads crossing a canal
    # without a bridge tag still win; bridges are not carved, so the water stays visible under them.
    wcfg = CONFIG["water"]
    water_bodies, shore_cap = [], None
    if (osm / "reference.gpkg").exists():
        for _, r in ter.select_water_bodies(ref, bbox, wcfg).iterrows():
            level, src = ter.water_level(r.geometry, dsm, dsm_tf, r.get("name"), wcfg)
            water_bodies.append({"name": None if str(r.get("name")) == "nan" else r.get("name"),
                                 "kind": str(r.get("water")) if str(r.get("water")) != "nan" else "water",
                                 "geom": r.geometry, "level_m": round(level, 2), "level_source": src,
                                 "area_m2": round(r.geometry.area)})
        # Narrow bodies (canals) give a DSM level contaminated by their banks (Jardim de Alah canal: 2.44 m next to
        # the 1.0 m lagoon it drains). If one touches a larger body, it takes that body's level.
        for b in sorted(water_bodies, key=lambda b: -b["area_m2"]):
            if b["level_source"] == "config":
                continue
            narrow = b["geom"].buffer(-wcfg["dsm_inset_m"]).area < 0.3 * b["area_m2"]
            bigger = [o for o in water_bodies if o["area_m2"] > b["area_m2"]
                      and b["geom"].distance(o["geom"]) <= wcfg["connect_m"]]
            if narrow and bigger:
                o = max(bigger, key=lambda o: o["area_m2"])
                b["level_m"], b["level_source"] = o["level_m"], f"connected:{o['name'] or o['kind']}"
        ccfg = wcfg["shore_cap"]
        beaches = ref[ref["natural"].astype(str).eq("beach") & (ref.area >= ccfg["beach_min_area_m2"])]
        anchors = [(b["geom"], b["level_m"]) for b in water_bodies] + \
                  [(g, ccfg["beach_level_m"]) for g in beaches.geometry]
        lowered = ter.cap_flat_shores(ground, anchors, ccfg)
        shore_cap = {"anchors": len(anchors), "beaches": sorted(set(beaches["name"].dropna())),
                     "cells_lowered_over_1m": int((lowered > 1.0).sum()), "max_lowered_m": round(float(lowered.max()), 2)}
        log(f"flat-shore cap: {len(anchors)} anchors; lowered {shore_cap['cells_lowered_over_1m']} cells by > 1 m "
            f"(max {shore_cap['max_lowered_m']} m)")
        wet = ter.carve_water(ground, [(b["geom"], b["level_m"]) for b in water_bodies], wcfg)
        log(f"inland water: {len(water_bodies)} bodies, {wet.mean():.1%} of the AOI; "
            + ", ".join(f"{b['name'] or b['kind']} @ {b['level_m']} m ({b['level_source']})"
                        for b in sorted(water_bodies, key=lambda b: -b["area_m2"])[:5]))

    # ---------------- roads
    surf, tunnels = rd.prepare_edges(edges, bbox, rcfg)
    nodes_xy = {r["osmid"]: (r.geometry.x, r.geometry.y) for _, r in nodes.iterrows()}
    # Tiered vertical alignment: each tier follows the terrain already carved by the tiers above it, so side
    # streets meet avenues at the avenue's height and parallel cycleways/footways sit on the same platform.
    profiles, fixed, terrain = [None] * len(surf), {}, ground
    for tier in rd.SOLVE_TIERS:
        mask = surf["group"].isin(tier).to_numpy()
        tier_profiles, node_z = rd.solve_profiles(surf[mask], nodes_xy, terrain.sample, fixed, rcfg)
        for i, prof in zip(np.flatnonzero(mask), tier_profiles):
            profiles[i] = prof
        fixed.update({k: v for k, v in node_z.items() if k not in fixed})
        done = [i for i, prof in enumerate(profiles) if prof is not None]
        samples = rd.corridor_samples(surf.iloc[done], [profiles[i] for i in done], rcfg)
        terrain = ter.Heightfield(ground.west, ground.south, step, ter.carve_roads(ground, samples)[0])
        log(f"  tier {'/'.join(tier)}: {int(mask.sum())} edges solved")
    log(f"roads: {len(surf)} surface edges, {len(tunnels)} tunnel edges skipped, {len(samples)} corridor samples")

    # ---------------- verification metrics
    grades = []
    for (_, r), prof in zip(surf.iterrows(), profiles):
        grades.append((rd.max_grade(prof), r["cls"], str(r.get("name")), r.geometry.length))
    drivable_grades = [g for g in grades if rd.CLASS_INFO[g[1]][1] in ("major", "minor") and g[3] > 20]
    steep = sorted(drivable_grades, reverse=True)[:8]
    # Road surface vs terrain: the road must sit just above the carved terrain along every centreline.
    gaps, worst_gaps = [], []
    for (_, r), prof in zip(surf.iterrows(), profiles):
        if not r["is_bridge"]:
            g = prof[:, 2] + rcfg["surface_offset_m"] - terrain.sample(prof[:, 0], prof[:, 1])
            gaps.append(g)
            k = int(np.argmax(np.abs(g)))
            worst_gaps.append((float(g[k]), r["cls"], str(r.get("name")), [round(v) for v in prof[k, :2]]))
    gaps = np.concatenate(gaps)
    worst_gaps = sorted(worst_gaps, key=lambda w: -abs(w[0]))[:8]

    # ---------------- meshes per tile
    def tile_of(e, n):
        return (int(np.clip((e - W) // tile_m, 0, ntx - 1)), int(np.clip((n - S) // tile_m, 0, nty - 1)))

    tiles = {(i, j): {"Terrain": MeshBuilder(), "Roads": MeshBuilder(), "Buildings": MeshBuilder(),
                      "Water": MeshBuilder()}
             for i in range(ntx) for j in range(nty)}
    for i in range(ntx):
        for j in range(nty):
            pe, pn = W + i * tile_m, S + j * tile_m
            v, t, nrm = ter.grid_mesh(terrain.z, terrain.west, terrain.south, step, i * per_tile,
                                      (i + 1) * per_tile, j * per_tile, (j + 1) * per_tile, pe, pn)
            tiles[(i, j)]["Terrain"].add("M_Terrain", v, t, nrm)
            cell = box(pe, pn, pe + tile_m, pn + tile_m)
            for b in water_bodies:
                piece = b["geom"].intersection(cell)
                if piece.is_empty:
                    continue
                v, t = ter.water_surface_triangles(piece, b["level_m"], (pe, pn))
                tiles[(i, j)]["Water"].add("M_Sea", v, t, [(0.0, 0.0, 1.0)] * len(v))

    for (_, r), prof in zip(surf.iterrows(), profiles):
        lift = rcfg["surface_offset_m"] + r["rank"] * 0.004
        for key, run in rd.split_by_tile(prof, tile_of):
            pe, pn = W + key[0] * tile_m, S + key[1] * tile_m
            local = run - [pe, pn, 0.0]
            v, t, nrm = rd.ribbon(local, r["width_m"] / 2, lift)
            tiles[key]["Roads"].add(rd.MATERIAL[r["group"]], v, t, nrm)

    # Building/road conflicts: footprints overlapping a drivable carriageway indicate a width or data error.
    drivable = surf[surf["group"].isin(["major", "minor"]) & ~surf["is_bridge"]]
    carriageways = [g.buffer(w / 2, cap_style="flat") for g, w in zip(drivable.geometry, drivable["width_m"])]
    tree = STRtree(carriageways)
    height_sources, conflicts, placed = {}, 0, 0
    for idx, r in buildings.iterrows():
        h, src = r["height_m"], r["height_src"]
        for poly in bld.footprint_polygons(r.geometry):
            if not box(*bbox).contains(poly.centroid):
                continue
            hits = tree.query(poly, predicate="intersects")
            if len(hits) and max(carriageways[k].intersection(poly).area for k in hits) > 0.25 * poly.area:
                conflicts += 1
            ring = np.asarray(poly.exterior.coords)
            zg = terrain.sample(ring[:, 0], ring[:, 1])
            key = tile_of(poly.centroid.x, poly.centroid.y)
            pivot = (W + key[0] * tile_m, S + key[1] * tile_m)
            mesh = bld.extrude(poly, zg.min() - bcfg["foundation_sink_m"], zg.max() + h, pivot)
            if mesh is None:
                continue
            tiles[key]["Buildings"].add("M_BuildingInformal" if r["informal"] else "M_BuildingFormal", *mesh)
            height_sources[src] = height_sources.get(src, 0) + 1
            placed += 1
    log(f"buildings placed: {placed}; carriageway conflicts: {conflicts}")

    # ---------------- write
    write_mtl(out / "blockout.mtl", PALETTE)
    # One triangle per material: imported once with materials into /Game/Rio/Materials/Blockout, so every
    # tile mesh (imported without materials) can share the same material assets.
    palette = MeshBuilder()
    for k, name in enumerate(PALETTE):
        palette.add(name, [(k, 0, 0), (k + 1, 0, 0), (k, 1, 0)], [(0, 1, 2)], [(0, 0, 1)] * 3)
    palette.write_obj(out / "SM_BlockoutPalette.obj", "blockout.mtl")
    area_tag = f"RioArea:{args.aoi}"
    manifest = {"aoi": args.aoi, "crs": PROJECTED_CRS, "unreal_origin": CONFIG["unreal"], "tags": [GEN_TAG, area_tag],
                "asset_root": f"/Game/Rio/Generated/{aoi_cfg.get('unreal_folder', 'Test_LeblonVidigal')}",
                "outliner_root": f"Generated/{aoi_cfg.get('unreal_folder', 'Test_LeblonVidigal')}",
                "always_loaded": bool(aoi_cfg.get("always_loaded", False)), "actors": []}
    for (i, j), kinds in tiles.items():
        pe, pn = W + i * tile_m, S + j * tile_m
        x, y, z = utm_to_ue(pe, pn, 0.0)
        for kind, mb in kinds.items():
            if not mb.triangle_count:
                continue
            name = f"SM_T{i}{j}_{kind}"
            mb.write_obj(out / "tiles" / f"{name}.obj", None)
            manifest["actors"].append({
                "asset": name, "kind": kind, "obj": str(out / "tiles" / f"{name}.obj"),
                "label": f"GEN_T{i}{j}_{kind}", "location_cm": [x, y, z], "tile": [i, j],
                "pivot_utm": [pe, pn], "materials": sorted(mb.parts), "triangles": mb.triangle_count,
                "collision": "none" if kind == "Water" else "complex_as_simple"})
    sea = MeshBuilder()
    pad = 3000.0  # extend the sea beyond the AOI so test levels do not end in a void
    sea.add("M_Sea", [(-pad, -pad, 0), (E - W + pad, -pad, 0), (E - W + pad, N - S + pad, 0), (-pad, N - S + pad, 0)],
            [(0, 1, 2), (0, 2, 3)], [(0, 0, 1)] * 4)
    sea.write_obj(out / "SM_Sea.obj", None)
    x, y, z = utm_to_ue(W, S, 0.0)
    manifest["actors"].append({"asset": "SM_Sea", "kind": "Water", "obj": str(out / "SM_Sea.obj"),
                               "label": "GEN_Sea", "location_cm": [x, y, z], "materials": ["M_Sea"],
                               "triangles": 2, "collision": "none"})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # The MCP import script can only read files under Saved/ or Content/, so mirror the manifest there.
    saved = PROJECT_DIR / "Saved" / "RioPipeline" / f"{args.aoi}_manifest.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(manifest, indent=2))

    graph = {"crs": PROJECTED_CRS, "frame": "UTM metres; z = metres above sea level", "edges": []}
    for (_, r), prof in zip(surf.iterrows(), profiles):
        graph["edges"].append({"u": int(r["u"]), "v": int(r["v"]), "osmid": str(r["osmid"]), "class": r["cls"],
                               "name": None if str(r.get("name")) == "nan" else r.get("name"),
                               "width_m": round(r["width_m"], 2), "oneway": str(r.get("oneway")),
                               "bridge": bool(r["is_bridge"]), "points": np.round(prof, 2).tolist()})
    graph["tunnels_not_built"] = sorted({str(n) for n in tunnels.get("name", []) if str(n) != "nan"})
    (out / "road_graph_source.json").write_text(json.dumps(graph))

    # DSM-vs-ground difference over buildings shows how much the ground correction removed.
    removed = dsm_grid.z - ground.z
    report = {
        "aoi": args.aoi, "tiles": [ntx, nty], "grid_m": step,
        "terrain_range_m": [float(terrain.z.min()), float(terrain.z.max())],
        "ground_correction_m": {"mean": float(removed.mean()), "p95": float(np.percentile(removed, 95)),
                                "max": float(removed.max())},
        "road_surface_minus_terrain_m": {"min": float(gaps.min()), "median": float(np.median(gaps)),
                                         "max": float(gaps.max()),
                                         "within_0_5m": float(np.mean(np.abs(gaps) <= 0.5)),
                                         "worst": worst_gaps},
        "steepest_drivable": [{"grade": round(g, 3), "class": c, "name": n, "length_m": round(L)}
                              for g, c, n, L in steep],
        "tunnels_skipped": graph["tunnels_not_built"],
        "buildings": {"placed": placed, "height_sources": height_sources, "carriageway_conflicts": conflicts},
        "shore_cap": shore_cap,
        "water_bodies": [{k: b[k] for k in ("name", "kind", "level_m", "level_source", "area_m2")}
                         for b in water_bodies],
        "triangles": {a["asset"]: a["triangles"] for a in manifest["actors"]},
    }
    write_metadata(out / "report.json", **report)
    log(f"wrote {len(manifest['actors'])} meshes, "
        f"{sum(a['triangles'] for a in manifest['actors']) / 1e6:.2f} M triangles -> {out}")
    print(json.dumps({k: report[k] for k in ("terrain_range_m", "ground_correction_m",
                                             "road_surface_minus_terrain_m", "steepest_drivable", "buildings")},
                     indent=1))


if __name__ == "__main__":
    main()
