"""Hero beach blockout: beachfront centreline + cross-section profile -> sand, ciclovia, calcadao, kiosks,
lifeguard posts (Postos) and palm rows.

Design: the only geographic input to the geometry is a 3D *centreline* (the sea-side carriageway of the
beachfront road) and a *profile* (offsets as functions of normalised station along it). The profile is
measured once from the real geography (`measure_profile`) and saved, so the compressed playable map can
re-run `build_geometry` on its own, shorter centreline with the same profile (station is normalised 0..1).

Frames: UTM metres (EPSG:31983), z = metres above sea level. Offsets are metres to the seaward side.
"""
import re

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

from .common import stable_unit


# --------------------------------------------------------------------------------------------- centreline
class Centreline:
    """Resampled polyline. xy (n,2), z road-surface height (n,), hw carriageway half-width (n,),
    s station in metres, t unit tangent, nrm unit normal pointing to the SEA (right of travel, W->E)."""

    def __init__(self, xy, z, hw, step, normal_smoothing_m):
        self.xy, self.z, self.hw = xy, z, hw
        d = np.r_[0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        self.s, self.length = d, d[-1]
        # Normals from a heavily smoothed tangent: junction kinks would otherwise fan the 100 m wide sand band.
        sig = normal_smoothing_m / step
        t = np.gradient(ndimage.gaussian_filter1d(xy, sig, axis=0, mode="nearest"), axis=0)
        self.t = t / np.linalg.norm(t, axis=1, keepdims=True)
        self.nrm = np.column_stack([self.t[:, 1], -self.t[:, 0]])

    @property
    def u(self):
        return self.s / self.length

    def trimmed_to(self, sand, margin_m, reach_m=60.0):
        """Cut the ends where no sand lies seaward (e.g. Delfim Moreira curving up to the Mirante)."""
        hits = [LineString([p + n * hw, p + n * (hw + reach_m)]).intersects(sand)
                for p, n, hw in zip(self.xy, self.nrm, self.hw)]
        k = np.flatnonzero(hits)
        if not len(k):
            raise RuntimeError("no sand seaward of the beachfront centreline")
        pad = int(round(margin_m / (self.s[1] - self.s[0])))
        a, b = max(k[0] - pad, 0), min(k[-1] + pad, len(self.s) - 1)
        out = Centreline.__new__(Centreline)
        out.xy, out.z, out.hw, out.t, out.nrm = (v[a:b + 1] for v in (self.xy, self.z, self.hw, self.t, self.nrm))
        out.s = self.s[a:b + 1] - self.s[a]
        out.length = out.s[-1]
        return out, (round(float(self.s[a]), 1), round(float(self.length - self.s[b]), 1))

    def at(self, offset):
        """Points at per-station seaward offsets (scalar or (n,))."""
        return self.xy + self.nrm * np.asarray(offset, dtype=float).reshape(-1, 1)

    def project(self, x, y):
        """Nearest station index and signed seaward offset of a point."""
        i = int(np.argmin(np.hypot(self.xy[:, 0] - x, self.xy[:, 1] - y)))
        d = np.array([x, y]) - self.xy[i]
        return i, float(d @ self.nrm[i]), float(d @ self.t[i])


def build_centreline(graph_edges, front_roads, coast, cfg, surface_offset_m):
    """Chain the beachfront road edges (from road_graph_source.json) into one W->E sea-side centreline.

    Dual carriageways: an edge is dropped when another front edge lies between it and the coast.
    z and half-width are carried over from the solved road profiles (nearest densified sample).
    """
    sel = [e for e in graph_edges if e["name"] in front_roads and len(e["points"]) >= 2]
    lines = [LineString(np.asarray(e["points"])[:, :2]) for e in sel]
    keep = []
    for k, (e, line) in enumerate(zip(sel, lines)):
        mid = line.interpolate(0.5, normalized=True)
        c = coast.interpolate(coast.project(mid))
        v = np.array(c.coords[0]) - np.array(mid.coords[0])
        v /= max(np.linalg.norm(v), 1e-9)
        if str(e.get("oneway")) in ("True", "true", "yes", "1"):
            # Right-hand traffic: the sea-side carriageway of a divided avenue always has the sea on its right.
            # (A probe toward the coast misses short inland pieces next to junctions, e.g. at Jardim de Alah.)
            d = np.asarray(line.coords[-1]) - np.asarray(line.coords[0])
            if d[0] * v[1] - d[1] * v[0] < 0:
                keep.append(k)
            continue
        probe = LineString([np.array(mid.coords[0]) + v * 0.5, np.array(mid.coords[0]) + v * 40.0])
        if not any(probe.intersects(o) for j, o in enumerate(lines) if j != k):
            keep.append(k)
    pts = np.vstack([_densify(np.asarray(sel[k]["points"]), 2.0) for k in keep])
    hw = np.concatenate([np.full(len(_densify(np.asarray(sel[k]["points"]), 2.0)), sel[k]["width_m"] / 2)
                         for k in keep])
    merged = linemerge([lines[k] for k in keep])
    pieces = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
    pieces = [np.asarray(p.coords) if p.coords[0][0] <= p.coords[-1][0] else np.asarray(p.coords)[::-1]
              for p in pieces]
    pieces.sort(key=lambda p: p[0, 0])
    chain, reach = [], -np.inf
    for p in pieces:
        if p[-1, 0] <= reach + 1.0:  # fully inside the stretch already covered (stray carriageway piece)
            continue
        p = p[p[:, 0] > reach - 1.0] if np.isfinite(reach) else p
        if len(p) >= 2:
            chain.append(p)
            reach = p[-1, 0]
    line = np.vstack(chain)
    step = cfg["station_step_m"]
    xy, _ = _resample(line, step)
    xy = np.vstack([xy[:1], ndimage.gaussian_filter1d(xy, 1.5, axis=0, mode="nearest")[1:-1], xy[-1:]])
    tree = cKDTree(pts[:, :2])
    _, nn = tree.query(xy, k=4)
    z = np.median(pts[nn, 2], axis=1) + surface_offset_m
    return Centreline(xy, ndimage.gaussian_filter1d(z, 5.0), np.median(hw[nn], axis=1), step,
                      cfg["normal_smoothing_m"]), [sel[k]["osmid"] for k in keep]


def _densify(points, spacing):
    p, _ = _resample(points[:, :2], spacing)
    d_src = np.r_[0, np.cumsum(np.hypot(*np.diff(points[:, :2], axis=0).T))]
    d_new = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
    return np.column_stack([p, np.interp(d_new, d_src, points[:, 2])])


def _resample(line, step):
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(line, axis=0).T))]
    keep = np.r_[True, np.diff(d) > 1e-6]
    line, d = line[keep], d[keep]
    n = max(2, int(np.ceil(d[-1] / step)) + 1)
    s = np.linspace(0, d[-1], n)
    return np.column_stack([np.interp(s, d, line[:, 0]), np.interp(s, d, line[:, 1])]), s


