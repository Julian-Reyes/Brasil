"""Informal housing infill for under-mapped favelas (Vidigal: 463 buildings mapped in OSM, thousands in reality).

Runs in build_area.py BEFORE the ground model, because the DSM already contains the real (unmapped) houses: their
footprints must be subtracted from the DSM like mapped buildings, otherwise the infill would sit on a ground
surface that still includes roofs.

Per favela polygon (OSM residential=favela) in the AOI:
  1. free space = polygon - (every road / beco / stair / path buffered by half its width + clearance)
                          - (mapped buildings + gap)
  2. lots on a ~7 m grid, each rotated so the house's long side follows the local contour (DSM gradient)
  3. a slightly irregular house per lot (2-5 storeys) until the polygon reaches its coverage target; lots are
     visited in a deterministic shuffled order, so the density is even and rebuilds are identical
Footprints only partly inside the free space are clipped to it (and dropped if what remains is too small).
"""
import numpy as np
import shapely
from scipy import ndimage
from shapely.affinity import rotate
from shapely.geometry import box

from .common import stable_unit


def contour_angle(dsm, transform, x, y, smooth_m=30.0):
    """Angle (radians) of the contour direction at points, from the smoothed DSM gradient."""
    res = transform.a
    z = ndimage.gaussian_filter(dsm, smooth_m / res)
    gy, gx = np.gradient(z)
    inv = ~transform
    c, r = inv * (np.asarray(x), np.asarray(y))
    gxs = ndimage.map_coordinates(gx, [r - 0.5, c - 0.5], order=1)
    gys = -ndimage.map_coordinates(gy, [r - 0.5, c - 0.5], order=1)  # raster rows grow southwards
    return np.arctan2(gys, gxs) + np.pi / 2  # perpendicular to the slope = along the contour


def generate_infill(favelas, mapped_buildings, edges_with_width, dsm, dsm_tf, bbox, cfg):
    """Returns (list of dicts with geometry / height_m / levels / favela / lot id, per-favela report)."""
    aoi = box(*bbox)
    lines = [g.buffer(w / 2 + (cfg["clear_path_m"] if grp in ("path", "steps") else cfg["clear_road_m"]),
                      cap_style="flat")
             for g, w, grp in zip(edges_with_width.geometry, edges_with_width["width_m"], edges_with_width["group"])]
    circulation = shapely.union_all(lines) if lines else shapely.Polygon()
    mapped = shapely.union_all([g.buffer(cfg["gap_to_mapped_m"]) for g in mapped_buildings.geometry]) \
        if len(mapped_buildings) else shapely.Polygon()
    houses, report = [], []
    lot = cfg["lot_m"]
    for fid, (name, poly) in enumerate(favelas):
        poly = poly.intersection(aoi)
        if poly.is_empty or poly.area < 2000:
            continue
        mapped_area = sum(g.intersection(poly).area for g in mapped_buildings.geometry
                          if g.intersects(poly))
        target = cfg["coverage"] * poly.area
        need = target - mapped_area
        entry = {"favela": name, "area_m2": round(poly.area), "mapped_coverage": round(mapped_area / poly.area, 3)}
        if need <= poly.area * 0.02:
            report.append({**entry, "added": 0, "note": "already at coverage target"})
            continue
        free = poly.difference(circulation).difference(mapped)
        shapely.prepare(free)
        w, s, e, n = poly.bounds
        xs = np.arange(w + lot / 2, e, lot)
        ys = np.arange(s + lot / 2, n, lot)
        X, Y = np.meshgrid(xs, ys)
        X, Y = X.ravel(), Y.ravel()
        inside = shapely.contains_xy(free, X, Y)
        X, Y = X[inside], Y[inside]
        ang = contour_angle(dsm, dsm_tf, X, Y)
        order = np.argsort([stable_unit((name, round(x), round(y))) for x, y in zip(X, Y)])
        added_area, added = 0.0, 0
        placed = {}  # lot grid index -> footprint, to check the 8 neighbouring lots for overlap
        gi = np.rint((X - w) / lot).astype(int)
        gj = np.rint((Y - s) / lot).astype(int)
        for k in order:
            if added_area >= need:
                break
            x, y = X[k], Y[k]
            key = (name, round(x), round(y))
            if stable_unit(key + ("empty",)) < cfg["empty_lot_fraction"]:
                continue
            lw = cfg["house_w_m"][0] + stable_unit(key + ("w",)) * np.ptp(cfg["house_w_m"])
            ld = cfg["house_d_m"][0] + stable_unit(key + ("d",)) * np.ptp(cfg["house_d_m"])
            jx, jy = (stable_unit(key + ("jx",)) - 0.5) * cfg["jitter_m"], (stable_unit(key + ("jy",)) - 0.5) * cfg["jitter_m"]
            fp = rotate(box(x + jx - lw / 2, y + jy - ld / 2, x + jx + lw / 2, y + jy + ld / 2),
                        np.degrees(ang[k]) + (stable_unit(key + ("rot",)) - 0.5) * 12.0, origin=(x, y))
            if not free.contains(fp):
                fp = fp.intersection(free)
                if fp.geom_type == "MultiPolygon":
                    fp = max(fp.geoms, key=lambda g: g.area)
                if fp.is_empty or fp.geom_type != "Polygon" or fp.area < cfg["min_footprint_m2"]:
                    continue
            nb = [placed[(gi[k] + a, gj[k] + b)] for a in (-1, 0, 1) for b in (-1, 0, 1)
                  if (gi[k] + a, gj[k] + b) in placed]
            if any(fp.distance(o) < cfg["gap_between_m"] for o in nb):
                fp = fp.difference(shapely.union_all([o.buffer(cfg["gap_between_m"]) for o in nb]))
                if fp.geom_type == "MultiPolygon":
                    fp = max(fp.geoms, key=lambda g: g.area)
                if fp.is_empty or fp.geom_type != "Polygon" or fp.area < cfg["min_footprint_m2"]:
                    continue
            u = stable_unit(key + ("levels",))
            levels = cfg["levels"][0] + int(u ** cfg["levels_skew"] * (cfg["levels"][1] - cfg["levels"][0] + 1))
            houses.append({"geometry": fp, "levels": levels, "favela": name, "lot": f"{fid}:{round(x)}:{round(y)}",
                           "water_tank": stable_unit(key + ("tank",)) < cfg["water_tank_fraction"]})
            placed[(gi[k], gj[k])] = fp
            added_area += fp.area
            added += 1
        report.append({**entry, "added": added, "coverage_after": round((mapped_area + added_area) / poly.area, 3)})
    return houses, report
