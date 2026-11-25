"""Favela street details: house doors, utility poles with overhead wire tangles, lookouts (mirantes).

Runs in scripts/build_details.py AFTER build_area.py (and build_places.py): it needs the placed house footprints
with their heights (houses.gpkg), the solved 3D road / beco / stair centrelines (road_graph_source.json) and the
final terrain. It only adds meshes; it never moves houses or changes the ground (except lookout siting, below).

  * doors: every favela house gets a door on the wall that faces its nearest street / beco / stair (and is not
    blocked by a neighbour), at the ground level just outside it. On car streets some ground floors get a wide
    roller shutter instead (garage / biroska). Houses with no free wall (fully enclosed) get none.
  * wires: concrete poles along the car streets inside favela zones (one side, ~30 m apart, on the shoulder,
    never on a carriageway, in a house or on a hub terrace), joined by power lines on the crossarm and a bundle
    of sagging telecom / "gato" cables below; service drops from the poles to nearby houses; becos and stairs get
    a zigzag of cables from wall to wall overhead, tied into the nearest pole. Spans that would pass through a
    taller house or dip below head height over the ground are dropped. Wires have no collision (kind "Wires").
  * lookouts: points on becos / stairs / lanes whose downhill side looks over the roofs to the sea; the best few
    (well apart) get a small concrete deck on the downhill edge with a railing, a bench facing the view and a
    gameplay marker (kind "lookout"). Sited by lookout_sites() in build_area.py, BEFORE the building meshes, so
    the one or two houses standing on a deck are dropped (a mirante takes a lot); build_details.py builds the
    meshes from lookouts.json (lookout_parts).
Every random choice is keyed on stable ids (stable_unit), so rebuilds are identical.
"""
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from .beach import box
from .buildings import extrude
from .common import stable_unit
from .shore import bench


def _z(terrain, x, y, z=None):
    """Terrain height at one point."""
    return float(terrain.sample([x], [y], z=z)[0])


def _between(lo_hi, u):
    return lo_hi[0] + (lo_hi[1] - lo_hi[0]) * u


class Circulation:
    """Road / beco / stair centrelines from road_graph_source.json, with widths, groups and 3D profiles."""

    def __init__(self, edges, class_info):
        self.edges = [e for e in edges if e["class"] in class_info and len(e["points"]) >= 2]
        self.lines = [LineString(np.asarray(e["points"])[:, :2]) for e in self.edges]
        self.width = np.array([e["width_m"] for e in self.edges])
        self.group = [class_info[e["class"]][1] for e in self.edges]
        self.prof = [np.asarray(e["points"], dtype=float) for e in self.edges]
        self.tree = STRtree(self.lines)
        drv = [k for k, g in enumerate(self.group) if g in ("major", "minor") and not self.edges[k]["bridge"]]
        self.carriageways = shapely.union_all([self.lines[k].buffer(self.width[k] / 2 + 0.1, cap_style="flat")
                                               for k in drv]) if drv else Polygon()
        foot = [k for k, g in enumerate(self.group) if g in ("path", "steps")]
        self.footways = shapely.union_all([self.lines[k].buffer(self.width[k] / 2 + 0.1, cap_style="flat")
                                           for k in foot]) if foot else Polygon()
        shapely.prepare(self.carriageways)
        shapely.prepare(self.footways)

    def nearest(self, x, y, search=12.0):
        """(edge index, distance from the edge's surface) of the closest circulation edge, or (None, inf)."""
        p = Point(x, y)
        best = (None, np.inf)
        for k in self.tree.query(p.buffer(search)):
            d = self.lines[k].distance(p) - self.width[k] / 2
            if d < best[1]:
                best = (int(k), d)
        return best

    def z_at(self, k, x, y):
        """Surface height of edge k at the point of its centreline closest to (x, y)."""
        s = self.lines[k].project(Point(x, y))
        p = self.prof[k]
        sp = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
        return float(np.interp(s, sp, p[:, 2]))


