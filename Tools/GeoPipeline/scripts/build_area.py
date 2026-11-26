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
import pyogrio
import shapely
import numpy as np
import pandas as pd
from shapely.geometry import Point, box
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo import buildings as bld  # noqa: E402
from rio_geo import details as dt  # noqa: E402
from rio_geo import favela as fav  # noqa: E402
from rio_geo import places as plc  # noqa: E402
from rio_geo.beach import box as box_mesh  # noqa: E402
from rio_geo import roads as rd  # noqa: E402
from rio_geo import stairs as st  # noqa: E402
from rio_geo import escadarias as esc  # noqa: E402
from rio_geo import turnarounds as tna  # noqa: E402
from rio_geo import terrain as ter  # noqa: E402
from rio_geo.common import (CONFIG, EXPORTS_DIR, PROCESSED_DIR, PROJECT_DIR, PROJECTED_CRS, RAW_DIR,  # noqa: E402
                            aoi_bbox_utm, stable_unit, utm_to_ue, write_metadata)
from rio_geo.meshio import MeshBuilder, write_mtl  # noqa: E402

PALETTE = {
    "M_Terrain": (0.30, 0.42, 0.22), "M_RoadMajor": (0.06, 0.06, 0.07), "M_RoadMinor": (0.16, 0.16, 0.17),
    "M_Path": (0.62, 0.55, 0.42), "M_Steps": (0.80, 0.45, 0.15), "M_BuildingFormal": (0.72, 0.72, 0.70),
    "M_BuildingInformal": (0.78, 0.40, 0.25), "M_Sea": (0.05, 0.20, 0.40), "M_WaterTank": (0.10, 0.35, 0.70),
    # favela facades (rio_geo/favela.py house_style): cement render and painted houses
    "M_HouseRender": (0.62, 0.61, 0.58), "M_HousePaintWhite": (0.88, 0.86, 0.80),
    "M_HousePaintYellow": (0.88, 0.76, 0.44), "M_HousePaintBlue": (0.48, 0.62, 0.76),
    "M_HousePaintGreen": (0.56, 0.68, 0.50), "M_HousePaintPink": (0.84, 0.60, 0.62),
    "M_HousePaintTeal": (0.34, 0.60, 0.58),
    # formal-city facades (rio_geo/buildings.py formal_style)
    "M_FacadeWhite": (0.90, 0.89, 0.86), "M_FacadeCream": (0.88, 0.83, 0.70), "M_FacadeBeige": (0.78, 0.71, 0.60),
    "M_FacadeGrey": (0.66, 0.67, 0.68), "M_FacadeConcrete": (0.56, 0.55, 0.52), "M_FacadeSalmon": (0.86, 0.66, 0.58),
    "M_FacadeBlue": (0.64, 0.74, 0.80), "M_GroundFloor": (0.24, 0.25, 0.27), "M_RoofConcrete": (0.50, 0.49, 0.47),
    # landmark complexes (config headlands): Forte de Copacabana fortress and museum buildings
    "M_FortConcrete": (0.58, 0.58, 0.55), "M_FortColonial": (0.92, 0.84, 0.62),
}
GEN_TAG = "RioGenerated"