# --------------------------------------------------------------------------------------------- profile
def measure_profile(cl, sand, coast, cycleways, water, cfg):
    """Measure the real cross-section along the centreline. Returns a JSON-able dict:
    u (normalised station), ciclovia_centre, sand_edge, waterline (metres seaward of the centreline),
    canal spans as [u0, u1] and per-field 'measured' fractions (the rest is interpolated or default)."""
    step = cfg["measure_step_m"]
    s = np.arange(0, cl.length, step)
    idx = np.searchsorted(cl.s, s).clip(0, len(cl.s) - 1)
    raw = {"ciclovia_centre": [], "sand_edge": [], "waterline": [], "canal": []}
    for i in idx:
        p, n, kerb = cl.xy[i], cl.nrm[i], cl.hw[i]

        def hit(geom, lo, hi):
            ray = LineString([p + n * lo, p + n * hi])
            x = ray.intersection(geom)
            if x.is_empty:
                return np.nan
            pts = [g for g in getattr(x, "geoms", [x])]
            return lo + min(Point(ray.coords[0]).distance(g) for g in pts)

        raw["ciclovia_centre"].append(hit(cycleways, kerb - 1.0, kerb + 8.0))
        inside = sand.contains(Point(p + n * (kerb + 0.5)))
        se = kerb + 0.5 if inside else hit(sand.boundary, kerb, kerb + 60.0)
        raw["sand_edge"].append(se)
        # Waterline = where the ray leaves the OSM sand (its seaward edge is the waterline). The coastline is only
        # a fallback: at the beach ends a ray can miss the nearby shore and hit the coast far away.
        base = se if np.isfinite(se) else kerb
        far = _sand_exit(LineString([p + n * base, p + n * 400.0]), sand)
        raw["waterline"].append(base + far if np.isfinite(far) else hit(coast, base + 5.0, 400.0))
        raw["canal"].append(LineString([p + n * kerb, p + n * (kerb + 60.0)]).intersects(water))
    prof = {"u": (s / cl.length).round(5).tolist(), "length_m": round(float(cl.length), 1), "measured": {}}
    window = max(1, int(round(cfg["profile_smoothing_m"] / step)))
    canal = np.array(raw["canal"])
    for k in ("ciclovia_centre", "sand_edge", "waterline"):
        v = np.array(raw[k], dtype=float)
        if k != "ciclovia_centre":
            v[canal] = np.nan  # the canal mouth is no evidence for the beach shape either side of it
        prof["measured"][k] = round(float(np.isfinite(v).mean()), 3)
        v = _fill(v, cfg["defaults_m"][k])
        v = ndimage.median_filter(v, size=window | 1, mode="nearest")
        prof[k] = ndimage.gaussian_filter1d(v, window / 2, mode="nearest").round(2).tolist()
    prof["canal"] = _spans(canal, s / cl.length, cfg["canal_margin_m"] / cl.length)
    # The ciclovia is only built where OSM has one (e.g. it leaves the front at Arpoador for Copacabana).
    has = np.isfinite(np.array(raw["ciclovia_centre"], dtype=float)).astype(float)
    has = ndimage.uniform_filter1d(has, size=window | 1, mode="nearest") >= 0.5
    prof["ciclovia_spans"] = _spans(has, s / cl.length, 0.0)
    return prof


