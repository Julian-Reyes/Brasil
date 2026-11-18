"""Lake shore promenade (Lagoa Rodrigo de Freitas): walking lane, bike lane, green strip with trees and palms,
benches facing the water, kiosks.

Like rio_geo/beach.py, geometry is built only from a *loop* (the water body's outer shoreline) plus a measured
*profile* (offsets as functions of normalised station 0..1 around the loop), so the compressed map can re-apply
the profile to its own lagoon outline. Offsets are metres from the shoreline towards the LAND.

Frames: UTM metres (EPSG:31983), z = metres above sea level.
"""
import numpy as np
from scipy import ndimage
from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient

from .beach import _q, _runs, box, palm
from .common import stable_unit


class Loop:
    """Closed shoreline resampled every `step` m. nrm points to the land (right of travel on a CCW ring)."""

    def __init__(self, ring_coords, step, normal_smoothing_m):
        ring = np.asarray(ring_coords)[:-1, :2]
        d = np.r_[0, np.cumsum(np.hypot(*np.diff(np.vstack([ring, ring[:1]]), axis=0).T))]
        n = int(round(d[-1] / step))
        s = np.linspace(0, d[-1], n, endpoint=False)
        closed = np.vstack([ring, ring[:1]])
        self.xy = np.column_stack([np.interp(s, d, closed[:, 0]), np.interp(s, d, closed[:, 1])])
        self.s, self.length = s, d[-1]
        sm = ndimage.gaussian_filter1d(self.xy, normal_smoothing_m / step, axis=0, mode="wrap")
        t = np.roll(sm, -1, axis=0) - np.roll(sm, 1, axis=0)
        self.t = t / np.linalg.norm(t, axis=1, keepdims=True)
        self.nrm = np.column_stack([self.t[:, 1], -self.t[:, 0]])  # CCW exterior: land is to the right

    @property
    def u(self):
        return self.s / self.length

    def at(self, offset):
        return self.xy + self.nrm * np.asarray(offset, dtype=float).reshape(-1, 1)

    def project(self, x, y):
        i = int(np.argmin(np.hypot(self.xy[:, 0] - x, self.xy[:, 1] - y)))
        d = np.array([x, y]) - self.xy[i]
        return i, float(d @ self.nrm[i])


def make_loop(water_geom, cfg):
    outer = max(getattr(water_geom, "geoms", [water_geom]), key=lambda p: p.area)
    return Loop(orient(outer, 1.0).exterior.coords, cfg["station_step_m"], cfg["normal_smoothing_m"])


def _hit(p, n, geom, lo, hi):
    ray = LineString([p + n * lo, p + n * hi])
    x = ray.intersection(geom)
    if x.is_empty:
        return np.nan
    return lo + min(Point(ray.coords[0]).distance(g) for g in getattr(x, "geoms", [x]))


def measure_profile(loop, path_lines, road_polys, building_union, cfg):
    """Per station (every measure_step_m): OSM promenade path offset, first drivable road edge, first building.
    Smoothed with wrap-around; returns a JSON-able dict keyed by normalised station."""
    step = cfg["measure_step_m"]
    s = np.arange(0, loop.length, step)
    idx = (s / loop.length * len(loop.s)).astype(int) % len(loop.s)
    raw = {"path_centre": [], "road_edge": [], "building": []}
    for i in idx:
        p, n = loop.xy[i], loop.nrm[i]
        raw["path_centre"].append(_hit(p, n, path_lines, 0.3, cfg["path_search_m"]))
        raw["road_edge"].append(_hit(p, n, road_polys, 0.3, cfg["strip_max_m"] + 20.0))
        raw["building"].append(_hit(p, n, building_union, 0.3, cfg["strip_max_m"] + 20.0))
    prof = {"u": (s / loop.length).round(5).tolist(), "length_m": round(float(loop.length), 1), "measured": {}}
    win = max(1, int(round(cfg["profile_smoothing_m"] / step))) | 1
    for k, default in (("path_centre", cfg["defaults_m"]["path_centre"]),
                       ("road_edge", cfg["strip_max_m"] + 20.0), ("building", cfg["strip_max_m"] + 20.0)):
        v = np.array(raw[k], dtype=float)
        prof["measured"][k] = round(float(np.isfinite(v).mean()), 3)
        ok = np.isfinite(v)
        if ok.any():
            x = np.arange(len(v))
            v = np.interp(x, x[ok], v[ok], period=len(v))
        else:
            v = np.full(len(v), default)
        if k == "path_centre":
            v = ndimage.median_filter(v, size=win, mode="wrap")
            v = ndimage.gaussian_filter1d(v, win / 2, mode="wrap")
        else:  # obstacles: keep the nearest (min filter) so lanes never run into a road or a building
            v = ndimage.minimum_filter1d(v, size=win, mode="wrap")
        prof[k] = v.round(2).tolist()
    return prof


