"""Roads: OSM edges -> classified centrelines with realistic widths -> smoothed 3D profiles -> ribbons."""
import re

import numpy as np
import pandas as pd
from shapely.geometry import LineString, box

# Rank decides which road wins where corridors overlap; group decides material and corridor shape.
CLASS_INFO = {
    "motorway": (10, "major"), "trunk": (10, "major"), "primary": (9, "major"), "secondary": (8, "major"),
    "motorway_link": (7, "major"), "trunk_link": (7, "major"), "primary_link": (7, "major"),
    "secondary_link": (6, "major"), "tertiary": (6, "minor"), "tertiary_link": (5, "minor"),
    "unclassified": (5, "minor"), "residential": (5, "minor"), "living_street": (4, "minor"),
    "service": (3, "minor"), "pedestrian": (3, "path"), "track": (2, "path"), "cycleway": (2, "path"),
    "footway": (1, "path"), "path": (1, "path"), "steps": (1, "steps"),
}
CORRIDOR = {  # shoulder (flattened beyond the kerb) and blend (back to natural ground), metres
    "major": (1.5, 10.0), "minor": (1.0, 7.0), "path": (0.3, 2.5), "steps": (0.3, 2.0),
}
MATERIAL = {"major": "M_RoadMajor", "minor": "M_RoadMinor", "path": "M_Path", "steps": "M_Steps"}


def _first(value):
    return str(value).split(";")[0].strip() if value is not None and str(value) != "nan" else None