def bbox_box_mesh(centre, size, z0, z1):
    """Axis-aligned closed-top box (no bottom), CCW outward faces. Returns verts, tris, normals."""
    v, t = box_mesh(np.asarray(centre), 0.0, (size, size), z0, z1)
    return v, t, None


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
    lanes_cfg = rcfg.get("favela_lanes", {"enabled": False})
    if lanes_cfg["enabled"] and favela_union is not None:  # narrow car access: one-car lanes inside the favela
        hubs_keep = shapely.union_all([Point(h["center"]).buffer(lanes_cfg["hub_keep_radius_m"])
                                       for h in CONFIG["places"]["hubs"]])
        edges, narrowed = rd.narrow_in_zones(edges, favela_union, lanes_cfg, hubs_keep)
        if narrowed:
            log("favela lanes narrowed: " + ", ".join(f"{c} {L / 1000:.1f} km" for c, L in sorted(narrowed.items())))
    ecfg = CONFIG.get("escadarias", {"enabled": False})
    esc_routes = []
    if ecfg["enabled"]:  # signature stairs: retag their ways as wide stairs before infill and the road solve
        edges, esc_routes = esc.resolve_routes(edges, ecfg)
        for rt in esc_routes:
            log(f"escadaria {rt['name']}: " + (f"skipped (not in this AOI: {rt['missing']})" if "missing" in rt else
                f"{rt['length_m']} m over {len(rt['edges'])} ways ({', '.join(f'{c} {v} m' for c, v in rt['classes'].items())})"))
        esc_routes = [rt for rt in esc_routes if "missing" not in rt]

    # ---------------- terrain
    hs = [bld.building_height(r, bool(r["informal"]), bcfg) for _, r in buildings.iterrows()]
    buildings["height_m"], buildings["height_src"] = [h for h, _ in hs], [s for _, s in hs]
    # landmark complexes (rio_geo/fort.py builds the rest): fixed heights / materials for their buildings
    hcfg = CONFIG.get("headlands", {})
    lm_count = 0
    if "material" not in buildings:
        buildings["material"] = None
    for lm in hcfg.get("landmark_buildings", []):
        hit = buildings["id"].astype(str).eq(str(lm["osm_id"])) if "id" in buildings else []
        if len(hit) and hit.any():
            buildings.loc[hit, ["height_m", "height_src", "material"]] = [lm["height_m"], "landmark", lm["material"]]
            lm_count += int(hit.sum())
    for zone in hcfg.get("landmark_zones", []):
        path = osm / f"{zone['set']}.gpkg"
        if not path.exists():
            continue
        za = gpd.read_file(path, layer="areas")
        sel = za[za["name"].astype(str).eq(zone["name"]) & za[zone["key"]].astype(str).eq(zone["value"])]
        if not len(sel):
            continue
        zpoly = sel.geometry.union_all()
        inz = buildings.geometry.centroid.within(zpoly) & buildings["height_src"].ne("landmark")
        for i in np.flatnonzero(inz.to_numpy()):
            lo, hi = zone["levels"]
            lv = lo + int(stable_unit(("zone", str(buildings["id"].iat[i]) if "id" in buildings else i)) * (hi - lo + 1))
            buildings.iat[i, buildings.columns.get_loc("height_m")] = lv * bcfg["storey_m"] + 0.5
            buildings.iat[i, buildings.columns.get_loc("height_src")] = "landmark_zone"
            buildings.iat[i, buildings.columns.get_loc("material")] = zone["material"]
            lm_count += 1
    if lm_count:
        log(f"landmark buildings: {lm_count} (fixed height / material)")
    margin = 1000.0
    dsm, dsm_tf = ter.load_dsm_window(PROCESSED_DIR / "dem" / "full_10m.tif", bbox, margin=margin)

    # ---------------- informal housing infill (under-mapped favelas). Before the ground model: the DSM contains
    # the real unmapped houses, so their footprints must be subtracted like mapped buildings.
    fcfg = CONFIG["favela_infill"]
    infill_report, infill = [], gpd.GeoDataFrame(geometry=[], crs=PROJECTED_CRS)
    buildings["water_tank"] = False
    buildings["parapet"], buildings["roof_room"] = False, False  # "material" keeps landmark overrides (above)
    buildings["frontage"] = ""
    if fcfg["enabled"] and fcfg.get("style_mapped_informal"):  # mapped favela houses get the same looks
        for i in np.flatnonzero(buildings["informal"].to_numpy()):
            sty = fav.house_style(("osm", str(buildings.at[i, "osmid"]) if "osmid" in buildings else i), fcfg)
            for k in ("material", "parapet", "roof_room", "water_tank"):
                buildings.at[i, k] = sty[k]
    if fcfg["enabled"] and len(favelas):
        circ, _ = rd.prepare_edges(edges, bbox, rcfg)
        zones = fav.infill_zones(favelas, CONFIG["places"]["hubs"], bbox)
        in_zone = buildings.geometry.centroid.within(shapely.union_all([z[1] for z in zones]))
        houses, infill_report = fav.generate_infill(zones, buildings[buildings["informal"] | in_zone],
                                                    circ, dsm, dsm_tf, bbox, fcfg)
        if houses:
            infill = gpd.GeoDataFrame(houses, crs=PROJECTED_CRS)
            infill["height_m"] = infill["levels"] * bcfg["informal_storey_m"] + 0.5
            infill["height"] = infill["height_m"]  # so building_height() returns it for the ground model
            infill["informal"], infill["height_src"] = True, "infill"
            infill["osmid"] = "infill:" + infill["lot"]
            buildings = gpd.GeoDataFrame(pd.concat([buildings, infill[["geometry", "height", "height_m", "informal",
                                                                       "height_src", "osmid", "water_tank", "material",
                                                                       "parapet", "roof_room", "frontage"]]],
                                                   ignore_index=True), crs=PROJECTED_CRS)
        log("favela infill: " + "; ".join(f"{r['favela']}: +{r['added']} houses, coverage "
                                          f"{r['mapped_coverage']:.0%} -> {r.get('coverage_after', r['mapped_coverage']):.0%}"
                                          for r in infill_report))
    # The ground model needs buildings for the whole DEM window, not just the AOI; otherwise rooftops just
    # outside the AOI leak in through the blur and the lowland opening. Prefer the full-region extract.
    # ---------------- places: pitch / hub terraces and designed hub buildings (rio_geo/places.py). Houses inside
    # them are removed from the meshes (the DSM still contains the real ones, so the ground model keeps them).
    pcfg = CONFIG["places"]
    pitch_areas = []
    for fs in CONFIG["feature_sets"]:
        path = osm / f"{fs}.gpkg"
        if path.exists() and "areas" in [lyr for lyr, _ in pyogrio.list_layers(path)]:
            pitch_areas.append(gpd.read_file(path, layer="areas"))
    pitch_areas = gpd.GeoDataFrame(pd.concat(pitch_areas, ignore_index=True), crs=PROJECTED_CRS) if pitch_areas else None
    terr = plc.terraces(pcfg, pitch_areas, bbox)
    elems = plc.hub_elements(pcfg, bbox)
    hub_stairs = plc.hub_stairs(pcfg, bbox)
    if terr or elems or hub_stairs:
        zone = plc.clear_zones(terr, elems, pcfg, hub_stairs)
        hit = buildings.geometry.intersects(zone)
        log(f"places: {len(terr)} terraces ({sum(t['kind'] == 'pitch' for t in terr)} pitches), {len(elems)} hub "
            f"elements; removed {int(hit.sum())} houses from them")
        buildings = buildings[~hit].reset_index(drop=True)

    # Coastal headlands (natural cover, military land, forts) keep their relief: no lowland opening, no shore caps.
    headland_polys = []
    if (osm / "reference.gpkg").exists():
        cover = set(hcfg.get("protect_natural", []))
        cand = list(ref[ref["natural"].astype(str).isin(cover)].geometry)
        if pitch_areas is not None:
            pa = pitch_areas
            for key, vals in hcfg.get("protect_tags", {}).items():
                if key in pa:
                    cand += list(pa[pa[key].astype(str).isin(vals)].geometry)
        coast = gpd.read_file(osm / "reference.gpkg", layer="lines")
        coast = coast[coast["natural"].astype(str).eq("coastline")].geometry.union_all() if len(coast) else None
        if coast is not None and not coast.is_empty:
            reach = coast.buffer(hcfg.get("protect_coast_m", 250.0))
            headland_polys = [g for g in cand if g.intersects(reach)]
        log(f"headlands: {len(headland_polys)} coastal polygons kept out of the lowland opening and shore caps")
    full_path = RAW_DIR / "osm" / "full" / "buildings.gpkg"
    if args.aoi != "full" and full_path.exists():
        ctx = gpd.read_file(full_path, bbox=(W - margin, S - margin, E + margin, N + margin))
        if len(infill):
            ctx = gpd.GeoDataFrame(pd.concat([ctx, infill[["geometry", "height"]]], ignore_index=True), crs=PROJECTED_CRS)
        ctx["informal"] = ctx.geometry.centroid.within(favela_union) if favela_union is not None else False
        ctx_h = [bld.building_height(r, bool(r["informal"]), bcfg)[0] for _, r in ctx.iterrows()]
        log(f"ground model uses {len(ctx)} full-region buildings within the DEM window")
        ground10, _ = ter.ground_from_dsm(dsm, dsm_tf, ctx.geometry, ctx_h, protect=headland_polys)
    else:
        log("WARNING: full-region buildings not available; ground model limited to AOI buildings")
        ground10, _ = ter.ground_from_dsm(dsm, dsm_tf, buildings.geometry, buildings["height_m"], protect=headland_polys)
    peaks_report = []
    full_ref = RAW_DIR / "osm" / "full" / "reference.gpkg"
    if full_ref.exists():  # peaks from the full extract: a summit just outside the AOI still shapes its slopes
        pts = gpd.read_file(full_ref, layer="points")
        radii = CONFIG["landmark_peaks"]["radius_m"]
        sel = pts[pts["name"].isin(radii) & pts["natural"].astype(str).eq("peak")]
        peaks = [(r["name"], r.geometry.x, r.geometry.y, float(r["ele"]), radii[r["name"]])
                 for _, r in sel.iterrows() if str(r.get("ele")) not in ("nan", "None")]
        peaks.sort(key=lambda p: -p[3])  # tallest first, so a smaller neighbour is corrected on top of its tail
        peaks_report = ter.raise_landmark_peaks(ground10, dsm_tf, peaks)
        raised = [f"{p['name']} +{p['raised_m']} m" for p in peaks_report if p.get("raised_m")]
        if raised:
            log("landmark peaks raised to surveyed height: " + ", ".join(raised))
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
                  [(g, ccfg["beach_level_m"], "beach") for g in beaches.geometry]
        headland_mask = ter.polygon_mask(ground, headland_polys)
        lowered = ter.cap_flat_shores(ground, anchors, ccfg, protect_mask=headland_mask)
        shore_cap = {"anchors": len(anchors), "beaches": sorted(set(beaches["name"].dropna())),
                     "cells_lowered_over_1m": int((lowered > 1.0).sum()), "max_lowered_m": round(float(lowered.max()), 2)}
        log(f"flat-shore cap: {len(anchors)} anchors; lowered {shore_cap['cells_lowered_over_1m']} cells by > 1 m "
            f"(max {shore_cap['max_lowered_m']} m)")
        ff = CONFIG["roads"].get("flat_fronts") or {}
        if ff.get("ground_names"):
            names = edges["name"].map(rd._first) if "name" in edges else None
            front = edges[names.isin(ff["ground_names"])] if names is not None else edges.iloc[:0]
            nat = ref[ref["natural"].astype(str).isin(["wood", "scrub", "bare_rock", "grassland", "heath", "cliff"])
                      | ref["landuse"].astype(str).isin(["forest", "meadow"])].geometry
            protect = list(nat) + headland_polys + ([favela_union] if favela_union is not None else [])
            low = ter.cap_front_roads(ground, list(front.geometry), ff,
                                      footprints=list(buildings.loc[~buildings["informal"]].geometry), protect=protect)
            shore_cap["front_roads_lowered_over_1m"] = int((low > 1.0).sum())
            xs, ys = ground.coords()
            big = np.argwhere(low > 8.0)
            shore_cap["front_roads_lowered_over_8m"] = int(len(big))
            if len(big):  # where the cap cut most: check these are rooftop residue, not real hills
                order = np.argsort(-low[big[:, 0], big[:, 1]])[:2000:100]
                shore_cap["front_roads_largest_cuts"] = [
                    {"utm": [round(float(xs[c]), 1), round(float(ys[r]), 1)], "lowered_m": round(float(low[r, c]), 1),
                     "z_after": round(float(ground.z[r, c]), 1)} for r, c in big[order]]
            log(f"beachfront ground cap: {len(front)} edges; lowered {int((low > 1.0).sum())} cells by > 1 m "
                f"(max {low.max():.1f} m)")
        pts = gpd.read_file(osm / "reference.gpkg", layer="points")
        for hd in hcfg.get("rebuild", []):
            rock = ref[ref["name"].astype(str).eq(hd["polygon_name"])]
            peak = pts[pts["name"].astype(str).eq(hd["peak"])]
            if not len(rock) or not len(peak):
                log(f"headland {hd['polygon_name']}: not in this AOI")
                continue
            poly = rock.geometry.union_all()
            near = ref[ref["natural"].astype(str).eq("bare_rock") & ref.geometry.intersects(poly.buffer(hd["merge_m"]))]
            poly = shapely.union_all([poly, *near.geometry])
            top = ter.rebuild_headland(ground, poly, (peak.geometry.x.iat[0], peak.geometry.y.iat[0]), hd["ele_m"], hd)
            log(f"headland {hd['polygon_name']}: rebuilt to {top:.1f} m (peak {hd['peak']} {hd['ele_m']} m), "
                f"{poly.area:.0f} m2")
        wet = ter.carve_water(ground, [(b["geom"], b["level_m"]) for b in water_bodies], wcfg)
        log(f"inland water: {len(water_bodies)} bodies, {wet.mean():.1%} of the AOI; "
            + ", ".join(f"{b['name'] or b['kind']} @ {b['level_m']} m ({b['level_source']})"
                        for b in sorted(water_bodies, key=lambda b: -b["area_m2"])[:5]))

    if terr:
        # streets win over terraces: an OSM pitch apron overlapping a carriageway at another level would put the
        # flat slab (and, after re-flattening, the ground) over the street
        drv, _ = rd.prepare_edges(edges, bbox, rcfg)
        drv = drv[drv["group"].isin(["major", "minor"])]
        trimmed = plc.trim_terraces(terr, shapely.union_all(
            [g.buffer(w / 2 + 0.5, cap_style="flat") for g, w in zip(drv.geometry, drv["width_m"])]))
        if trimmed:
            log(f"places: trimmed street corridors out of {len(trimmed)} terraces ({', '.join(trimmed[:4])})")
        plc.apply_terraces(ground, terr, pcfg["pitch_terraces"]["min_z_m"])
        (out / "terraces.json").write_text(json.dumps(
            [{k: (v.wkt if hasattr(v, "wkt") else v) for k, v in t.items()} for t in terr], indent=1))

    # ---------------- roads
    surf, tunnels = rd.prepare_edges(edges, bbox, rcfg)
    if terr:
        block = plc.hub_path_block(terr)
        cut = surf["group"].isin(["path", "steps"]) & surf.geometry.intersects(block)
        if cut.any():
            log(f"places: dropped {int(cut.sum())} beco / stair edges crossing hub terraces")
            surf = surf[~cut].reset_index(drop=True)
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

    if terr:
        keep = shapely.union_all([g.buffer(w / 2 + 0.5, cap_style="flat") for g, w in zip(surf.geometry, surf["width_m"])])
        fix, fix_name = plc.reflatten_terraces(terrain, terr, keep)
        log(f"places: terraces re-flattened after the road carve (largest correction {fix:.1f} m, {fix_name})")

    # ---------------- designed stairs (hub): end heights from terrace / road surface / terrain, ramp under them
    if hub_stairs:
        lines = list(surf.geometry)
        road_tree = STRtree(lines)

        def road_z(x, y):
            p = Point(x, y)
            best = None
            for k in road_tree.query(p.buffer(12.0)):
                r = surf.iloc[k]
                d = lines[k].distance(p)
                if d <= r["width_m"] / 2 + 0.3 and (best is None or d < best[0]):
                    prof = profiles[k]
                    j = int(np.argmin(np.hypot(prof[:, 0] - x, prof[:, 1] - y)))
                    best = (d, float(prof[j, 2]) + rcfg["surface_offset_m"])
            return None if best is None else best[1]

        plc.resolve_stairs(hub_stairs, terr, terrain, road_z, pcfg)
        (out / "stairs.json").write_text(json.dumps(
            [{"name": s["name"], "hub": s["hub"], "width": s["width"], "rise": s["rise"], "steps": s["steps"],
              "ends": s["ends"], "min_run_m": s["min_run_m"], "profile": np.round(s["profile"], 3).tolist()}
             for s in hub_stairs], indent=1))
        log("hub stairs: " + "; ".join(f"{s['name'].split(': ')[-1]} {s['ends'][0]['z']:.1f}->{s['ends'][1]['z']:.1f} m, "
                                       f"{s['steps']} steps of {s['rise'] * 100:.0f} cm, run {s['min_run_m']} m"
                                       for s in hub_stairs))
        steep = [s["name"].split(": ")[-1] for s in hub_stairs if s["min_run_m"] < 0.22]
        if steep:
            log(f"WARNING: hub stairs too steep to walk (run < 22 cm per step): {steep}. The hub layout is designed "
                "for test_vidigal's terrain; check the terrace levels in this AOI.")
    elif (out / "stairs.json").exists():
        (out / "stairs.json").unlink()

    # ---------------- turning space at the dead ends of narrow favela lanes (rio_geo/turnarounds.py)
    tcfg = rcfg.get("turnarounds", {"enabled": False})
    pads = []
    if tcfg["enabled"] and favela_union is not None:
        cands = tna.dead_ends(surf, profiles, nodes_xy, bbox, favela_union.buffer(tcfg["zone_buffer_m"]), tcfg)
        tblock = [t["polygon"].buffer(0.5) for t in terr] + [e["polygon"].buffer(1.0) for e in elems] + \
                 [s_["corridor"].buffer(0.8) for s_ in hub_stairs]
        if "escadaria" in surf:
            tblock += [g.buffer(w / 2 + 0.5, cap_style="flat") for g, w, e in
                       zip(surf.geometry, surf["width_m"], surf["escadaria"]) if isinstance(e, str)]
        pads, gone, tstats = tna.site_pads(cands, surf, profiles, buildings,
                                           shapely.union_all(tblock) if tblock else None, tcfg)
        buildings = buildings.drop(index=buildings.index[gone]).reset_index(drop=True)
        protect = shapely.union_all([t["polygon"] for t in terr]) if terr else None
        for pad in pads:
            tna.apply_pad(terrain, pad, rcfg["terrain_sink_m"], tcfg["blend_m"], protect)
        log(f"turnarounds: {tstats}; radii " + ", ".join(f"{p_['radius']:.1f}" for p_ in pads))
    (out / "turnarounds.json").write_text(json.dumps(
        [{"edge_osmid": str(surf.iloc[p_["edge"]]["osmid"]), "node": int(p_["node"]), "radius": p_["radius"],
          "center": np.round(p_["center"], 3).tolist(), "z": round(float(p_["end"][2]), 3),
          "grade": round(p_["grade"], 4), "polygon": p_["polygon"].wkt} for p_ in pads], indent=1))

    # ---------------- signature escadarias: clear the corridor, paint the facades, landings; meshes by build_details
    esc_out = []
    if esc_routes:
        is_esc = surf["escadaria"].map(lambda v: isinstance(v, str)).to_numpy() if "escadaria" in surf else \
            np.zeros(len(surf), bool)
        for rt in esc_routes:
            rows = [i for i in np.flatnonzero(is_esc) if surf.iloc[i]["escadaria"] == rt["name"]]
            if not rows:
                continue
            order = {n: k for k, n in enumerate(rt["nodes"])}
            rows.sort(key=lambda i: min(order.get(surf.iloc[i]["u"], 1e9), order.get(surf.iloc[i]["v"], 1e9)))
            width = float(surf.iloc[rows[0]]["width_m"])
            corridor = shapely.union_all([surf.geometry.iloc[i].buffer(width / 2 + ecfg["clear_buildings_m"],
                                                                         cap_style="flat") for i in rows])
            hit = buildings.geometry.intersects(corridor).to_numpy()
            buildings = buildings[~hit].reset_index(drop=True)
            painted = esc.paint_houses(buildings, corridor, ecfg["paint_houses"], key=rt["name"])
            eds, nland = [], 0
            for i in rows:
                r = surf.iloc[i]
                lift = rcfg["surface_offset_m"] + r["rank"] * 0.004
                prof = esc.oriented(profiles[i] + [0.0, 0.0, lift], r["u"], r["v"], rt)
                pl, lands = esc.with_landings(prof, ecfg["landings"])
                nland += len(lands)
                # ground under the treads (and just beyond their edges) below them: no terrain through a tread on
                # a cross-slope; terraces untouched
                st.apply_ramp(terrain, pl, width + 2 * ecfg["ramp_extra_m"], sink=ecfg["ramp_sink_m"], blend_m=1.0,
                              protect=shapely.union_all([t["polygon"] for t in terr]) if terr else None)
                eds.append({"osmid": str(r["osmid"]), "profile": np.round(pl[::2] if len(pl) > 4 else pl, 3).tolist(),
                            "landings": lands})
            spec = next(x for x in ecfg["routes"] if x["name"] == rt["name"])
            esc_out.append({"name": rt["name"], "width": width, "paint": spec["paint"], "bars": spec.get("bars", 0),
                            "length_m": rt["length_m"], "edges": eds,
                            "z": [round(eds[0]["profile"][0][2], 1), round(eds[-1]["profile"][-1][2], 1)]})
            log(f"escadaria {rt['name']}: {esc_out[-1]['z'][0]} -> {esc_out[-1]['z'][1]} m, {len(eds)} edges, "
                f"{nland} landings; {int(hit.sum())} buildings cleared from the corridor, {painted} facades painted")
    (out / "escadarias.json").write_text(json.dumps(esc_out, indent=1))

    # ---------------- lookouts (mirantes): sited here so the house or two on each deck leave the meshes; the deck,
    # railing and bench are built by build_details.py from lookouts.json (rio_geo/details.py)
    lcfg = CONFIG.get("details", {}).get("lookouts", {"enabled": False})
    lookouts = []
    if lcfg["enabled"] and len(favelas):
        lzone = shapely.union_all([z[1] for z in fav.infill_zones(favelas, CONFIG["places"]["hubs"], bbox)])
        rings = [g.exterior if g.geom_type == "Polygon" else g.convex_hull.exterior for g in buildings.geometry]
        zmax = np.array([terrain.sample(*np.asarray(r.coords)[:, :2].T).max() for r in rings])
        zmin = np.array([terrain.sample(*np.asarray(r.coords)[:, :2].T).min() for r in rings])
        top = zmax + buildings["height_m"].to_numpy()
        hs = dt.Houses(gpd.GeoDataFrame({"top": top, "roof_top": top + 1.1 * buildings["water_tank"].eq(True),
                                         "ground_min": zmin, "ground_max": zmax, "informal": buildings["informal"],
                                         "frontage": buildings["frontage"], "osmid": buildings["osmid"].astype(str)},
                                        geometry=buildings.geometry.values, crs=PROJECTED_CRS))
        circ_l = dt.Circulation([{"class": r["cls"], "points": prof, "width_m": r["width_m"], "bridge": bool(r["is_bridge"]),
                                  "osmid": str(r["osmid"]), "name": None if str(r.get("name")) == "nan" else r.get("name")}
                                 for (_, r), prof in zip(surf.iterrows(), profiles)], rd.CLASS_INFO)
        block = [t["polygon"].buffer(0.5) for t in terr] + [e["polygon"].buffer(1.0) for e in elems] + \
                [s["corridor"].buffer(0.8) for s in hub_stairs] + \
                [Point(h["center"]).buffer(lcfg["hub_exclude_m"]) for h in CONFIG["places"]["hubs"]]
        lookouts, lstats = dt.lookout_sites(hs, circ_l, terrain, lzone, shapely.union_all(block), lcfg)
        gone = sorted({h for s in lookouts for h in s["remove"]})
        buildings = buildings.drop(index=gone).reset_index(drop=True)
        log(f"lookouts: {lstats}; " + "; ".join(f"{s['edge']} @ {s['z']:.0f} m facing {s['heading_deg']:.0f} deg "
                                                f"(score {s['score']})" for s in lookouts))
    (out / "lookouts.json").write_text(json.dumps(
        [{"edge": s["edge"], "z": round(s["z"], 3), "score": s["score"], "heading_deg": s["heading_deg"],
          "side": s["side"], "xy": np.round(s["xy"], 3).tolist(), "t": np.round(s["t"], 5).tolist(),
          "n": np.round(s["n"], 5).tolist(), "inner": np.round(s["inner"], 3).tolist(), "deck": s["deck"].wkt,
          "houses_removed": len(s["remove"])} for s in lookouts], indent=1))

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

    scfg = rcfg.get("stepped_stairs", {"enabled": False})
    step_stats = {"edges": 0, "steps": 0, "length_m": 0.0, "steep_treads_under_25cm": 0}
    for (_, r), prof in zip(surf.iterrows(), profiles):
        lift = rcfg["surface_offset_m"] + r["rank"] * 0.004
        if isinstance(r.get("escadaria"), str) and esc_out:  # built by build_details.py (escadarias.json)
            step_stats["edges"] += 1
            step_stats["length_m"] += r.geometry.length
            continue
        if r["group"] == "steps" and scfg["enabled"] and not r["is_bridge"]:
            blocks, info = st.step_blocks(prof + [0.0, 0.0, lift], r["width_m"], scfg["rise_m"])
            for v, t in blocks:
                v = np.asarray(v, dtype=float)
                key = tile_of(*v[:, :2].mean(axis=0))
                tiles[key]["Roads"].add("M_Steps", v - [W + key[0] * tile_m, S + key[1] * tile_m, 0.0], t)
            step_stats["edges"] += 1
            step_stats["steps"] += info["steps"]
            step_stats["length_m"] += r.geometry.length
            if info["min_tread_m"] is not None and info["min_tread_m"] < 0.25:
                step_stats["steep_treads_under_25cm"] += 1
            continue
        for key, run in rd.split_by_tile(prof, tile_of):
            pe, pn = W + key[0] * tile_m, S + key[1] * tile_m
            local = run - [pe, pn, 0.0]
            v, t, nrm = rd.ribbon(local, r["width_m"] / 2, lift)
            tiles[key]["Roads"].add(rd.MATERIAL[r["group"]], v, t, nrm)

    for pad in pads:  # turning bulbs, just under the lane ribbon where they overlap it
        key = tile_of(*pad["center"])
        v, t, nrm = tna.pad_mesh(pad, rcfg["surface_offset_m"] - 0.003, (W + key[0] * tile_m, S + key[1] * tile_m))
        tiles[key]["Roads"].add("M_RoadMinor", v, t, nrm)
    if step_stats["edges"]:
        log(f"stepped stairs: {step_stats['edges']} steps edges, {step_stats['length_m'] / 1000:.1f} km, "
            f"{step_stats['steps']} steps; {step_stats['steep_treads_under_25cm']} edges with a tread < 25 cm")
    # Building/road conflicts: footprints overlapping a drivable carriageway indicate a width or data error.
    drivable = surf[surf["group"].isin(["major", "minor"]) & ~surf["is_bridge"]]
    carriageways = [g.buffer(w / 2, cap_style="flat") for g, w in zip(drivable.geometry, drivable["width_m"])]
    tree = STRtree(carriageways)
    height_sources, conflicts, placed = {}, 0, 0
    fstyle, style_counts = bcfg.get("formal_style", {}), {}
    house_rows = []  # placed footprints with their heights, for build_details.py (doors, wires, lookouts)
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
            base = zg.min() - bcfg["foundation_sink_m"]
            mat = r.get("material")
            style = None
            if not isinstance(mat, str):
                if r["informal"]:
                    mat = "M_BuildingInformal"
                elif fstyle.get("enabled"):
                    style = bld.formal_style((str(r.get("osmid")), round(poly.centroid.x, 1)), h, poly.area, fstyle)
                    mat = style["material"]
                else:
                    mat = "M_BuildingFormal"
            band_top = zg.max() + style["ground_band_m"] if style and style["ground_band_m"] else None
            mesh = bld.extrude(poly, band_top if band_top else base, zg.max() + h, pivot)
            if mesh is None:
                continue
            tiles[key]["Buildings"].add(mat, *mesh)
            if band_top:  # darker shopfront / pilotis storey; its roof face is hidden inside the tower
                bm = bld.extrude(poly, base, band_top, pivot)
                if bm is not None:
                    tiles[key]["Buildings"].add("M_GroundFloor", *bm)
            top = zg.max() + h
            if style:
                style_counts[mat] = style_counts.get(mat, 0) + 1
                if style["parapet_m"]:
                    ring = poly.difference(poly.buffer(-0.2, join_style="mitre"))
                    if ring.geom_type == "Polygon":
                        pm = bld.extrude(ring, top - 0.05, top + style["parapet_m"], pivot)
                        if pm is not None:
                            tiles[key]["Buildings"].add(mat, *pm)
                if style["machine_room"]:
                    room = bld.machine_room(poly, style["machine_room"][0], str(r.get("osmid")))
                    rm = bld.extrude(room, top - 0.1, top + style["machine_room"][1], pivot) if room is not None else None
                    if rm is not None:
                        tiles[key]["Buildings"].add("M_RoofConcrete", *rm)
                        style_counts["machine_rooms"] = style_counts.get("machine_rooms", 0) + 1
            tank_top, tank_at = top, None
            skey = (str(r.get("osmid")), round(poly.centroid.x, 1))
            if r.get("parapet") == True:  # noqa: E712 - low wall around the laje
                ring = poly.difference(poly.buffer(-0.15, join_style="mitre"))
                if ring.geom_type == "Polygon":
                    pm = bld.extrude(ring, top, top + fcfg["roof"]["parapet_m"], pivot)
                    if pm is not None:
                        tiles[key]["Buildings"].add(mat, *pm)
            if r.get("roof_room") == True:  # noqa: E712 - puxadinho: small room on the laje
                room = fav.roof_room(poly, skey, fcfg)
                rm = bld.extrude(room, top - 0.1, top + fcfg["roof"]["room_m"], pivot)
                if rm is not None:
                    tiles[key]["Buildings"].add(mat, *rm)
                    tank_top, tank_at = top + fcfg["roof"]["room_m"], room.representative_point()
            if r.get("water_tank") == True:  # noqa: E712 - numpy bool or NaN; blue caixa d'agua on the laje (flat roof)
                if tank_at is None:  # toward a corner, not dead centre
                    cc = np.asarray(poly.centroid.coords[0])
                    vs = np.asarray(poly.exterior.coords)[:-1]
                    v = vs[int(stable_unit(skey + ("tank_corner",)) * len(vs))]
                    p = cc + (v - cc) * 0.55
                    tank_at = Point(*p) if poly.buffer(-0.7).contains(Point(*p)) else poly.representative_point()
                tank = bbox_box_mesh((tank_at.x - pivot[0], tank_at.y - pivot[1]), 1.2, tank_top, tank_top + 1.1)
                tiles[key]["Buildings"].add("M_WaterTank", *tank)
            roof_top = max(top + (fcfg["roof"]["parapet_m"] if r.get("parapet") == True else 0.0),  # noqa: E712
                           tank_top + (1.1 if r.get("water_tank") == True else 0.0))  # noqa: E712
            house_rows.append({"geometry": poly, "osmid": str(r.get("osmid")), "informal": bool(r["informal"]),
                               "frontage": r.get("frontage") if isinstance(r.get("frontage"), str) else "",
                               "material": mat, "ground_min": float(zg.min()), "ground_max": float(zg.max()),
                               "top": float(top), "roof_top": float(roof_top)})
            height_sources[src] = height_sources.get(src, 0) + 1
            placed += 1
    log(f"buildings placed: {placed}; carriageway conflicts: {conflicts}; formal styles: {style_counts}")
    gpd.GeoDataFrame(house_rows, geometry="geometry", crs=PROJECTED_CRS).to_file(out / "houses.gpkg", driver="GPKG")

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
        "stepped_stairs": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in step_stats.items()},
        "landmark_peaks": [p for p in peaks_report if p.get("status") != "outside DEM window"],
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