def profile_at(prof, u, key):
    return np.interp(u, prof["u"], prof[key], period=1.0)


def tree(base, height, crown_r, seed):
    """Low-poly broadleaf tree: hexagonal trunk + flattened icosahedron crown. {material: (verts, tris)}."""
    sides, r = 6, 0.22
    trunk_top = base[2] + height * 0.55
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring0 = np.column_stack([base[0] + r * np.cos(ang), base[1] + r * np.sin(ang), np.full(sides, base[2] - 0.3)])
    ring1 = np.column_stack([base[0] + r * 0.7 * np.cos(ang), base[1] + r * 0.7 * np.sin(ang),
                             np.full(sides, trunk_top + crown_r * 0.3)])
    tv = np.vstack([ring0, ring1])
    tt = [t for k in range(sides) for t in ((k, (k + 1) % sides, sides + (k + 1) % sides),
                                              (k, sides + (k + 1) % sides, sides + k))]
    g = (1 + 5 ** 0.5) / 2
    ico = np.array([(-1, g, 0), (1, g, 0), (-1, -g, 0), (1, -g, 0), (0, -1, g), (0, 1, g), (0, -1, -g), (0, 1, -g),
                    (g, 0, -1), (g, 0, 1), (-g, 0, -1), (-g, 0, 1)], float)
    ico /= np.linalg.norm(ico[0])
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
             (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11),
             (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    rot = stable_unit(("tree_rot", seed)) * np.pi
    c, s_ = np.cos(rot), np.sin(rot)
    ico = ico @ np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]]).T
    centre = np.array([base[0], base[1], trunk_top + crown_r * 0.75])
    cv = centre + ico * [crown_r, crown_r, crown_r * 0.75]
    # per-face vertices so the flat-shaded blockout reads as faceted, not smoothed
    fv = cv[np.array(faces)].reshape(-1, 3)
    ft = np.arange(len(fv)).reshape(-1, 3)
    return {"M_TreeTrunk": (tv, np.array(tt)), "M_TreeCrown": (fv, ft)}


def bench(centre, yaw, z, cfg):
    """Seat + backrest; the backrest is on the land side so the bench faces the water."""
    L, D, H = cfg["size_m"]
    c, s = np.cos(yaw), np.sin(yaw)
    land = np.array([s, -c])  # right of the tangent = towards land
    seat_v, seat_t = box(centre, yaw, (L, D), z, z + H)
    back_c = centre + land * (D / 2 + 0.04)
    back_v, back_t = box(back_c, yaw, (L, 0.08), z, z + H + 0.45)
    return [(seat_v, seat_t), (back_v, back_t)]