def _number(value):
    if value is None or str(value) == "nan":
        return None
    nums = [float(m) for m in re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", "."))]
    return max(nums) if nums else None


def road_width(row, cfg):
    cls = row["cls"]
    w = _number(row.get("width"))
    if w is None:
        lanes = _number(row.get("lanes"))
        if lanes and CLASS_INFO[cls][1] in ("major", "minor"):
            w = lanes * cfg["lane_width_m"] + cfg["lane_extra_m"]
        else:
            w = cfg["class_width_m"][cls]
            if str(row.get("oneway")) in ("True", "true", "yes", "1") and CLASS_INFO[cls][1] != "path":
                w *= 0.6  # one carriageway of a dual road
    return float(np.clip(w, cfg["min_width_m"], cfg["max_width_m"]))


def prepare_edges(edges, bbox, cfg):
    """Classify, size and clip edges. Returns (surface_edges, skipped) where skipped lists tunnels."""
    edges = edges.copy()
    edges["cls"] = edges["highway"].map(_first)
    edges = edges[edges["cls"].isin(CLASS_INFO)]
    tunnel = edges.get("tunnel").map(_first).isin(["yes", "culvert"]) if "tunnel" in edges else False
    bridge = edges.get("bridge").map(_first).isin(["yes", "viaduct"]) if "bridge" in edges else False
    edges["is_bridge"] = bridge
    skipped = edges[tunnel]
    edges = edges[~tunnel].copy()
    edges["group"] = edges["cls"].map(lambda c: CLASS_INFO[c][1])
    edges["rank"] = edges["cls"].map(lambda c: CLASS_INFO[c][0])
    edges["width_m"] = [road_width(r, cfg) for _, r in edges.iterrows()]
    # OSMnx stores both directions of a two-way street; keep one geometry per undirected edge.
    und = [(min(u, v), max(u, v), k, round(L, 1)) for u, v, k, L in
           zip(edges["u"], edges["v"], edges["key"], edges["length"])]
    edges = edges[~pd.Series(und, index=edges.index).duplicated()]
    clip = box(*bbox)
    edges["geometry"] = edges.geometry.intersection(clip)
    edges = edges[~edges.geometry.is_empty]
    edges = edges.explode(index_parts=False)
    edges = edges[edges.geometry.geom_type == "LineString"]
    return edges.reset_index(drop=True), skipped


def densify(line, spacing):
    n = max(2, int(np.ceil(line.length / spacing)) + 1)
    d = np.linspace(0, line.length, n)
    pts = np.array([line.interpolate(x).coords[0][:2] for x in d])
    return pts, d


# Vertical-alignment solve, per tier. Smoothing length ~ spacing * lambda2 ** 0.25.
SOLVE_TIERS = [("major",), ("minor",), ("path", "steps")]
SMOOTH_LAMBDA2 = {"major": 5e4, "minor": 1e4, "path": 40.0, "steps": 10.0}


def solve_profiles(edges, nodes_xy, ground_fn, fixed_nodes, cfg, spacing=2.0, iterations=12):
    """Least-squares vertical alignment of one tier of the network.

    Unknowns are the heights of every ~2 m centreline sample; samples at a graph node are merged into one
    unknown so intersections are exact. Energy = data (follow ground_fn) + curvature (second differences,
    class-dependent stiffness) + slope penalty. Segments steeper than the class limit get their slope
    penalty raised and the system is re-solved (iteratively reweighted), so excess relief becomes cut/fill.
    Nodes already solved by a higher tier enter as heavily weighted constraints.
    Returns (profiles, node_heights).
    """
    from scipy import sparse
    from scipy.sparse.linalg import spsolve

    var_of_node, ids, samples = {}, [], []
    nvar = 0
    for _, r in edges.iterrows():
        pts, d = densify(r.geometry, spacing)
        idx = np.arange(nvar, nvar + len(pts))
        nvar += len(pts)
        for end, node in ((0, r["u"]), (-1, r["v"])):
            nxy = nodes_xy.get(node)
            if nxy is not None and np.hypot(*(np.array(nxy) - pts[end])) < 1.0:
                idx[end] = var_of_node.setdefault(node, idx[end])
        ids.append(idx)
        samples.append((pts, d))
    # Merging endpoints into node unknowns leaves unused indices; compact them so the system is non-singular.
    used, inverse = np.unique(np.concatenate(ids), return_inverse=True)
    splits = np.cumsum([len(i) for i in ids])[:-1]
    ids = np.split(inverse, splits)
    remap = {old: new for new, old in enumerate(used)}
    var_of_node = {node: remap[v] for node, v in var_of_node.items()}
    nvar = len(used)
    g = np.zeros(nvar)
    for idx, (pts, _) in zip(ids, samples):
        g[idx] = ground_fn(pts[:, 0], pts[:, 1])
    # Coastal roads must never follow the synthetic seabed below the shoreline.
    g = np.maximum(g, cfg["min_height_m"])

    rows, cols, vals, rhs = [], [], [], []

    def eq(terms, b):
        r = len(rhs)
        for c, v in terms:
            rows.append(r), cols.append(c), vals.append(v)
        rhs.append(b)

    seg_list = []
    for (_, r), idx, (pts, d) in zip(edges.iterrows(), ids, samples):
        data_idx = idx[[0, -1]] if r["is_bridge"] else idx
        for i in data_idx:
            eq([(i, 1.0)], g[i])
        lam = np.sqrt(SMOOTH_LAMBDA2[r["group"]])
        for k in range(1, len(idx) - 1):
            eq([(idx[k - 1], lam), (idx[k], -2 * lam), (idx[k + 1], lam)], 0.0)
        limit = cfg["max_grade"].get(r["group"])
        for k in range(len(idx) - 1):
            seg_list.append((idx[k], idx[k + 1], max(d[k + 1] - d[k], 1e-3), limit))
    for node, z in fixed_nodes.items():
        if node in var_of_node:
            eq([(var_of_node[node], 100.0)], 100.0 * z)
    base = sparse.csr_matrix((vals, (rows, cols)), shape=(len(rhs), nvar))
    b0 = np.array(rhs)
    seg = np.array([(a, c, ds, lim if lim else np.inf) for a, c, ds, lim in seg_list])
    a_i, c_i, ds, lim = seg[:, 0].astype(int), seg[:, 1].astype(int), seg[:, 2], seg[:, 3]
    w_slope = np.full(len(seg), 1e-2)
    for _ in range(iterations):
        slope_rows = sparse.csr_matrix(
            (np.r_[w_slope, -w_slope] / np.r_[ds, ds],
             (np.r_[np.arange(len(seg)), np.arange(len(seg))], np.r_[c_i, a_i])), shape=(len(seg), nvar))
        A = sparse.vstack([base, slope_rows]).tocsr()
        b = np.r_[b0, np.zeros(len(seg))]
        z = spsolve((A.T @ A).tocsc(), A.T @ b)
        grade = np.abs(z[c_i] - z[a_i]) / ds
        over = grade > lim * 0.98
        if not over.any():
            break
        w_slope[over] = np.minimum(w_slope[over] * 6.0, 1e4)

    profiles = [np.column_stack([pts, z[idx]]) for idx, (pts, _) in zip(ids, samples)]
    return profiles, {node: float(z[v]) for node, v in var_of_node.items()}


def max_grade(profile, window_m=10.0, spacing=2.0):
    k = max(1, int(window_m / spacing))
    if len(profile) <= k:
        return 0.0
    dz = profile[k:, 2] - profile[:-k, 2]
    ds = np.hypot(*(profile[k:, :2] - profile[:-k, :2]).T)
    return float(np.max(np.abs(dz) / np.maximum(ds, 1e-6)))


def ribbon(profile, half_width, lift, skirt=0.5):
    """Road surface strip plus vertical skirts on both edges. Returns verts, tris, normals."""
    p = profile[:, :2]
    t = np.gradient(p, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    nrm = np.column_stack([-t[:, 1], t[:, 0]])
    z = profile[:, 2] + lift
    L = np.column_stack([p + nrm * half_width, z])
    R = np.column_stack([p - nrm * half_width, z])
    n = len(p)
    up = np.tile([0.0, 0.0, 1.0], (2 * n, 1))
    verts = [np.vstack([R, L])]
    normals = [up]
    i = np.arange(n - 1)
    tris = [np.column_stack([i, i + 1, n + i + 1]), np.column_stack([i, n + i + 1, n + i])]
    base = 2 * n
    for edge, sign in ((R, -1.0), (L, 1.0)):
        out = np.column_stack([nrm * sign, np.zeros(n)])
        bottom = edge - [0, 0, skirt]
        verts += [edge, bottom]
        normals += [out, out]
        top_i, bot_i = base + i, base + n + i
        if sign < 0:
            tris += [np.column_stack([bot_i, bot_i + 1, top_i + 1]), np.column_stack([bot_i, top_i + 1, top_i])]
        else:
            tris += [np.column_stack([bot_i + 1, bot_i, top_i]), np.column_stack([bot_i + 1, top_i, top_i + 1])]
        base += 2 * n
    return np.vstack(verts), np.vstack(tris), np.vstack(normals)


def corridor_samples(edges, profiles, cfg):
    rows = []
    for (_, r), prof in zip(edges.iterrows(), profiles):
        if r["is_bridge"]:
            continue
        shoulder, blend = CORRIDOR[r["group"]]
        for e, n, z in prof:
            rows.append((e, n, z - cfg["terrain_sink_m"], r["width_m"] / 2, shoulder, blend, r["rank"]))
    dtype = [("e", "f8"), ("n", "f8"), ("z", "f8"), ("half_width", "f8"), ("shoulder", "f8"),
             ("blend", "f8"), ("rank", "f8")]
    return np.array(rows, dtype=dtype)


def split_by_tile(profile, tile_of):
    """Split a polyline into runs whose segment midpoints fall in the same tile (shared boundary vertex)."""
    mids = (profile[1:, :2] + profile[:-1, :2]) / 2
    keys = [tile_of(e, n) for e, n in mids]
    runs, start = [], 0
    for k in range(1, len(keys) + 1):
        if k == len(keys) or keys[k] != keys[start]:
            runs.append((keys[start], profile[start:k + 1]))
            start = k
    return runs


def line_from_profile(profile):
    return LineString(profile[:, :2])