class Houses:
    def __init__(self, gdf):
        self.geoms = list(gdf.geometry)
        self.top = gdf["top"].to_numpy()
        self.roof_top = gdf["roof_top"].to_numpy()
        self.ground_min = gdf["ground_min"].to_numpy()
        self.ground_max = gdf["ground_max"].to_numpy()
        self.informal = gdf["informal"].to_numpy().astype(bool)
        self.frontage = gdf["frontage"].fillna("").to_numpy()
        self.ids = gdf["osmid"].astype(str).to_numpy()
        self.tree = STRtree(self.geoms)

    def at(self, x, y, exclude=None):
        """Index of a house containing (x, y), or None."""
        for k in self.tree.query(Point(x, y), predicate="intersects"):
            if k != exclude:
                return int(k)
        return None

    def max_top_along(self, a, b, exclude=()):
        """Highest roof of any house the 2D segment a-b passes over (and the parameters t where it does)."""
        seg = LineString([a[:2], b[:2]])
        worst = []
        for k in self.tree.query(seg, predicate="intersects"):
            if k in exclude:
                continue
            inter = self.geoms[k].intersection(seg)
            if inter.is_empty:
                continue
            for g in getattr(inter, "geoms", [inter]):
                for q in np.asarray(g.coords):
                    worst.append((seg.project(Point(q)) / max(seg.length, 1e-9), self.roof_top[k]))
        return worst


# --------------------------------------------------------------------------------------------- doors
def doors(houses, circ, terrain, cfg):
    """[(material, verts, tris, anchor)] door / shutter boxes on favela houses, plus stats."""
    out, stats = [], {"doors": 0, "shutters": 0, "enclosed": 0}
    for i in np.flatnonzero(houses.informal):
        poly = orient(shapely.simplify(houses.geoms[i], 0.2, preserve_topology=True), 1.0)
        if poly.geom_type != "Polygon":
            continue
        ring = np.asarray(poly.exterior.coords)[:, :2]
        cands = []
        for a, b in zip(ring[:-1], ring[1:]):
            L = float(np.hypot(*(b - a)))
            if L < cfg["min_wall_m"]:
                continue
            t = (b - a) / L
            n = np.array([t[1], -t[0]])  # outward for a CCW ring
            m = (a + b) / 2
            probe = m + n * 0.7
            if houses.at(*probe, exclude=i) is not None or houses.at(*(m + n * 0.25), exclude=i) is not None:
                continue
            if poly.contains(Point(*probe)):  # concave corner: the probe went back inside
                continue
            k, d = circ.nearest(*probe, search=cfg["search_m"])
            cands.append((d, -L, a, t, n, L, k))
        if not cands:
            stats["enclosed"] += 1
            continue
        d, _, a, t, n, L, k = min(cands, key=lambda c: (round(min(c[0], 99.0) / 2.0), c[1]))
        key = ("door", houses.ids[i], round(a[0], 1), round(a[1], 1))
        street = k is not None and circ.group[k] in ("major", "minor") and d < 2.5
        shutter = street and L >= cfg["shutter_m"][0] + 0.8 and stable_unit(key + ("shutter",)) < cfg["shutter_share"]
        w, h = (cfg["shutter_m"] if shutter else cfg["door_m"])
        s = float(np.clip(_between((0.2, 0.8), stable_unit(key + ("pos",))) * L, w / 2 + 0.3, L - w / 2 - 0.3))
        if L < w + 0.6:
            continue
        p = a + t * s
        zd = _z(terrain, *(p + n * 0.4))
        zd = float(np.clip(zd, houses.ground_min[i], houses.ground_max[i]))
        if houses.top[i] - zd < h + 0.3:
            continue
        yaw = float(np.arctan2(t[1], t[0]))
        mat = "M_Shutter" if shutter else cfg["door_materials"][int(stable_unit(key + ("mat",)) *
                                                                    len(cfg["door_materials"]))]
        v, tr = box(p + n * 0.03, yaw, (w, 0.1), zd - 0.05, zd + h)
        out.append((mat, v, tr, p))
        if not shutter:  # a small concrete step / sill in front of the door
            v, tr = box(p + n * 0.25, yaw, (w + 0.3, 0.4), zd - 0.3, zd + 0.06)
            out.append(("M_DoorStep", v, tr, p))
        stats["shutters" if shutter else "doors"] += 1
    return out, stats


