"""Stepped stair meshes along a 3D polyline, plus the terrain ramp under designed stairs.

One generator serves both
  * designed hub stairs (config `places.hubs[].stairs`: a 2D path whose end heights come from the terrace / road
    / terrain they touch, z linear in between, rise = dz / ceil(dz / max_rise) so every riser is equal), and
  * OSM highway=steps (the solved road profile, fixed rise).

The profile is resampled finely and its height quantised into levels `rise` apart (with hysteresis, so a nearly
flat run does not flicker between two levels). Each run of constant level becomes one tread: a solid block whose
top is the highest profile point in the run (the ramp / carved terrain under it never pokes through) and whose
base reaches below the lowest. Long treads (flat stretches, landings) are split so curved paths stay on the path.
"""
import numpy as np
import shapely
from shapely.geometry import LineString

from .beach import box


def resample(profile, spacing=0.05):
    """(s, x, y, z) arrays along a 3D polyline at ~spacing metres."""
    p = np.asarray(profile, dtype=float)
    seg = np.hypot(*(p[1:, :2] - p[:-1, :2]).T)
    s = np.r_[0.0, np.cumsum(seg)]
    if s[-1] <= 0:
        return None
    ss = np.linspace(0.0, s[-1], max(2, int(np.ceil(s[-1] / spacing)) + 1))
    return ss, np.interp(ss, s, p[:, 0]), np.interp(ss, s, p[:, 1]), np.interp(ss, s, p[:, 2])


def levels(z, rise, hysteresis=0.6):
    """Level index per sample: changes only once z is more than hysteresis * rise from the current level."""
    k = np.empty(len(z), dtype=np.int64)
    cur = 0
    z0 = z[0]
    for i, v in enumerate(z):
        while v - (z0 + cur * rise) > hysteresis * rise:
            cur += 1
        while (z0 + cur * rise) - v > hysteresis * rise:
            cur -= 1
        k[i] = cur
    return k


def step_blocks(profile, width, rise, max_tread=1.5, depth=0.35):
    """Solid tread blocks along a 3D polyline. Returns ([(verts, tris)], stats) in world coordinates."""
    r = resample(profile)
    if r is None:
        return [], {"steps": 0, "min_tread_m": None}
    s, x, y, z = r
    k = levels(z, rise)
    cut = np.flatnonzero(np.diff(k)) + 1
    starts, ends = np.r_[0, cut], np.r_[cut, len(s)]
    parts, treads = [], []
    for a, b in zip(starts, ends):
        b = min(b, len(s) - 1) if b > a + 1 else min(a + 1, len(s) - 1)
        top = float(z[a:b + 1].max())
        base = float(z[a:b + 1].min()) - depth
        L = s[b] - s[a]
        if L <= 1e-3:
            continue
        treads.append(L)
        n = max(1, int(np.ceil(L / max_tread)))
        for j in range(n):
            ia = a + int(round((b - a) * j / n))
            ib = a + int(round((b - a) * (j + 1) / n))
            pa, pb = np.array([x[ia], y[ia]]), np.array([x[ib], y[ib]])
            chord = np.linalg.norm(pb - pa)
            if chord < 1e-3:
                continue
            yaw = float(np.arctan2(*(pb - pa)[::-1]))
            parts.append(box((pa + pb) / 2, yaw, (chord + 0.02, width), base, top))
    inner = treads[1:-1] if len(treads) > 2 else treads
    return parts, {"steps": max(0, len(treads) - 1), "min_tread_m": round(min(inner), 2) if inner else None}


