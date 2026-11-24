"""Informal housing infill for under-mapped favelas (Vidigal: 463 buildings mapped in OSM, thousands in reality).

Runs in build_area.py BEFORE the ground model, because the DSM already contains the real (unmapped) houses: their
footprints must be subtracted from the DSM like mapped buildings, otherwise the infill would sit on a ground
surface that still includes roofs.

Per favela zone (OSM residential=favela polygons, plus each designed hub's surroundings) in the AOI:
  free space = zone - (every road / beco / stair / path buffered by half its width + clearance)
                    - (mapped buildings + gap)
  1. frontage: houses lining both sides of every street, beco and stair in the zone, aligned with it, with
     varied widths (small / medium / wide merged houses), set back 0-1.2 m, mostly wall to wall with an
     occasional narrow passage between them;
  2. interior: the space behind the frontage is filled by throwing houses of the same size mix at a jittered
     candidate grid, turned along the contour (DSM gradient), until the zone reaches its coverage target.
Footprints only partly inside the free space, or touching a house already placed, are trimmed (and dropped if
what remains is too small). Every random choice is keyed on stable ids, so rebuilds are identical.

Heights and looks (house_style): 2-5 storeys skewed low, +1 on a car street, a few 5-7 storey "predios", a
smooth spatial variation so neighbouring blocks differ; facade colour (bare brick, cement render or a painted
colour); laje extras: parapet, a small room on the roof (puxadinho), the blue water tank.
"""
import numpy as np
import shapely
from scipy import ndimage
from shapely.affinity import rotate, scale
from shapely.geometry import Point, box

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


def _between(lo_hi, u):
    return lo_hi[0] + u * (lo_hi[1] - lo_hi[0])


def house_size(key, cfg):
    """(width along the street / contour, depth) from the size mix (small / medium / wide)."""
    u = stable_unit(key + ("size",))
    acc = 0.0
    for name, s in cfg["sizes"].items():
        if name.startswith("_"):
            continue
        acc += s["share"]
        if u < acc:
            break
    return _between(s["w"], stable_unit(key + ("w",))), _between(s["d"], stable_unit(key + ("d",)))


def value_noise(x, y, cell, key):
    """Smooth deterministic noise in [0, 1) on a `cell`-metre lattice (bilinear between hashed corners)."""
    gx, gy = x / cell, y / cell
    i, j = int(np.floor(gx)), int(np.floor(gy))
    fx, fy = gx - i, gy - j
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    c = [[stable_unit((key, i + a, j + b)) for b in (0, 1)] for a in (0, 1)]
    return (c[0][0] * (1 - fx) * (1 - fy) + c[1][0] * fx * (1 - fy) + c[0][1] * (1 - fx) * fy + c[1][1] * fx * fy)


def house_style(key, cfg, x=None, y=None, footprint_m2=None, street=None, max_levels=None):
    """Levels, facade material and laje extras for one informal house (procedural or mapped). max_levels caps
    the result (and rules out towers), e.g. around a designed hub."""
    lv = cfg["levels"]
    u = stable_unit(key + ("levels",))
    levels = lv[0] + int(u ** cfg["levels_skew"] * (lv[1] - lv[0] + 1))
    if x is not None and cfg.get("noise_m"):
        levels += int(round((value_noise(x, y, cfg["noise_m"], "favela-levels") - 0.5) * 2 * cfg["noise_levels"]))
    if street in ("major", "minor") and stable_unit(key + ("street",)) < cfg["street_bonus_share"]:
        levels += 1
    t = cfg["towers"]
    if (footprint_m2 or 0) >= t["min_footprint_m2"] and stable_unit(key + ("tower",)) < t["share"]:
        levels = t["levels"][0] + int(stable_unit(key + ("tower_lv",)) * (t["levels"][1] - t["levels"][0] + 1))
    if footprint_m2 is not None and footprint_m2 < 18.0:
        levels = min(levels, 3)  # thin slivers stay low
    levels = int(np.clip(levels, 1, max(lv[1], t["levels"][1]) if max_levels is None else max_levels))
    u = stable_unit(key + ("colour",))
    acc, material = 0.0, "M_BuildingInformal"
    for m, share in cfg["colours"].items():
        if m.startswith("_"):
            continue
        acc += share
        if u < acc:
            material = m
            break
    r = cfg["roof"]
    return {"levels": levels, "material": material,
            "parapet": stable_unit(key + ("parapet",)) < r["parapet_share"],
            "roof_room": stable_unit(key + ("room",)) < r["room_share"] and (footprint_m2 or 30) >= 20.0,
            "water_tank": stable_unit(key + ("tank",)) < cfg["water_tank_fraction"]}