# --------------------------------------------------------------------------------------------- wires
def wire(p0, p1, sag, radius, nseg):
    """Sagging cable (parabola) as a square tube: (verts, tris) in world coordinates."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    tt = np.linspace(0, 1, nseg + 1)
    pts = p0 + (p1 - p0) * tt[:, None]
    pts[:, 2] -= 4 * sag * tt * (1 - tt)
    d = p1 - p0
    d /= max(np.linalg.norm(d), 1e-9)
    side = np.cross(d, [0, 0, 1.0])
    side = side / np.linalg.norm(side) if np.linalg.norm(side) > 1e-6 else np.array([1.0, 0, 0])
    up = np.cross(side, d)
    offs = [side * radius + up * radius, -side * radius + up * radius, -side * radius - up * radius,
            side * radius - up * radius]
    V = np.array([p + o for p in pts for o in offs])
    T = []
    for j in range(nseg):
        a, b = 4 * j, 4 * (j + 1)
        for q in range(4):
            r = (q + 1) % 4
            T += [(a + q, b + q, b + r), (a + q, b + r, a + r)]
    return V, np.array(T)


def _sag_points(p0, p1, sag, n=9):
    tt = np.linspace(0, 1, n)
    pts = np.asarray(p0) + (np.asarray(p1) - np.asarray(p0)) * tt[:, None]
    pts[:, 2] -= 4 * sag * tt * (1 - tt)
    return tt, pts


def _span_ok(p0, p1, sag, houses, terrain, clearance, exclude=()):
    """A cable may not dip below `clearance` over the ground, nor pass through a house roof."""
    tt, pts = _sag_points(p0, p1, sag)
    inner = slice(1, -1)
    g = terrain.sample(pts[inner, 0], pts[inner, 1])
    if (pts[inner, 2] - g).min() < clearance:
        return False
    for t, roof in houses.max_top_along(p0, p1, exclude):
        if 0.04 < t < 0.96 and np.interp(t, tt, pts[:, 2]) < roof + 0.3:
            return False
    return True


def poles_and_wires(houses, circ, terrain, zones, blockers, cfg):
    """Returns (pole parts [(material, verts, tris, anchor)], wire parts [(verts, tris, anchor)], poles, stats)."""
    pc = cfg["poles"]
    shapely.prepare(zones)
    poles = []  # dicts: xy, z, height, arm (unit vector), key
    for k, (line, w, grp) in enumerate(zip(circ.lines, circ.width, circ.group)):
        if grp not in ("major", "minor") or circ.edges[k]["bridge"] or not line.intersects(zones):
            continue
        ekey = ("pole", circ.edges[k]["osmid"], k)
        side = 1 if stable_unit(ekey + ("side",)) < 0.5 else -1
        L = line.length
        s = pc["spacing_m"] * 0.5 * stable_unit(ekey + ("start",))
        j = 0
        while s < L:
            p = np.asarray(line.interpolate(s).coords[0])
            q = np.asarray(line.interpolate(min(L, s + 0.5)).coords[0]) - np.asarray(line.interpolate(max(0, s - 0.5)).coords[0])
            t = q / max(np.hypot(*q), 1e-9)
            n = np.array([-t[1], t[0]])
            for off in (w / 2 + 0.35, w / 2 + 0.2, -(w / 2 + 0.35)):
                c = p + n * side * off
                pt = Point(*c)
                if not zones.contains(pt) or circ.carriageways.contains(pt) or circ.footways.contains(pt):
                    continue
                if any(g.distance(pt) < 0.25 for g in (houses.geoms[h] for h in houses.tree.query(pt.buffer(0.3)))):
                    continue
                if blockers is not None and blockers.contains(pt):
                    continue
                key = ekey + (j,)
                poles.append({"xy": c, "z": _z(terrain, *c), "arm": n, "key": key,
                              "height": _between(pc["height_m"], stable_unit(key + ("h",)))})
                break
            s += pc["spacing_m"] * (0.85 + 0.3 * stable_unit(ekey + (j, "gap")))
            j += 1
    # drop near-duplicates (junctions, both sides of a split edge)
    kept = []
    for p in poles:
        if all(np.hypot(*(p["xy"] - o["xy"])) >= pc["min_gap_m"] for o in kept):
            kept.append(p)
    poles = kept
    parts, wires = [], []
    stats = {"poles": len(poles), "spans": 0, "power_cables": 0, "telecom_cables": 0, "drops": 0, "beco_cables": 0,
             "spans_dropped": 0}
    for p in poles:
        x, y = p["xy"]
        z, H = p["z"], p["height"]
        arm_yaw = float(np.arctan2(p["arm"][1], p["arm"][0]))
        parts.append(("M_Pole", *box(p["xy"], arm_yaw, (0.24, 0.24), z - 0.4, z + H), p["xy"]))
        parts.append(("M_Pole", *box(p["xy"], arm_yaw, (1.8, 0.12), z + H - 0.65, z + H - 0.5), p["xy"]))
        if stable_unit(p["key"] + ("tr",)) < pc["transformer_share"]:
            c = p["xy"] - p["arm"] * 0.45
            parts.append(("M_Transformer", *box(c, arm_yaw, (0.6, 0.6), z + H - 2.9, z + H - 1.8), p["xy"]))
        p["power"] = [np.r_[p["xy"] + p["arm"] * o, z + H - 0.48] for o in (-0.75, -0.25, 0.75)]
        p["telecom_z"] = z + H - pc["telecom_below_top_m"]
    if not poles:
        return parts, wires, poles, stats
    xy = np.array([p["xy"] for p in poles])
    kd = cKDTree(xy)
    pairs = set()
    for a in range(len(poles)):
        for dist, b in zip(*kd.query(xy[a], k=min(4, len(poles)))):
            if b != a and dist <= pc["max_span_m"]:
                pairs.add((min(a, b), max(a, b)))
    linked = {}
    for a, b in sorted(pairs, key=lambda ab: np.hypot(*(xy[ab[0]] - xy[ab[1]]))):
        pa, pb = poles[a], poles[b]
        L = float(np.hypot(*(pa["xy"] - pb["xy"])))
        # keep a graph, not a mesh: at most 3 spans per pole, shortest spans first
        if linked.get(a, 0) >= 3 or linked.get(b, 0) >= 3:
            continue
        key = ("span", pa["key"], pb["key"])
        sag_p = 0.3 + 0.012 * L
        if not _span_ok(pa["power"][1], pb["power"][1], sag_p, houses, terrain, 4.0):
            stats["spans_dropped"] += 1
            continue
        linked[a], linked[b] = linked.get(a, 0) + 1, linked.get(b, 0) + 1
        stats["spans"] += 1
        for k in range(3):
            v, t = wire(pa["power"][k], pb["power"][k], sag_p + 0.05 * k, cfg["radius_m"], cfg["segments"])
            wires.append((v, t, pa["xy"]))
            stats["power_cables"] += 1
        n_tel = int(_between(pc["telecom_cables"], stable_unit(key + ("n",))) + 0.5)
        for k in range(n_tel):
            u = stable_unit(key + ("t", k))
            dz = (u - 0.5) * 0.6
            ja = pa["xy"] + pa["arm"] * ((stable_unit(key + ("oa", k)) - 0.5) * 0.4)
            jb = pb["xy"] + pb["arm"] * ((stable_unit(key + ("ob", k)) - 0.5) * 0.4)
            a3, b3 = np.r_[ja, pa["telecom_z"] + dz], np.r_[jb, pb["telecom_z"] - dz]
            sag = 0.5 + 0.02 * L + 0.8 * u
            if _span_ok(a3, b3, sag, houses, terrain, 2.6):
                v, t = wire(a3, b3, sag, cfg["radius_m"] * 1.4, cfg["segments"])
                wires.append((v, t, pa["xy"]))
                stats["telecom_cables"] += 1
        # tangle: a few short loops hanging off the pole
        for k in range(int(stable_unit(key + ("loops",)) * 3)):
            c = np.r_[pa["xy"], pa["telecom_z"] + 0.2 * k]
            d = (pb["xy"] - pa["xy"]) / L * (1.2 + 0.6 * k)
            v, t = wire(c, np.r_[pa["xy"] + d, pa["telecom_z"] - 0.1 * k], 0.35 + 0.15 * k, cfg["radius_m"] * 1.4, 4)
            wires.append((v, t, pa["xy"]))
    stats["poles_unlinked"] = sum(1 for a in range(len(poles)) if not linked.get(a))
    # service drops: informal houses near a pole get a cable to the wall facing it
    dc = cfg["drops"]
    per_pole = {}
    for i in np.flatnonzero(houses.informal):
        g = houses.geoms[i]
        c = np.asarray(g.centroid.coords[0])
        dist, a = kd.query(c)
        if dist > dc["radius_m"] or stable_unit(("drop", houses.ids[i])) > dc["share"]:
            continue
        if per_pole.get(a, 0) >= dc["max_per_pole"]:
            continue
        p = poles[a]
        wall = np.asarray(nearest_points(g.exterior, Point(*p["xy"]))[0].coords[0])
        zw = min(houses.top[i] - 0.3, houses.ground_min[i] + 2.8 + 1.5 * stable_unit(("drop_z", houses.ids[i])))
        if zw < _z(terrain, *wall) + 2.5:
            continue
        a3 = np.r_[p["xy"], p["telecom_z"] - 0.3 * stable_unit(("drop_p", houses.ids[i]))]
        b3 = np.r_[wall, zw]
        L = float(np.hypot(*(a3[:2] - b3[:2])))
        sag = 0.15 + 0.02 * L
        if L < 1.0 or not _span_ok(a3, b3, sag, houses, terrain, 2.4, exclude=(i,)):
            continue
        v, t = wire(a3, b3, sag, cfg["radius_m"], max(3, cfg["segments"] // 2))
        wires.append((v, t, p["xy"]))
        per_pole[a] = per_pole.get(a, 0) + 1
        stats["drops"] += 1
    # becos and stairs: cables zigzag from wall to wall overhead, chained into the nearest pole
    bc = cfg["becos"]
    for k, (line, w, grp) in enumerate(zip(circ.lines, circ.width, circ.group)):
        if grp not in ("path", "steps") or line.length < 8.0 or not line.intersects(zones):
            continue
        ekey = ("beco", circ.edges[k]["osmid"], k)
        anchors, s, j = [], bc["step_m"] * stable_unit(ekey + ("s",)), 0
        L = line.length
        while s < L:
            p = np.asarray(line.interpolate(s).coords[0])
            q = np.asarray(line.interpolate(min(L, s + 0.5)).coords[0]) - np.asarray(line.interpolate(max(0, s - 0.5)).coords[0])
            t = q / max(np.hypot(*q), 1e-9)
            n = np.array([-t[1], t[0]]) * (1 if j % 2 == 0 else -1)
            probe = Point(*(p + n * (w / 2 + 1.0)))
            if zones.contains(probe):
                near = [h for h in houses.tree.query(probe.buffer(bc["snap_m"]))
                        if houses.geoms[h].distance(probe) <= bc["snap_m"]]
                if near:
                    h = min(near, key=lambda h: houses.geoms[h].distance(probe))
                    wall = np.asarray(nearest_points(houses.geoms[h].exterior, Point(*p))[0].coords[0])
                    zp = circ.z_at(k, *wall)
                    z = min(zp + _between(bc["height_m"], stable_unit(ekey + (j, "z"))), houses.top[h] - 0.2)
                    if z >= zp + 2.6:
                        anchors.append((np.r_[wall, z], h))
            s += bc["step_m"] * (0.8 + 0.4 * stable_unit(ekey + (j, "gap")))
            j += 1
        for (a3, ha), (b3, hb) in zip(anchors[:-1], anchors[1:]):
            n_c = 1 + int(stable_unit(ekey + ("n", round(a3[0], 1))) * bc["max_cables"])
            for c in range(n_c):
                u = stable_unit(ekey + ("c", round(a3[0], 1), c))
                o = np.array([0, 0, (u - 0.5) * 0.5])
                sag = 0.25 + 0.5 * u
                if _span_ok(a3 + o, b3 - o, sag, houses, terrain, 2.4, exclude=(ha, hb)):
                    v, t = wire(a3 + o, b3 - o, sag, cfg["radius_m"], max(3, cfg["segments"] // 2))
                    wires.append((v, t, a3[:2]))
                    stats["beco_cables"] += 1
        if anchors:  # tie the chain's first anchor into the nearest pole
            a3, ha = anchors[0]
            dist, pi = kd.query(a3[:2])
            if dist <= bc["tie_m"]:
                p3 = np.r_[poles[pi]["xy"], poles[pi]["telecom_z"]]
                sag = 0.3 + 0.02 * dist
                if _span_ok(p3, a3, sag, houses, terrain, 2.4, exclude=(ha,)):
                    v, t = wire(p3, a3, sag, cfg["radius_m"], cfg["segments"])
                    wires.append((v, t, poles[pi]["xy"]))
                    stats["beco_cables"] += 1
    return parts, wires, poles, stats


# --------------------------------------------------------------------------------------------- lookouts
def obstacle_surface(terrain, houses):
    """Terrain with every house raised to its roof: what blocks a view."""
    z = terrain.z.copy()
    xs, ys = terrain.coords()
    for g, top in zip(houses.geoms, houses.roof_top):
        w, s, e, n = g.bounds
        ix = np.flatnonzero((xs >= w) & (xs <= e))
        iy = np.flatnonzero((ys >= s) & (ys <= n))
        if not len(ix) or not len(iy):
            continue
        X, Y = np.meshgrid(xs[ix], ys[iy])
        m = shapely.contains_xy(g, X, Y)
        sub = z[np.ix_(iy, ix)]
        z[np.ix_(iy, ix)] = np.where(m, np.maximum(sub, top), sub)
    return z


def view_score(terrain, obst, x, y, eye_z, heading, cfg):
    """Share of rays in a fan around `heading` that clear every roof and reach low ground (sea / city / beach)."""
    fan = np.radians(np.linspace(-cfg["fan_deg"] / 2, cfg["fan_deg"] / 2, cfg["rays"]))
    d = np.arange(cfg["near_m"], cfg["far_m"], terrain.step * 1.5)
    az = heading + fan[:, None]
    X, Y = x + np.cos(az) * d, y + np.sin(az) * d
    zo = terrain.sample(X.ravel(), Y.ravel(), z=obst).reshape(X.shape)
    ang = np.degrees(np.arctan2(zo - eye_z, d))
    clear = ang.max(axis=1) < cfg["max_block_deg"]
    low = zo[:, -10:].min(axis=1) < cfg["low_ground_m"]
    return float(np.mean(clear & low))


def lookout_sites(houses, circ, terrain, zones, blockers, cfg, obst=None):
    """Pick the best lookout points (runs in build_area.py, before the building meshes). A deck may replace up to
    cfg max_removed informal houses (a mirante takes a lot or two) and stand over lajes lower than the path; it
    never touches a formal building, another street / beco, or `blockers`. The view is scored from the deck's
    outer edge, so the houses it replaces do not count. Returns (sites, stats); each site lists `remove` (house
    indices to drop from the meshes)."""
    if obst is None:
        obst = obstacle_surface(terrain, houses)
    W, D = cfg["deck_m"]
    cands = []
    for k, (line, w, grp) in enumerate(zip(circ.lines, circ.width, circ.group)):
        if grp not in cfg["on_groups"] or circ.edges[k]["bridge"] or not line.intersects(zones):
            continue
        L = line.length
        for s in np.arange(W / 2, L - W / 2, cfg["sample_m"]):
            p = np.asarray(line.interpolate(s).coords[0])
            q = np.asarray(line.interpolate(min(L, s + 0.5)).coords[0]) - np.asarray(line.interpolate(max(0, s - 0.5)).coords[0])
            t = q / max(np.hypot(*q), 1e-9)
            nrm = np.array([-t[1], t[0]])
            zp = circ.z_at(k, *p)
            for side in (-1, 1):
                n = nrm * side
                edge = p + n * (w / 2 + 0.1)
                if not zones.contains(Point(*edge)):
                    continue
                if zp - _z(terrain, *(edge + n * 4.0)) < cfg["min_drop_m"]:
                    continue
                score = view_score(terrain, obst, *(edge + n * D), zp + 1.6, float(np.arctan2(n[1], n[0])), cfg)
                if score >= cfg["min_score"]:
                    cands.append((score, k, side, p, t, n, zp))
    cands.sort(key=lambda c: (-c[0], -c[6]))  # best view first, higher up on ties
    sites = []
    stats = {"candidates": len(cands), "rejected_formal_or_too_many": 0, "rejected_roads": 0}
    for score, k, side, p, t, n, zp in cands:
        if len(sites) >= cfg["count"]:
            break
        if any(np.hypot(*(p - q["xy"])) < cfg["min_separation_m"] for q in sites):
            continue
        inner = p + n * (circ.width[k] / 2 + 0.05)
        deck = Polygon([inner - t * W / 2, inner + t * W / 2, inner + t * W / 2 + n * D, inner - t * W / 2 + n * D])
        z = zp + 0.04
        hits = [int(h) for h in houses.tree.query(deck.buffer(0.3), predicate="intersects")
                if houses.roof_top[h] > z - cfg["over_roof_m"]]
        if len(hits) > cfg["max_removed"] or any(not houses.informal[h] for h in hits):
            stats["rejected_formal_or_too_many"] += 1
            continue
        others = circ.carriageways.union(circ.footways).difference(circ.lines[k].buffer(circ.width[k] / 2 + 0.3))
        if deck.intersects(others) or (blockers is not None and deck.intersects(blockers)):
            stats["rejected_roads"] += 1
            continue
        sites.append({"xy": p, "z": z, "score": round(score, 2), "side": side, "t": t, "n": n, "inner": inner,
                      "deck": deck, "remove": hits,
                      "heading_deg": round(float(np.degrees(np.arctan2(n[0], n[1]))) % 360, 0),
                      "edge": circ.edges[k].get("name") or circ.edges[k]["class"]})
    stats["built"] = len(sites)
    stats["houses_removed"] = sum(len(s["remove"]) for s in sites)
    return sites, stats


def lookout_parts(site, terrain):
    """Deck (solid down to the ground), railing on the three outer sides, bench facing the view.
    [(material, verts, tris, anchor)]."""
    deck, z = site["deck"], site["z"]
    inner, t, n = np.asarray(site["inner"]), np.asarray(site["t"]), np.asarray(site["n"])
    ring = np.asarray(deck.exterior.coords)
    W = float(np.hypot(*(ring[1] - ring[0])[:2]))
    D = float(np.hypot(*(ring[2] - ring[1])[:2]))
    c = inner + n * D / 2
    zg = terrain.sample(ring[:, 0], ring[:, 1])
    parts = []
    m = extrude(deck, float(min(zg.min(), z)) - 0.4, z, (0.0, 0.0))
    if m is not None:
        parts.append(("M_LookoutDeck", m[0], m[1], c))
    rail = [inner + t * W / 2, inner + t * W / 2 + n * D, inner - t * W / 2 + n * D, inner - t * W / 2]
    for a, b in zip(rail[:-1], rail[1:]):
        seg = b - a
        Ls = float(np.hypot(*seg))
        syaw = float(np.arctan2(seg[1], seg[0]))
        for f in np.linspace(0, 1, max(2, int(np.ceil(Ls / 1.5)) + 1)):
            parts.append(("M_Railing", *box(a + seg * f, syaw, (0.06, 0.06), z, z + 1.05), c))
        for hz in (1.05, 0.55):
            parts.append(("M_Railing", *box((a + b) / 2, syaw, (Ls, 0.05), z + hz - 0.05, z + hz), c))
    # bench facing the view (n), backrest toward the path: shore.bench puts the back on the right of the tangent
    yaw = float(np.arctan2(t[1], t[0])) if site["side"] > 0 else float(np.arctan2(-t[1], -t[0]))
    for v, tr in bench(inner + n * 0.75, yaw, z, {"size_m": [1.8, 0.45, 0.45]}):
        parts.append(("M_Bench", v, tr, c))
    return parts
