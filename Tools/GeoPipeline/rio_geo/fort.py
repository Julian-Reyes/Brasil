"""Forte de Copacabana: the landmark complex on the peninsula east of Posto 6 (build_places.py, kind Places).

build_area.py already places the buildings (the 1914 fortress as a low, massive concrete block with
the landmark registry (config `landmarks.buildings`); the museum grounds' buildings as low colonial blocks with `landmark_zones`) and
keeps the peninsula's relief (`headlands`). This module adds what makes it read as a fort:

  * the fortress roof: perimeter parapet, two large and two small gun cupolas along its long axis, guns aimed
    out to sea (from the museum grounds towards the tip). The roof itself is walkable (OSM maps footways there).
  * the perimeter wall along the land-facing edge of the museum grounds, open at the OSM gate / sally port /
    entrances and clear of the coastline and of buildings on the line.
  * lawns (landuse=grass inside the grounds) and the fishermen's pier at Posto 6 (man_made=pier nearby).

Cupola positions / sizes and the fortress height are blockout guesses, not surveyed: check against photos.
"""
import numpy as np
import shapely
from shapely.geometry import Point
from shapely.geometry.polygon import orient

from .beach import box
from .buildings import extrude
from .places import oriented_axes


def _dome(c, z0, r, sides=14, rings=4):
    """Low dome (height 0.55 r) on a 0.6 m drum, closed, CCW outward."""
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    verts, tris = [], []
    h = 0.55 * r
    prof = [(r, z0), (r, z0 + 0.6)] + [(r * np.cos(a), z0 + 0.6 + h * np.sin(a))
                                       for a in np.linspace(0, np.pi / 2, rings + 1)[1:-1]]
    for rr, zz in prof:
        verts += [(c[0] + rr * np.cos(a), c[1] + rr * np.sin(a), zz) for a in ang]
    for k in range(len(prof) - 1):
        for a in range(sides):
            b = (a + 1) % sides
            p, q = k * sides, (k + 1) * sides
            tris += [(p + a, p + b, q + b), (p + a, q + b, q + a)]
    top = len(verts)
    verts.append((c[0], c[1], z0 + 0.6 + h))
    last = (len(prof) - 1) * sides
    tris += [(last + a, last + (a + 1) % sides, top) for a in range(sides)]
    return np.array(verts), np.array(tris)