def roof_room(poly, key, cfg):
    """Small room on the laje: the footprint scaled toward one of its corners."""
    coords = np.asarray(poly.exterior.coords)[:-1]
    corner = coords[int(stable_unit(key + ("room_corner",)) * len(coords))]
    f = _between(cfg["roof"]["room_scale"], stable_unit(key + ("room_f",)))
    return scale(poly, f, f, origin=tuple(corner))


class _Placed:
    """Placed footprints in a coarse grid hash, for neighbour queries while placing."""

    def __init__(self, cell=8.0):
        self.cell, self.grid, self.items = cell, {}, []

    def _keys(self, bounds, pad):
        w, s, e, n = bounds
        c = self.cell
        return [(i, j) for i in range(int((w - pad) // c), int((e + pad) // c) + 1)
                for j in range(int((s - pad) // c), int((n + pad) // c) + 1)]

    def near(self, poly, pad):
        idx = {k for key in self._keys(poly.bounds, pad) for k in self.grid.get(key, ())}
        return [self.items[k] for k in idx]

    def add(self, poly):
        self.items.append(poly)
        for key in self._keys(poly.bounds, 0.0):
            self.grid.setdefault(key, []).append(len(self.items) - 1)


def _fit(fp, free, placed, gap, min_area):
    """Trim a candidate footprint to the free space and away from placed houses; None if too little is left."""
    if not free.contains(fp):
        fp = fp.intersection(free)
        if fp.geom_type == "MultiPolygon":
            fp = max(fp.geoms, key=lambda g: g.area)
        if fp.is_empty or fp.geom_type != "Polygon" or fp.area < min_area:
            return None
    nb = [o for o in placed.near(fp, gap) if fp.distance(o) < gap]
    if nb:
        fp = fp.difference(shapely.union_all([o.buffer(gap, join_style="mitre") for o in nb]))
        if fp.geom_type == "MultiPolygon":
            fp = max(fp.geoms, key=lambda g: g.area)
        if fp.is_empty or fp.geom_type != "Polygon" or fp.area < min_area:
            return None
    # drop slivers left by trimming (thinner than 2.5 m)
    if fp.buffer(-1.25).is_empty:
        return None
    return fp


def frontage_candidates(zone, edges_with_width, cfg, label):
    """Rectangles lining both sides of every circulation edge in the zone: [(footprint, key, street group)]."""
    fc = cfg["frontage"]
    out = []
    reach = zone.buffer(12.0)
    for ei, (g, w, grp) in enumerate(zip(edges_with_width.geometry, edges_with_width["width_m"],
                                         edges_with_width["group"])):
        if not g.intersects(reach):
            continue
        clear = cfg["clear_path_m"] if grp in ("path", "steps") else cfg["clear_road_m"]
        L = g.length
        for side in (-1, 1):
            s, k = 0.0, 0
            while s < L:
                key = (label, "front", ei, side, k)
                W, D = house_size(key, cfg)
                c = s + W / 2
                if c > L:
                    break
                p0 = np.asarray(g.interpolate(max(0.0, c - 0.5)).coords[0])
                p1 = np.asarray(g.interpolate(min(L, c + 0.5)).coords[0])
                t = p1 - p0
                if np.hypot(*t) < 1e-6:
                    s += W
                    k += 1
                    continue
                t = t / np.hypot(*t)
                nrm = np.array([-t[1], t[0]]) * side
                u = stable_unit(key + ("setback",))
                setback = 0.0 if u < fc["setback_zero_share"] else _between(fc["setback_m"], u)
                off = w / 2 + clear + 0.05 + setback + D / 2
                pc = np.asarray(g.interpolate(c).coords[0]) + nrm * off
                if zone.contains(Point(*pc)):
                    ang = np.degrees(np.arctan2(t[1], t[0]))
                    fp = rotate(box(pc[0] - W / 2, pc[1] - D / 2, pc[0] + W / 2, pc[1] + D / 2), ang, origin=tuple(pc))
                    out.append((fp, key, grp))
                gap = _between(fc["passage_m"], stable_unit(key + ("gapw",))) \
                    if stable_unit(key + ("passage",)) < fc["passage_share"] else _between(fc["gap_m"], stable_unit(key + ("gap",)))
                s += W + gap
                k += 1
    return out


def generate_infill(favelas, mapped_buildings, edges_with_width, dsm, dsm_tf, bbox, cfg):
    """Returns (list of dicts with geometry / levels / favela / lot id / style, per-favela report)."""
    aoi = box(*bbox)
    lines = [g.buffer(w / 2 + (cfg["clear_path_m"] if grp in ("path", "steps") else cfg["clear_road_m"]),
                      cap_style="flat")
             for g, w, grp in zip(edges_with_width.geometry, edges_with_width["width_m"], edges_with_width["group"])]
    circulation = shapely.union_all(lines) if lines else shapely.Polygon()
    mapped = shapely.union_all([g.buffer(cfg["gap_to_mapped_m"]) for g in mapped_buildings.geometry]) \
        if len(mapped_buildings) else shapely.Polygon()
    houses, report = [], []
    gap, min_area = cfg["gap_between_m"], cfg["min_footprint_m2"]
    for fid, zone in enumerate(favelas):
        name, poly = zone[0], zone[1]
        opts = zone[2] if len(zone) > 2 else {}  # per-zone overrides, e.g. {"max_levels": 3} around a hub
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
        placed = _Placed()
        added_area, n_front, n_inner = 0.0, 0, 0

        def place(fp, key, street, x, y):
            nonlocal added_area
            st = house_style(key, cfg, x, y, fp.area, street, opts.get("max_levels"))
            houses.append({"geometry": fp, "favela": name, "lot": f"{fid}:{round(x)}:{round(y)}",
                           "frontage": street or "", **st})
            placed.add(fp)
            added_area += fp.area

        # 1. frontage along streets, becos and stairs (shuffled, so a coverage cap thins them evenly)
        if cfg["frontage"]["enabled"]:
            cands = frontage_candidates(poly, edges_with_width, cfg, name)
            for k in np.argsort([stable_unit(c[1] + ("order",)) for c in cands]):
                if added_area >= need:
                    break
                fp, key, grp = cands[k]
                if stable_unit(key + ("empty",)) < cfg["empty_lot_fraction"]:
                    continue
                fp = _fit(fp, free, placed, gap, min_area)
                if fp is not None:
                    c = fp.centroid
                    place(fp, key, grp, c.x, c.y)
                    n_front += 1
        # 2. interior: jittered candidate grid, contour-aligned, until the coverage target
        step = cfg["interior_grid_m"]
        w, s, e, n = poly.bounds
        X, Y = np.meshgrid(np.arange(w + step / 2, e, step), np.arange(s + step / 2, n, step))
        X, Y = X.ravel(), Y.ravel()
        J = np.array([[stable_unit((name, "jx", round(x), round(y))) - 0.5,
                       stable_unit((name, "jy", round(x), round(y))) - 0.5] for x, y in zip(X, Y)]).reshape(-1, 2)
        X, Y = X + J[:, 0] * step * 0.8, Y + J[:, 1] * step * 0.8
        inside = shapely.contains_xy(free, X, Y)
        X, Y = X[inside], Y[inside]
        ang = contour_angle(dsm, dsm_tf, X, Y)
        for k in np.argsort([stable_unit((name, round(x, 1), round(y, 1))) for x, y in zip(X, Y)]):
            if added_area >= need:
                break
            x, y = X[k], Y[k]
            key = (name, "inner", round(x, 1), round(y, 1))
            if stable_unit(key + ("empty",)) < cfg["empty_lot_fraction"]:
                continue
            W, D = house_size(key, cfg)
            fp = rotate(box(x - W / 2, y - D / 2, x + W / 2, y + D / 2),
                        np.degrees(ang[k]) + (stable_unit(key + ("rot",)) - 0.5) * 16.0, origin=(x, y))
            fp = _fit(fp, free, placed, gap, min_area)
            if fp is not None:
                place(fp, key, None, x, y)
                n_inner += 1
        report.append({**entry, "added": n_front + n_inner, "frontage": n_front, "interior": n_inner,
                       "coverage_after": round((mapped_area + added_area) / poly.area, 3)})
    return houses, report