def _sand_exit(ray, sand):
    """Distance along the ray to the far end of the first sand piece it crosses (nan if none)."""
    x = ray.intersection(sand)
    if x.is_empty:
        return np.nan
    o = Point(ray.coords[0])
    pieces = sorted((g for g in getattr(x, "geoms", [x]) if g.geom_type == "LineString"),
                    key=lambda g: o.distance(g))
    if not pieces:
        return np.nan
    first = pieces[0]
    # merge pieces separated by tiny gaps (polygon seams between Praia do Leblon / Ipanema / Arpoador)
    end = max(o.distance(Point(c)) for c in first.coords)
    for g in pieces[1:]:
        if o.distance(g) - end > 3.0:
            break
        end = max(end, max(o.distance(Point(c)) for c in g.coords))
    return end


def _fill(v, default):
    ok = np.isfinite(v)
    if not ok.any():
        return np.full_like(v, default)
    x = np.arange(len(v))
    return np.interp(x, x[ok], v[ok])


def _spans(mask, u, margin):
    spans, start = [], None
    for k, m in enumerate(np.r_[mask, False]):
        if m and start is None:
            start = k
        if not m and start is not None:
            spans.append([round(float(max(u[start] - margin, 0)), 5),
                          round(float(min(u[k - 1] + margin, 1)), 5)])
            start = None
    return spans


def profile_at(prof, u, key):
    return np.interp(u, prof["u"], prof[key])


# --------------------------------------------------------------------------------------------- features
POSTO_RE = re.compile(r"\bposto\s*(\d+)\b", re.I)
KIOSK_AMENITY = {"kiosk", "fast_food", "bar", "cafe"}