def build(areas, features, buildings, coast, terrain, tm, cfg, registry=None):
    """areas / features: forte.gpkg layers; buildings: buildings.gpkg; coast: coastline geometry or None;
    terrain: Heightfield; tm: places TiledMesh. Returns statistics (None if the fort is not in this AOI)."""
    fc = cfg["fort"]
    g = fc["grounds"]
    gsel = areas[areas["name"].astype(str).eq(g["name"]) & areas[g["key"]].astype(str).eq(g["value"])]
    fsel = buildings[buildings["id"].astype(str).eq(str(fc["fortress_osm_id"]))] if "id" in buildings else []
    if not len(gsel) or not len(fsel):
        return None
    grounds = gsel.geometry.union_all()
    fortress = max(getattr(fsel.geometry.iat[0], "geoms", [fsel.geometry.iat[0]]), key=lambda p: p.area)
    lm = (registry or {}).get(str(fc["fortress_osm_id"])) or {"height_m": 6.0}
    ring = np.asarray(fortress.exterior.coords)
    top = float(terrain.sample(ring[:, 0], ring[:, 1]).max()) + lm["height_m"]
    stats = {"fortress_top_m": round(top, 1), "cupolas": 0, "wall_m": 0.0, "lawns": 0, "piers": 0}

    def add(mat, vt, anchor):
        tm.add_object(mat, vt[0], vt[1], anchor)

    # ---- fortress roof: parapet, cupolas, guns
    par = fortress.difference(fortress.buffer(-0.4, join_style="mitre"))
    for p in getattr(par, "geoms", [par]):
        m = extrude(p, top - 0.05, top + fc["parapet_m"], (0.0, 0.0))
        if m is not None:
            add("M_FortConcrete", m[:2], np.asarray(p.centroid.coords[0]))
    centre, axis, length, _ = oriented_axes(fortress)
    out = np.asarray(fortress.centroid.coords[0]) - np.asarray(grounds.centroid.coords[0])
    out = out / max(np.linalg.norm(out), 1e-6)
    if axis @ out < 0:
        axis = -axis
    for k, cu in enumerate(fc["cupolas"]):
        c = centre + axis * (cu["at"] - 0.5) * length
        if not fortress.buffer(-cu["radius_m"] * 0.6).contains(Point(*c)):
            continue
        add("M_FortTurret", _dome(c, top, cu["radius_m"]), c)
        aim = np.arctan2(out[1], out[0]) + np.radians((-25, 25, -35, 35)[k % 4])
        d = np.array([np.cos(aim), np.sin(aim)])
        zg = top + 0.6 + 0.25 * cu["radius_m"]
        side = np.array([-d[1], d[0]])
        for s in (-1, 1):  # each cupola carries a pair of guns
            start = c + d * cu["radius_m"] * 0.7 + side * s * cu["radius_m"] * 0.22
            mid = start + d * cu["gun_m"] / 2
            add("M_FortGun", box(mid, aim, (cu["gun_m"], 0.45 if cu["radius_m"] > 4 else 0.32), zg - 0.22, zg + 0.22), mid)
        stats["cupolas"] += 1

    # ---- perimeter wall on the land side of the grounds, open at gates and clear of coast / buildings
    wc = fc["wall"]
    line = grounds.exterior if grounds.geom_type == "Polygon" else shapely.union_all([p.exterior for p in grounds.geoms])
    cut = []
    if coast is not None:
        cut.append(coast.buffer(wc["coast_clear_m"]))
    gates = features[features["barrier"].astype(str).isin(["gate", "sally_port", "entrance"])
                     | features["entrance"].astype(str).ne("nan")] if "barrier" in features else features.iloc[:0]
    cut += [p.buffer(wc["gate_gap_m"] / 2) for p in gates.geometry]
    near_b = buildings[buildings.intersects(grounds.buffer(2.0))]
    cut += [b.buffer(0.5) for b in near_b.geometry]
    wall = line.difference(shapely.union_all(cut)) if cut else line
    for piece in getattr(wall, "geoms", [wall]):
        if piece.geom_type != "LineString" or piece.length < 3.0:
            continue
        xy = np.asarray(piece.coords)
        for a, b in zip(xy[:-1], xy[1:]):  # oriented boxes (extrude() drops polygons under 4 m2)
            L = float(np.hypot(*(b - a)))
            if L < 0.3:
                continue
            n = int(np.ceil(L / 4.0))
            yaw = float(np.arctan2(b[1] - a[1], b[0] - a[0]))
            for k in range(n):
                p0, p1 = a + (b - a) * k / n, a + (b - a) * (k + 1) / n
                zg = terrain.sample(np.array([p0[0], p1[0]]), np.array([p0[1], p1[1]]))
                c = (p0 + p1) / 2
                add("M_FortWall", box(c, yaw, (L / n + 0.05, wc["thickness_m"]), zg.min() - 0.4,
                                      zg.max() + wc["height_m"]), c)
        stats["wall_m"] += piece.length
    stats["wall_m"] = round(stats["wall_m"], 1)

    # ---- lawns inside the grounds (draped, slightly lifted)
    grass = areas[areas["landuse"].astype(str).eq("grass") & areas.intersects(grounds.buffer(20.0))]
    for poly in grass.geometry:
        for p in getattr(poly, "geoms", [poly]):
            p = shapely.segmentize(p, 2.0)
            for tri in shapely.constrained_delaunay_triangles(p).geoms:
                c = np.asarray(orient(tri, 1.0).exterior.coords)[:3]
                z = terrain.sample(c[:, 0], c[:, 1]) + fc["lawn_lift_m"]
                add("M_Lawn", (np.column_stack([c, z]), np.array([(0, 1, 2)])), c.mean(axis=0))
            stats["lawns"] += 1

    # ---- fishermen's pier (Posto 6): deck slab over the water
    piers = areas[areas["man_made"].astype(str).eq("pier") & areas.intersects(grounds.buffer(200.0))]
    for poly in piers.geometry:
        m = extrude(poly, -1.0, fc["pier_deck_m"], (0.0, 0.0))
        if m is not None:
            add("M_Pier", m[:2], np.asarray(poly.centroid.coords[0]))
            stats["piers"] += 1
    return stats