def cheek_walls(profile, width, rise, height=0.9, thickness=0.2):
    """Low stepped walls on both sides of a designed stair (one block per tread, following the treads)."""
    r = resample(profile)
    if r is None:
        return []
    s, x, y, z = r
    t = np.gradient(np.column_stack([x, y]), axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    nrm = np.column_stack([-t[:, 1], t[:, 0]])
    out = []
    for side in (-1, 1):
        off = side * (width / 2 + thickness / 2)
        prof = np.column_stack([x + nrm[:, 0] * off, y + nrm[:, 1] * off, z])[::4]
        blocks, _ = step_blocks(prof, thickness, rise, depth=0.35)
        for v, tr in blocks:
            v = np.asarray(v, dtype=float).copy()
            top = v[:, 2].max()
            v[v[:, 2] >= top - 1e-6, 2] = top + height
            out.append((v, tr))
    return out


def side_walls(profile, width, terrain, out_m=1.2, min_diff=0.6, step=0.5):
    """Retaining walls along both sides of a designed stair wherever the ground just outside stands more than
    min_diff above the stair line (a stair cut into the slope). Returns [(verts, tris)] (double-sided quads)."""
    r = resample(profile, step)
    if r is None:
        return []
    s, x, y, z = r
    t = np.gradient(np.column_stack([x, y]), axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    nrm = np.column_stack([-t[:, 1], t[:, 0]])
    out = []
    for side in (-1, 1):
        edge = np.column_stack([x, y]) + nrm * side * width / 2
        probe = np.column_stack([x, y]) + nrm * side * (width / 2 + out_m)
        zo = terrain.sample(probe[:, 0], probe[:, 1])
        for k in range(len(s) - 1):
            lo = min(z[k], z[k + 1]) - 0.3
            hi = max(zo[k], zo[k + 1])
            if hi - max(z[k], z[k + 1]) < min_diff:
                continue
            a, b = edge[k], edge[k + 1]
            v = np.array([(a[0], a[1], lo), (b[0], b[1], lo), (b[0], b[1], hi), (a[0], a[1], hi)])
            out.append((v, np.array([(0, 1, 2), (0, 2, 3), (0, 2, 1), (0, 3, 2)])))
    return out


def stair_rise(dz, max_rise=0.17):
    """Equal risers for a designed stair: dz split into ceil(dz / max_rise) steps."""
    n = max(1, int(np.ceil(abs(dz) / max_rise)))
    return abs(dz) / n if dz else max_rise, n


def corridor(path, width):
    return LineString(path).buffer(width / 2, cap_style="flat", join_style="mitre")


def apply_ramp(hf, profile, width, sink=0.25, blend_m=0.6, protect=None):
    """Set the heightfield under a designed stair to its line (minus sink, so the solid treads cover it) and blend
    back over blend_m. Cells inside `protect` (terraces: flat pitches / pracas) are never changed. In place."""
    line = LineString(np.asarray(profile)[:, :2])
    p = np.asarray(profile, dtype=float)
    s_pts = np.r_[0.0, np.cumsum(np.hypot(*(p[1:, :2] - p[:-1, :2]).T))]
    poly = line.buffer(width / 2 + 0.3, cap_style="flat", join_style="mitre")
    w, s_, e, n = poly.buffer(blend_m + hf.step).bounds
    xs, ys = hf.coords()
    ix = np.flatnonzero((xs >= w) & (xs <= e))
    iy = np.flatnonzero((ys >= s_) & (ys <= n))
    if not len(ix) or not len(iy):
        return 0
    X, Y = np.meshgrid(xs[ix], ys[iy])
    pts = shapely.points(X, Y)
    d = shapely.distance(poly, pts)
    along = shapely.line_locate_point(line, pts)
    zl = np.interp(along, s_pts, p[:, 2]) - sink
    wgt = np.clip(1 - d / max(blend_m, 1e-6), 0, 1)
    # Do not ramp beyond the stair ends (the flat cap): only cells whose projection falls inside the line
    wgt = np.where((along <= 1e-6) | (along >= line.length - 1e-6), np.where(d <= 0, wgt, 0.0), wgt)
    if protect is not None and not protect.is_empty:
        wgt = np.where(shapely.contains_xy(protect, X, Y), 0.0, wgt)
    sub = hf.z[np.ix_(iy, ix)]
    hf.z[np.ix_(iy, ix)] = sub * (1 - wgt) + zl * wgt
    return int((wgt > 0).sum())