def place_features(cl, prof, features, cfg):
    """Snap OSM beach features to the profile. Returns (postos, kiosks), each a list of dicts with station
    index, offset, name and source. Kiosk gaps longer than cfg kiosk.fill_gap_m get procedural kiosks."""
    postos, kiosks = [], []
    u = cl.u
    sand_edge = profile_at(prof, u, "sand_edge")
    waterline = profile_at(prof, u, "waterline")
    for _, f in (features.iterrows() if features is not None else []):
        x, y = f.geometry.x, f.geometry.y
        i, off, along = cl.project(x, y)
        if (i == 0 or i == len(u) - 1) and abs(along) > 5.0:
            continue  # beyond the ends of the beachfront
        if not (cl.hw[i] - 1.0 <= off <= waterline[i]):
            continue  # across the road (buildings) or out at sea
        name = str(f.get("name")) if str(f.get("name")) not in ("nan", "None") else ""
        m = POSTO_RE.search(name)
        is_life = any(str(f.get(k)).startswith("lifeguard") for k in ("emergency", "amenity", "man_made"))
        if m or is_life:
            postos.append({"i": i, "offset": off, "name": f"Posto {m.group(1)}" if m else (name or "Posto"),
                           "number": int(m.group(1)) if m else None, "source": f"osm:{f['osm_type']}/{f['osm_id']}"})
        elif str(f.get("shop")) == "kiosk" or str(f.get("amenity")) in KIOSK_AMENITY:
            kiosks.append({"i": i, "offset": off, "name": name, "source": f"osm:{f['osm_type']}/{f['osm_id']}"})
    # One posto per number (OSM often has both the tower node and the base building).
    by_num = {}
    for p in postos:
        key = p["number"] if p["number"] is not None else f"i{p['i'] // 25}"
        by_num.setdefault(key, p)
    postos = sorted(by_num.values(), key=lambda p: p["i"])
    # Kiosks: collapse duplicates within 15 m, then fill long gaps procedurally.
    kiosks.sort(key=lambda k: k["i"])
    dedup = []
    for k in kiosks:
        if not dedup or cl.s[k["i"]] - cl.s[dedup[-1]["i"]] > 15.0:
            dedup.append(k)
    kcfg = cfg["kiosk"]
    stations = [cl.s[0]] + [cl.s[k["i"]] for k in dedup] + [cl.s[-1]]
    filled = list(dedup)
    for a, b in zip(stations[:-1], stations[1:]):
        n_add = int((b - a) // kcfg["fill_gap_m"])
        for k in range(1, n_add + 1):
            st = a + (b - a) * k / (n_add + 1)
            i = int(np.searchsorted(cl.s, st).clip(0, len(u) - 1))
            filled.append({"i": i, "offset": None, "name": "", "source": "procedural"})
    for k in filled:  # kiosks stand on the sand just beyond the calcadao edge
        k["offset"] = float(sand_edge[k["i"]] + kcfg["offset_from_sand_edge_m"])
    for p in postos:
        p["offset"] = float(np.clip(p["offset"], sand_edge[p["i"]], waterline[p["i"]] - 10.0))
    return postos, sorted(filled, key=lambda k: k["i"])


# --------------------------------------------------------------------------------------------- meshes
class TiledMesh:
    """Per-tile MeshBuilders keyed by (i, j); geometry is passed in UTM and re-pivoted per tile."""

    def __init__(self, bbox, tile_m, make_builder):
        self.W, self.S, E, N = bbox
        self.tile_m = tile_m
        self.ntx, self.nty = int(round((E - self.W) / tile_m)), int(round((N - self.S) / tile_m))
        self.tiles, self.make = {}, make_builder

    def key(self, e, n):
        return (int(np.clip((e - self.W) // self.tile_m, 0, self.ntx - 1)),
                int(np.clip((n - self.S) // self.tile_m, 0, self.nty - 1)))

    def pivot(self, key):
        return np.array([self.W + key[0] * self.tile_m, self.S + key[1] * self.tile_m, 0.0])

    def builder(self, key):
        if key not in self.tiles:
            self.tiles[key] = self.make()
        return self.tiles[key]

    def add_grid(self, material, V):
        """V (n_s, k, 3): vertex grid along station x across (inner -> seaward). Quads are split by tile on
        their centroid; winding gives +Z normals for an along-W->E, across-seaward grid."""
        ns, nk, _ = V.shape
        idx = np.arange(ns * nk).reshape(ns, nk)
        a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
        c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
        flat = V.reshape(-1, 3)
        cen = (flat[a] + flat[c]) / 2
        keys = [self.key(e, n) for e, n in cen[:, :2]]
        groups = {}
        for q, k in enumerate(keys):
            groups.setdefault(k, []).append(q)
        for k, qs in groups.items():
            qs = np.array(qs)
            quads = np.column_stack([a[qs], b[qs], c[qs], d[qs]])
            used, inv = np.unique(quads, return_inverse=True)
            inv = inv.reshape(-1, 4)
            tris = np.vstack([inv[:, [0, 1, 2]], inv[:, [0, 2, 3]]])
            self.builder(k).add(material, flat[used] - self.pivot(k), tris)

    def add_object(self, material, verts, tris, anchor):
        k = self.key(*anchor[:2])
        self.builder(k).add(material, np.asarray(verts) - self.pivot(k), tris)


def box(center, yaw, size, z0, z1):
    """Closed-top box (no bottom). Each face has its own vertices, CCW seen from outside."""
    sx, sy = size[0] / 2, size[1] / 2
    c, s = np.cos(yaw), np.sin(yaw)
    R = np.array([[c, -s], [s, c]])
    corners = (np.array([[-sx, -sy], [sx, -sy], [sx, sy], [-sx, sy]]) @ R.T) + center[:2]
    verts, tris = [], []

    def quad(p):
        o = len(verts)
        verts.extend(p)
        tris.extend([(o, o + 1, o + 2), (o, o + 2, o + 3)])

    for k in range(4):
        (ax, ay), (bx, by) = corners[k], corners[(k + 1) % 4]
        quad([(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)])
    quad([(x, y, z1) for x, y in corners])
    return np.array(verts), np.array(tris)


def palm(base, height, lean_dir, cfg, seed):
    """Low-poly palm: tapered hexagonal trunk with a slight lean, crown of drooping double-sided fronds.
    Returns {material: (verts, tris)}."""
    sides, r0, r1 = 6, 0.22, 0.15
    lean = lean_dir * height * 0.06
    top = np.array([base[0] + lean[0], base[1] + lean[1], base[2] + height])
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring0 = np.column_stack([base[0] + r0 * np.cos(ang), base[1] + r0 * np.sin(ang), np.full(sides, base[2] - 0.3)])
    ring1 = np.column_stack([top[0] + r1 * np.cos(ang), top[1] + r1 * np.sin(ang), np.full(sides, top[2])])
    tv = np.vstack([ring0, ring1])
    tt = []
    for k in range(sides):
        a, b = k, (k + 1) % sides
        tt += [(a, b, sides + b), (a, sides + b, sides + a)]
    fv, ft = [], []
    L = cfg["frond_length_m"]
    for k in range(cfg["fronds"]):
        th = 2 * np.pi * (k + stable_unit((seed, k)) * 0.4) / cfg["fronds"]
        d = np.array([np.cos(th), np.sin(th)])
        side = np.array([-d[1], d[0]]) * 0.6
        mid = top + np.r_[d * L * 0.55, -0.4]  # slight arch above the top-tip line, near-flat leaf
        tip = top + np.r_[d * L, -1.1]
        o = len(fv)
        fv += [top, mid + np.r_[side, 0], tip, mid - np.r_[side, 0]]
        ft += [(o, o + 1, o + 2), (o, o + 2, o + 3), (o, o + 2, o + 1), (o, o + 3, o + 2)]  # both faces
    return {"M_PalmTrunk": (tv, np.array(tt)), "M_PalmLeaf": (np.array(fv), np.array(ft))}


def build_geometry(cl, prof, terrain, tm, cfg, postos, kiosks):
    """Fill the TiledMesh with the beach. terrain: Heightfield of the generated terrain (for draping) or None.
    Returns per-element statistics for the report."""
    u = cl.u
    cic_c = profile_at(prof, u, "ciclovia_centre")
    sand_edge = profile_at(prof, u, "sand_edge")
    water = profile_at(prof, u, "waterline")
    canal = np.zeros(len(u), bool)
    for u0, u1 in prof["canal"]:
        canal |= (u >= u0) & (u <= u1)
    kerb = cl.hw
    wmin, wmax = cfg["ciclovia_width_m"]
    has_cic = np.zeros(len(u), bool)
    for u0, u1 in prof.get("ciclovia_spans", [[0.0, 1.0]]):
        has_cic |= (u >= u0) & (u <= u1)
    cic_w = np.where(has_cic, np.clip(2 * (cic_c - kerb), wmin, wmax), 0.0)
    cic_out = kerb + cic_w
    calc_out = np.clip(sand_edge, cic_out + cfg["calcadao_min_width_m"], cic_out + cfg["calcadao_max_width_m"])
    z_cic = cl.z + cfg["ciclovia_lift_m"]
    z_calc = cl.z + cfg["calcadao_lift_m"]
    stats = {}

    def strip(material, inner, outer, z, rows=None):
        rows = np.ones(len(u), bool) if rows is None else rows
        for a, b in _runs(rows):
            sl = slice(a, b + 1)
            V = np.stack([np.column_stack([cl.at(inner)[sl], z[sl]]), np.column_stack([cl.at(outer)[sl], z[sl]])],
                         axis=1)
            tm.add_grid(material, V)
            # skirts: vertical faces down from both edges hide gaps against terrain / the lower sand
            for edge, sign in ((0, -1), (1, 1)):
                top = V[:, edge, :]
                bot = top - [0, 0, cfg["skirt_m"]]
                # add_grid's winding gives normal = along x across; across bot->top faces inland
                S = np.stack([top, bot], axis=1) if sign > 0 else np.stack([bot, top], axis=1)
                tm.add_grid(material, S)

    strip("M_Ciclovia", kerb, cic_out, z_cic, rows=has_cic)
    strip("M_Calcadao", cic_out, calc_out, z_calc)
    stats["ciclovia_width_m"] = _q(cic_w[has_cic]) if has_cic.any() else None
    stats["ciclovia_length_m"] = round(float(has_cic.mean() * cl.length), 1)
    stats["calcadao_width_m"] = _q(calc_out - cic_out)
    if terrain is not None:  # positive = terrain pokes through the promenade surface
        mid = cl.at((cic_out + calc_out) / 2)
        stats["terrain_above_calcadao_m"] = _q(terrain.sample(mid[:, 0], mid[:, 1]) - z_calc)

    # Sand: a graded cross-section from just below the calcadao edge to below the sea plane.
    water = np.maximum(water, calc_out + 5.0)
    end = water + cfg["sand_beyond_waterline_m"]
    k = int(np.ceil((end - calc_out).max() / cfg["sand_cross_step_m"])) + 1
    f = np.linspace(0, 1, k)
    off = calc_out[:, None] + (end - calc_out)[:, None] * f[None, :]
    z0 = (z_calc - cfg["sand_drop_m"])[:, None]
    frac_water = ((water - calc_out) / (end - calc_out))[:, None]
    # linear to 0 m at the waterline, then on to sand_end_depth_m (hidden under the sea plane)
    zs = np.where(f[None, :] <= frac_water, z0 * (1 - f[None, :] / frac_water),
                  cfg["sand_end_depth_m"] * (f[None, :] - frac_water) / (1 - frac_water))
    P = cl.xy[:, None, :] + cl.nrm[:, None, :] * off[:, :, None]
    draped = 0
    if terrain is not None:
        tz = terrain.sample(P[..., 0].ravel(), P[..., 1].ravel()).reshape(zs.shape)
        lift = (tz + cfg["terrain_clearance_m"] > zs) & (zs > -0.05)
        draped = float(lift[~canal].mean())
        zs = np.where(lift, tz + cfg["terrain_clearance_m"], zs)
    V = np.concatenate([P, zs[..., None]], axis=2)
    for a, b in _runs(~canal):
        tm.add_grid("M_Sand", V[a:b + 1])
    # Canal mouth: water surface across the sand band, slightly below the sea plane's level of detail.
    for a, b in _runs(canal):
        sl = slice(max(a - 1, 0), b + 2)
        Wv = np.concatenate([P[sl], np.full(P[sl].shape[:2] + (1,), 0.02)], axis=2)
        tm.add_grid("M_Sea", Wv[:, ::max(1, k // 4)])
    stats["sand_width_to_waterline_m"] = _q(water - calc_out)
    stats["sand_draped_fraction"] = round(draped, 3)
    stats["canal_stations"] = int(canal.sum())

    # Postos
    pc = cfg["posto"]
    for p in postos:
        i = p["i"]
        p["offset"] = float(np.clip(p["offset"], calc_out[i] + pc["base_m"][1] / 2, water[i] - 10.0))
        c = cl.at(p["offset"])[i]
        zg = _sand_z(V, off, i, p["offset"])
        yaw = np.arctan2(cl.t[i, 1], cl.t[i, 0])
        for mat, (v, t) in (("M_Posto", box(c, yaw, pc["base_m"][:2], zg - 0.3, zg + pc["base_m"][2])),
                            ("M_Posto", box(c, yaw, (pc["column_m"], pc["column_m"]), zg, zg + pc["cabin_floor_m"])),
                            ("M_PostoCabin", box(c, yaw, pc["cabin_m"][:2], zg + pc["cabin_floor_m"],
                                                 zg + pc["cabin_floor_m"] + pc["cabin_m"][2]))):
            tm.add_object(mat, v, t, c)
        p["xyz"] = [round(float(c[0]), 2), round(float(c[1]), 2), round(float(zg), 2)]

    kc = cfg["kiosk"]
    for kq in kiosks:
        i = kq["i"]
        if canal[max(i - 5, 0):i + 6].any():
            kq["skipped"] = "canal"
            continue
        kq["offset"] = float(calc_out[i] + kc["offset_from_sand_edge_m"] + kc["roof_m"][1] / 2)
        c = cl.at(kq["offset"])[i]
        zg = _sand_z(V, off, i, kq["offset"])
        yaw = np.arctan2(cl.t[i, 1], cl.t[i, 0])
        v, t = box(c, yaw, kc["size_m"][:2], zg - 0.3, zg + kc["size_m"][2])
        tm.add_object("M_Kiosk", v, t, c)
        v, t = box(c, yaw, kc["roof_m"][:2], zg + kc["size_m"][2], zg + kc["size_m"][2] + kc["roof_m"][2])
        tm.add_object("M_KioskRoof", v, t, c)
        kq["xyz"] = [round(float(c[0]), 2), round(float(c[1]), 2), round(float(zg), 2)]

    # Palm row along the sea edge of the calcadao, clear of postos, kiosks and the canal.
    pcfg = cfg["palm"]
    blockers = np.array([cl.s[q["i"]] for q in postos + kiosks if "xyz" in q] or [-1e9])
    n_palms, s_val, palms_xy = 0, pcfg["spacing_m"] / 2, []
    while s_val < cl.length - pcfg["spacing_m"] / 2:
        st = s_val + (stable_unit(("palm", round(s_val))) - 0.5) * 2 * pcfg["jitter_m"]
        s_val += pcfg["spacing_m"]
        i = int(np.searchsorted(cl.s, st).clip(0, len(u) - 1))
        if canal[max(i - 4, 0):i + 5].any() or np.min(np.abs(blockers - st)) < pcfg["clear_m"]:
            continue
        o = calc_out[i] + pcfg["offset_from_sand_edge_m"]
        c = cl.at(o)[i]
        h = pcfg["height_m"][0] + stable_unit(("palm_h", round(st))) * (pcfg["height_m"][1] - pcfg["height_m"][0])
        zb = _sand_z(V, off, i, o)
        for mat, (v, t) in palm(np.r_[c, zb], h, cl.nrm[i], pcfg, round(st)).items():
            tm.add_object(mat, v, t, c)
        palms_xy.append(c)
        n_palms += 1
    stats["palms"] = n_palms
    # for the preview only (stripped from the report)
    stats["_debug"] = {"kerb": kerb, "cic_out": cic_out, "calc_out": calc_out, "water": water, "canal": canal,
                       "palms": np.array(palms_xy).reshape(-1, 2)}
    return stats


def _sand_z(V, off, i, o):
    return float(np.interp(o, off[i], V[i, :, 2]))


def _runs(mask):
    runs, start = [], None
    for k, m in enumerate(np.r_[mask, False]):
        if m and start is None:
            start = k
        elif not m and start is not None:
            if k - 1 > start:
                runs.append((start, k - 1))
            start = None
    return runs


def _q(v):
    v = np.asarray(v)
    return {"min": round(float(v.min()), 2), "median": round(float(np.median(v)), 2), "max": round(float(v.max()), 2)}
