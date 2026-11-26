"""Landmark forms for the registry in config `landmarks.buildings` (used by scripts/build_area.py).

Plan size always comes from the OSM outline; the registry gives height and form. All functions return a list of
(material, verts, tris) in absolute UTM metres (z = metres above sea level); the caller subtracts the tile pivot.

  cone    truncated cone: base = the outline's circumscribed circle around its centroid, top_diameter_m at height
          (Catedral Metropolitana).
  arcade  aqueduct along the outline's long axis: `tiers` levels of piers with square-topped openings and a level
          top (Arcos da Lapa). Piers stand on the local terrain; the top is level at min(ground) + height.
  dome    a drum and a low dome on top of the box, centred on the outline, reaching dome.top_m above the ground
          (Candelaria, CCBB rotunda).
"""
import numpy as np

from .places import oriented_axes


def _ring(c, r, z, n):
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([c[0] + r * np.cos(a), c[1] + r * np.sin(a), np.full(n, z)])


def frustum(c, r0, r1, z0, z1, n=32, cap=True):
    """Closed side + top of a truncated cone / cylinder, CCW outward."""
    v = np.vstack([_ring(c, r0, z0, n), _ring(c, r1, z1, n)])
    t = []
    for a in range(n):
        b = (a + 1) % n
        t += [(a, b, n + b), (a, n + b, n + a)]
    if cap:
        top = len(v)
        v = np.vstack([v, [[c[0], c[1], z1]]])
        t += [(n + a, n + (a + 1) % n, top) for a in range(n)]
    return v, np.array(t)


def dome(c, z0, r, n=24, rings=5):
    """Low dome (height 0.55 r) from z0, CCW outward."""
    h = 0.55 * r
    prof = [(r * np.cos(a), z0 + h * np.sin(a)) for a in np.linspace(0, np.pi / 2, rings + 1)[:-1]]
    v = np.vstack([_ring(c, rr, zz, n) for rr, zz in prof] + [[[c[0], c[1], z0 + h]]])
    t = []
    for k in range(len(prof) - 1):
        for a in range(n):
            b = (a + 1) % n
            p, q = k * n, (k + 1) * n
            t += [(p + a, p + b, q + b), (p + a, q + b, q + a)]
    last, top = (len(prof) - 1) * n, len(v) - 1
    t += [(last + a, last + (a + 1) % n, top) for a in range(n)]
    return v, np.array(t)


def box(c, axis, length, width, z0, z1):
    """Closed-top box oriented along `axis` (unit xy), CCW outward."""
    d = np.asarray(axis, float)
    nrm = np.array([-d[1], d[0]])
    hl, hw = length / 2, width / 2
    corners = [c - d * hl - nrm * hw, c + d * hl - nrm * hw, c + d * hl + nrm * hw, c - d * hl + nrm * hw]
    v, t = [], []
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        o = len(v)
        v += [(*a, z0), (*b, z0), (*b, z1), (*a, z1)]
        t += [(o, o + 1, o + 2), (o, o + 2, o + 3)]
    o = len(v)
    v += [(*p, z1) for p in corners]
    t += [(o, o + 1, o + 2), (o, o + 2, o + 3)]
    return np.array(v), np.array(t)


def cone(poly, ground, entry):
    c = np.asarray(poly.centroid.coords[0])
    ring = np.asarray(poly.exterior.coords)
    r0 = float(np.hypot(*(ring[:, :2] - c).T).max())
    z0 = float(ground.min()) - 0.5
    v, t = frustum(c, r0, entry["top_diameter_m"] / 2, z0, float(ground.max()) + entry["height_m"], n=48)
    return [(entry["material"], v, t)]


def arcade(poly, terrain, entry, pier_m=3.0, open_frac=0.78):
    """Aqueduct: `tiers` rows of piers + a spandrel band per tier; level top at min(ground) + height."""
    centre, axis, length, width = oriented_axes(poly)
    n = int(entry["arches_per_tier"])
    tiers = int(entry["tiers"])
    xs = np.linspace(-length / 2, length / 2, n + 1)
    pts = centre + np.outer(xs, axis)
    zg = terrain.sample(pts[:, 0], pts[:, 1])
    top = float(zg.min()) + entry["height_m"]
    levels = np.linspace(float(zg.min()), top, tiers + 1)
    mat, parts = entry["material"], []
    bay = length / n
    for k in range(tiers):
        lo, hi = levels[k], levels[k + 1]
        spring = lo + (hi - lo) * open_frac  # top of the openings
        for p, z in zip(pts, zg):  # piers
            if spring - max(lo, z) > 0.3:
                parts.append((mat, *box(p, axis, pier_m, width, max(lo, z) - (0.5 if k == 0 else 0.0), spring)))
        for i in range(n):  # spandrel band over each opening (full where the ground is above the opening)
            c = (pts[i] + pts[i + 1]) / 2
            zlo = max(spring, min(zg[i], zg[i + 1]) - 0.5) if k == 0 else spring
            if hi - zlo > 0.05:
                parts.append((mat, *box(c, axis, bay + 0.02, width, zlo, hi)))
    return parts


def drum_dome(poly, ground_top, ground_min, entry):
    """Drum from the roof up to where a dome of radius_m ends at dome.top_m above the lowest ground."""
    d = entry["dome"]
    c = np.asarray(poly.representative_point().coords[0])
    r = d["radius_m"]
    z_top = ground_min + d["top_m"]
    z_spring = z_top - 0.55 * r
    parts = []
    if z_spring > ground_top + 0.2:
        parts.append((entry["material"], *frustum(c, r, r, ground_top - 0.1, z_spring, n=24, cap=False)))
    parts.append((entry["material"], *dome(c, max(z_spring, ground_top), r)))
    return parts


def osm_id_str(v):
    """'30621926' for 30621926, 30621926.0 or '30621926' (ids turn into floats once NaN rows are concatenated)."""
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v)


def registry(cfg):
    """{osm_id (str): entry} from config `landmarks.buildings`."""
    return {str(e["osm_id"]): e for e in cfg.get("buildings", [])}
