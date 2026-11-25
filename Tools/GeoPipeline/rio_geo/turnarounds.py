"""Turning space at the dead ends of narrow favela lanes (config `roads.turnarounds`, called from build_area.py).

A one-car lane (3-4.5 m) that just stops leaves a driver nowhere to turn. Each such dead end inside a favela gets
a round turning bulb (a "largo") at its end: a disc of radius `radius_m` centred `beyond_m - radius_m` metres back
from the end node along the lane (so it reaches `beyond_m` past the end), tilted like the lane's last metres
(level across), so it continues the lane surface. The ground under it is graded to it (blending out over
`blend_m`), informal houses on it are dropped, and it is skipped (or tried smaller) where it would hit a formal
building, a hub terrace / designed stair / escadaria, or another street at a different height. Pads go to
turnarounds.json so build_details.py keeps poles off them.
"""
import numpy as np
import shapely
from shapely.geometry import Point, Polygon


def dead_ends(surf, profiles, nodes_xy, bbox, zone, cfg):
    """Candidate dead ends: [(edge index, node id, end point (x, y, z), outward unit tangent, grade, width)]."""
    drv = surf["group"].isin(["major", "minor"]).to_numpy() & ~surf["is_bridge"].to_numpy()
    deg = {}
    for i in np.flatnonzero(drv):
        for n in (surf.iloc[i]["u"], surf.iloc[i]["v"]):
            deg[n] = deg.get(n, 0) + 1
    W, S, E, N = bbox
    m = cfg["edge_margin_m"]
    out = []
    for i in np.flatnonzero(drv):
        r = surf.iloc[i]
        if r["cls"] not in cfg["classes"] or r["width_m"] > cfg["max_width_m"] or r.geometry.length < cfg["min_length_m"]:
            continue
        prof = np.asarray(profiles[i], float)
        for n in (r["u"], r["v"]):
            if deg.get(n) != 1 or n not in nodes_xy:
                continue
            nx, ny = nodes_xy[n]
            if not (W + m < nx < E - m and S + m < ny < N - m):
                continue
            d0, d1 = np.hypot(*(prof[0, :2] - (nx, ny))), np.hypot(*(prof[-1, :2] - (nx, ny)))
            if min(d0, d1) > 1.0:  # clipped / split edge: its end is not the node
                continue
            p = prof if d1 <= d0 else prof[::-1]  # oriented: ends at the dead end
            if zone is not None and not zone.contains(Point(*p[-1, :2])):
                continue
            s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(p[:, :2], axis=0).T))]
            back = np.interp(s[-1] - cfg["grade_from_m"], s, np.arange(len(s)))
            j = int(np.floor(back))
            q = p[j] + (p[min(j + 1, len(p) - 1)] - p[j]) * (back - j)
            t = p[-1, :2] - q[:2]
            L = float(np.hypot(*t))
            if L < 1.0:
                continue
            out.append((int(i), n, p[-1].copy(), t / L, float((p[-1, 2] - q[2]) / L), float(r["width_m"])))
    return out


def pad_for(end, t, grade, radius, beyond, segments=24):
    c = end[:2] + t * (beyond - radius)
    ang = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = c + radius * np.column_stack([np.cos(ang), np.sin(ang)])
    return {"center": c, "radius": radius, "end": end, "t": t, "grade": grade, "polygon": Polygon(ring)}


def plane_z(pad, x, y):
    e, t = pad["end"], pad["t"]
    return e[2] + pad["grade"] * ((np.asarray(x) - e[0]) * t[0] + (np.asarray(y) - e[1]) * t[1])


def site_pads(cands, surf, profiles, buildings, block, cfg):
    """Choose a radius per dead end (largest of cfg radii that fits). Returns (pads, informal building indices to
    drop, stats)."""
    lines = list(surf.geometry)
    drv = np.flatnonzero(surf["group"].isin(["major", "minor"]).to_numpy())
    formal = buildings[~buildings["informal"].astype(bool)]
    ftree = shapely.STRtree(list(formal.geometry))
    itree = shapely.STRtree(list(buildings.geometry))
    pads, drop, stats = [], set(), {"dead_ends": len(cands), "built": 0, "skipped": 0, "houses_dropped": 0}
    for i, n, end, t, g, w in cands:
        ok = None
        for radius in cfg["radius_m"]:
            pad = pad_for(end, t, g, radius, cfg["beyond_m"])
            poly = pad["polygon"]
            if block is not None and poly.intersects(block):
                continue
            if any(formal.geometry.iloc[k].intersection(poly).area > 0.5 for k in ftree.query(poly)):
                continue
            clash = False
            for k in drv:  # another street at a different height inside the pad
                if k == i or not lines[k].intersects(poly):
                    continue
                pr = np.asarray(profiles[k])
                inside = shapely.contains_xy(poly, pr[:, 0], pr[:, 1])
                if inside.any() and np.abs(pr[inside, 2] - plane_z(pad, pr[inside, 0], pr[inside, 1])).max() > 0.4:
                    clash = True
                    break
            if clash:
                continue
            ok = pad
            break
        if ok is None:
            stats["skipped"] += 1
            continue
        ok["edge"], ok["node"], ok["width"] = i, n, w
        pads.append(ok)
        for k in itree.query(ok["polygon"].buffer(cfg["house_clear_m"])):
            if buildings["informal"].iloc[k] and buildings.geometry.iloc[k].intersects(ok["polygon"].buffer(cfg["house_clear_m"])):
                drop.add(int(k))
    stats["built"], stats["houses_dropped"] = len(pads), len(drop)
    return pads, sorted(drop), stats


def apply_pad(hf, pad, sink, blend_m, protect=None):
    """Grade the heightfield under a pad to its plane (minus sink), blending back over blend_m. In place."""
    poly = pad["polygon"]
    w, s, e, n = poly.buffer(blend_m + hf.step).bounds
    xs, ys = hf.coords()
    ix = np.flatnonzero((xs >= w) & (xs <= e))
    iy = np.flatnonzero((ys >= s) & (ys <= n))
    if not len(ix) or not len(iy):
        return 0
    X, Y = np.meshgrid(xs[ix], ys[iy])
    d = np.hypot(X - pad["center"][0], Y - pad["center"][1]) - pad["radius"]
    wgt = np.clip(1 - np.maximum(d, 0) / max(blend_m, 1e-6), 0, 1)
    if protect is not None and not protect.is_empty:
        wgt = np.where(shapely.contains_xy(protect, X, Y), 0.0, wgt)
    target = plane_z(pad, X, Y) - sink
    sub = hf.z[np.ix_(iy, ix)]
    hf.z[np.ix_(iy, ix)] = sub * (1 - wgt) + target * wgt
    return int((wgt > 0).sum())


def pad_mesh(pad, lift, origin=(0.0, 0.0)):
    """Fan-triangulated disc on the pad plane: (verts, tris, normals), verts relative to origin."""
    ring = np.asarray(pad["polygon"].exterior.coords)[:-1]
    c = pad["center"]
    pts = np.vstack([c, ring])
    z = plane_z(pad, pts[:, 0], pts[:, 1]) + lift
    v = np.column_stack([pts[:, 0] - origin[0], pts[:, 1] - origin[1], z])
    m = len(ring)
    tris = [(0, 1 + k, 1 + (k + 1) % m) for k in range(m)]
    a = np.array([-pad["grade"] * pad["t"][0], -pad["grade"] * pad["t"][1], 1.0])
    a /= np.linalg.norm(a)
    return v, np.array(tris), np.tile(a, (len(v), 1))