def build(loop, prof, terrain, tm, cfg, features, road_tree, road_polys, building_tree, building_geoms):
    """Fill the TiledMesh. Returns statistics (plus '_debug' arrays for the preview)."""
    u = loop.u
    pc = profile_at(prof, u, "path_centre")
    road = profile_at(prof, u, "road_edge")
    bld = profile_at(prof, u, "building")
    limit = np.minimum(road, bld) - cfg["obstacle_clear_m"]  # land-side limit for anything we build
    lw = cfg["lanes_m"]
    walk_w = np.full(len(u), lw["walk"])
    bike_w = np.full(len(u), lw["bike"])
    inner = np.maximum(pc - (walk_w + bike_w) / 2, cfg["shore_clear_m"])
    # squeeze where the road / a building is close: narrow the walking lane, then the bike lane, down to their
    # minimums (a narrow shared promenade, as on the east shore under the hill); only then drop the walking lane
    inner = np.maximum(np.minimum(inner, limit - walk_w - bike_w), cfg["shore_clear_m"])  # slide towards the water
    over = inner + walk_w + bike_w - limit
    cut = np.clip(over, 0, walk_w - lw["walk_min"])
    walk_w, over = walk_w - cut, over - cut
    cut = np.clip(over, 0, bike_w - lw["bike_min"])
    bike_w, over = bike_w - cut, over - cut
    walk_w = np.where(over > 0, 0.0, walk_w)
    inner = np.maximum(np.minimum(inner, limit - walk_w - bike_w), cfg["shore_clear_m"])
    has_lanes = inner + walk_w + bike_w <= limit + 0.05
    if tm.clip:  # an AOI with only part of the lagoon: build and count only the stations inside it
        has_lanes &= np.array([tm.inside(x, y) for x, y in loop.at(inner + walk_w)])
    walk_out = inner + walk_w
    bike_out = walk_out + bike_w
    stats = {}

    def lane_z(lo, hi):
        """Sit on the highest terrain across the lane, smoothed along the loop (never below terrain)."""
        k = 4
        zs = np.stack([terrain.sample(*loop.at(lo + (hi - lo) * f).T) for f in np.linspace(0, 1, k)])
        z = zs.max(axis=0) + cfg["lane_lift_m"]
        z = ndimage.maximum_filter1d(z, 5, mode="wrap")
        return ndimage.gaussian_filter1d(z, 2.0, mode="wrap")

    def strip(material, lo, hi, z, rows):
        for a, b in _runs(rows):
            sl = slice(a, b + 1)
            V = np.stack([np.column_stack([loop.at(lo)[sl], z[sl]]), np.column_stack([loop.at(hi)[sl], z[sl]])],
                         axis=1)
            tm.add_grid(material, V)
            for edge, sign in ((0, -1), (1, 1)):
                top = V[:, edge, :]
                bot = top - [0, 0, cfg["skirt_m"]]
                tm.add_grid(material, np.stack([top, bot], axis=1) if sign > 0 else np.stack([bot, top], axis=1))

    z_lanes = lane_z(inner, bike_out)
    walk_rows = has_lanes & (walk_w > 0)
    strip("M_WalkLane", inner, walk_out, z_lanes, walk_rows)
    strip("M_Ciclovia", walk_out, bike_out, z_lanes, has_lanes)
    stats["loop_m"] = round(float(loop.length), 1)
    stats["bike_lane_m"] = round(float(has_lanes.mean() * loop.length), 1)
    stats["walk_lane_m"] = round(float(walk_rows.mean() * loop.length), 1)
    stats["walk_lane_width_m"] = _q(walk_w[walk_rows]) if walk_rows.any() else None
    stats["bike_lane_width_m"] = _q(bike_w[has_lanes]) if has_lanes.any() else None
    stats["lane_inner_offset_m"] = _q(inner[has_lanes]) if has_lanes.any() else None

    def blocked(x, y, r):
        if tm.clip and not tm.inside(x, y):
            return True
        p = Point(x, y).buffer(r)
        return bool(len(road_tree.query(p, predicate="intersects"))
                    or len(building_tree.query(p, predicate="intersects")))

    placed_xy = []  # (x, y, radius) of everything placed so far, to avoid overlaps

    def free(x, y, r):
        return all(np.hypot(x - a, y - b) > r + rr for a, b, rr in placed_xy)

    # ---- food venues: kiosks and lakeside restaurants. OSM first (land side of the lanes, else water side), then
    # procedural kiosks in long gaps. Restaurants = amenity restaurant / bar on the lake side of the ring road:
    # a building with a raised deck facing the water. Points inside a mapped building are left to that building.
    kc, rc = cfg["kiosk"], cfg["restaurant"]
    kiosks = []
    gap = kc["offset_from_lanes_m"]

    def venue_depth(kind):
        return kc["roof_m"][1] if kind == "kiosk" else rc["building_m"][1] + rc["deck_m"][1]

    def venue_radius(kind):
        return kc["roof_m"][0] / 2 if kind == "kiosk" else 0.5 * np.hypot(rc["roof_m"][0], venue_depth(kind))

    def draw_venue(kind, i, o, x, y, z):
        yaw = np.arctan2(loop.t[i, 1], loop.t[i, 0])
        if kind == "kiosk":
            v, t = box((x, y), yaw, kc["size_m"][:2], z - 0.3, z + kc["size_m"][2])
            tm.add_object("M_Kiosk", v, t, (x, y))
            v, t = box((x, y), yaw, kc["roof_m"][:2], z + kc["size_m"][2], z + kc["size_m"][2] + kc["roof_m"][2])
            tm.add_object("M_KioskRoof", v, t, (x, y))
            return
        D = venue_depth(kind)
        bw, bd, bh = rc["building_m"]
        dw, dd, dh = rc["deck_m"]
        rw, rd, rt = rc["roof_m"]
        # the deck takes the water-facing part of the footprint, the building the land-facing part
        deck_c = loop.at(o - D / 2 + dd / 2)[i]
        bld_c = loop.at(o + D / 2 - bd / 2)[i]
        roof_c = loop.at(o + D / 2 - rd / 2)[i]  # roof covers the building and part of the deck (pergola)
        for mat, (v, t) in (("M_Restaurant", box(bld_c, yaw, (bw, bd), z - 0.3, z + bh)),
                            ("M_Deck", box(deck_c, yaw, (dw, dd), z - 0.3, z + dh)),
                            ("M_RestaurantRoof", box(roof_c, yaw, (rw, rd), z + bh, z + bh + rt))):
            tm.add_object(mat, v, t, (x, y))

    def put_venue(kind, i, rec):
        """Try the land side of the lanes, then the water side. Returns True if placed."""
        D, r = venue_depth(kind), venue_radius(kind)
        cands = []
        if has_lanes[i]:
            cands.append(bike_out[i] + gap + D / 2)
            if inner[i] - gap - D >= cfg["shore_clear_m"]:
                cands.append(inner[i] - gap - D / 2)
        for o in cands:
            x, y = loop.at(o)[i]
            if o + D / 2 > limit[i] + cfg["obstacle_clear_m"] or blocked(x, y, r) or not free(x, y, r + 4.0):
                continue
            z = float(terrain.sample([x], [y])[0])
            draw_venue(kind, i, o, x, y, z)
            placed_xy.append((x, y, r))
            kiosks.append({**rec, "kind": kind, "i": i, "side": "land" if o > inner[i] else "water",
                           "xyz": [round(float(x), 2), round(float(y), 2), round(z, 2)]})
            return True
        return False

    for f in features.get("kiosk", []):
        kind = "restaurant" if f.get("amenity") in ("restaurant", "bar") else "kiosk"
        i, off = loop.project(f["x"], f["y"])
        if not (-2.0 <= off <= limit[i] + 10.0):
            continue  # across the ring road (in the apartment blocks) or out in the water
        if f.get("in_building"):
            kiosks.append({**f, "kind": kind, "source": "osm", "skipped": "inside a mapped building"})
            continue
        if not put_venue(kind, i, {**f, "source": "osm"}):
            kiosks.append({**f, "kind": kind, "source": "osm", "skipped": "no room / duplicate"})
    # procedural kiosks: one in the middle of any gap (between placed venues of either kind) over fill_gap_m
    placed_s = sorted(loop.s[k["i"]] for k in kiosks if "xyz" in k)
    gaps = list(zip(placed_s, placed_s[1:] + [placed_s[0] + loop.length])) if placed_s else [(0.0, loop.length)]
    for a, b in gaps:
        n_add = int((b - a) // kc["fill_gap_m"])
        for k in range(1, n_add + 1):
            target = a + (b - a) * k / (n_add + 1)
            for shift in (0, 40, -40, 80, -80, 120, -120):  # look nearby for room
                i = int(((target + shift) % loop.length) / loop.length * len(u)) % len(u)
                if put_venue("kiosk", i, {"name": None, "osm_id": None, "source": "procedural"}):
                    break
    placed = [k for k in kiosks if "xyz" in k]
    stats["kiosks"] = {"osm": sum(k["kind"] == "kiosk" and k["source"] == "osm" for k in placed),
                       "procedural": sum(k["source"] == "procedural" for k in placed),
                       "water_side": sum(k["kind"] == "kiosk" and k["side"] == "water" for k in placed)}
    stats["restaurants"] = {"placed": sum(k["kind"] == "restaurant" for k in placed),
                            "water_side": sum(k["kind"] == "restaurant" and k["side"] == "water" for k in placed),
                            "names": [k.get("name") for k in placed if k["kind"] == "restaurant"]}
    stats["venues_skipped"] = {"inside_building": sum(k.get("skipped") == "inside a mapped building" for k in kiosks),
                               "no_room_or_duplicate": sum(k.get("skipped") == "no room / duplicate" for k in kiosks)}

    # ---- benches: OSM where mapped (snapped to the water side of the walking lane), else every spacing_m
    bc = cfg["bench"]
    bench_st = []
    for f in features.get("bench", []):
        i, off = loop.project(f["x"], f["y"])
        if -2.0 <= off <= limit[i]:
            bench_st.append((loop.s[i], "osm"))
    s_val = bc["spacing_m"] / 2
    osm_s = np.array([s for s, _ in bench_st] or [-1e9])
    while s_val < loop.length:
        if np.min(np.abs(osm_s - s_val)) > bc["spacing_m"] / 2:
            bench_st.append((s_val, "procedural"))
        s_val += bc["spacing_m"]
    n_bench = {"osm": 0, "procedural": 0}
    for st, src in bench_st:
        i = int(st / loop.length * len(u)) % len(u)
        if not has_lanes[i]:
            continue
        o = inner[i] - bc["offset_from_lane_m"]
        if o < cfg["shore_clear_m"] * 0.5:
            continue
        x, y = loop.at(o)[i]
        if blocked(x, y, 1.0) or not free(x, y, 1.0):
            continue
        z = z_lanes[i] - cfg["lane_lift_m"]
        yaw = np.arctan2(loop.t[i, 1], loop.t[i, 0])
        for v, t in bench(np.array([x, y]), yaw, z, bc):
            tm.add_object("M_Bench", v, t, (x, y))
        placed_xy.append((x, y, 1.0))
        n_bench[src] += 1
    stats["benches"] = n_bench

    # ---- trees: OSM trees in the shore band first, then procedural rows in the green strip
    vc = cfg["vegetation"]
    n_tree = {"osm": 0, "procedural": 0, "palms": 0}

    def plant(x, y, key, src):
        if blocked(x, y, vc["clear_m"]) or not free(x, y, vc["clear_m"]):
            return False
        z = float(terrain.sample([x], [y])[0])
        is_palm = stable_unit(("palm?", key)) < vc["palm_fraction"]
        if is_palm:
            h = vc["palm_height_m"][0] + stable_unit(("ph", key)) * np.ptp(vc["palm_height_m"])
            parts = palm(np.array([x, y, z]), h, np.array([0.0, 0.0]), CONFIG_PALM, key)
        else:
            h = vc["tree_height_m"][0] + stable_unit(("th", key)) * np.ptp(vc["tree_height_m"])
            cr = vc["crown_radius_m"][0] + stable_unit(("cr", key)) * np.ptp(vc["crown_radius_m"])
            parts = tree(np.array([x, y, z]), h, cr, key)
        for mat, (v, t) in parts.items():
            tm.add_object(mat, v, t, (x, y))
        placed_xy.append((x, y, vc["clear_m"]))
        n_tree[src] += 1
        n_tree["palms"] += int(is_palm)
        return True

    for f in features.get("tree", []):
        i, off = loop.project(f["x"], f["y"])
        if bike_out[i] + 0.5 <= off <= limit[i]:
            plant(f["x"], f["y"], ("osm", f["osm_id"]), "osm")
    green_lo = np.where(has_lanes, bike_out + vc["lane_gap_m"], cfg["shore_clear_m"])
    green_hi = np.minimum(limit, green_lo + vc["strip_max_m"])
    width = green_hi - green_lo
    s_val = 0.0
    while s_val < loop.length:
        st = s_val + (stable_unit(("tree_s", round(s_val))) - 0.5) * 2 * vc["jitter_m"]
        s_val += vc["spacing_m"]
        i = int((st % loop.length) / loop.length * len(u)) % len(u)
        if width[i] < vc["min_strip_m"]:
            continue
        rows = 1 if width[i] < vc["two_rows_from_m"] else 2
        for r_ in range(rows):
            frac = 0.5 if rows == 1 else (0.25 + 0.5 * r_)
            o = green_lo[i] + width[i] * frac
            x, y = loop.at(o)[i]
            plant(x, y, ("p", round(st), r_), "procedural")
    stats["trees"] = n_tree
    stats["_debug"] = {"inner": inner, "walk_out": walk_out, "bike_out": bike_out, "limit": limit,
                       "has_lanes": has_lanes, "walk_rows": walk_rows, "green_lo": green_lo, "green_hi": green_hi,
                       "placed": np.array([(x, y) for x, y, _ in placed_xy]).reshape(-1, 2), "kiosks": kiosks}
    return stats


CONFIG_PALM = {"fronds": 7, "frond_length_m": 3.0}
