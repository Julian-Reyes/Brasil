"""Buildings: OSM footprints -> placeholder heights -> extruded blocks seated on the terrain."""
import re

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from .common import stable_unit


def _num(v):
    if v is None or str(v) in ("nan", "None"):
        return None
    m = re.findall(r"\d+(?:\.\d+)?", str(v).replace(",", "."))
    return float(m[0]) if m else None


def building_height(row, informal, cfg):
    """Returns (height_m, source). OSM height > building:levels > deterministic default."""
    h = _num(row.get("height"))
    if h and 2.0 <= h <= 250.0:
        return h, "osm:height"
    storey = cfg["informal_storey_m"] if informal else cfg["storey_m"]
    lv = _num(row.get("building:levels"))
    if lv and 1 <= lv <= 60:
        return lv * storey + 0.5, "osm:levels"
    key = row.get("osmid", row.name)
    if informal:
        lo, hi = cfg["informal_levels"]
    elif row.geometry.area >= cfg["formal_large_footprint_m2"]:
        lo, hi = cfg["formal_large_levels"]
    else:
        lo, hi = cfg["formal_small_levels"]
    levels = lo + int(stable_unit(key) * (hi - lo + 1))
    return levels * storey + 0.5, "default"


def extrude(poly, base_z, top_z, pivot):
    """Closed prism (walls + roof, open bottom sunk into terrain). Footprint holes become courtyards."""
    poly = orient(shapely.simplify(poly, 0.25, preserve_topology=True), 1.0)
    if poly.is_empty or poly.geom_type != "Polygon" or poly.area < 4.0:
        return None
    px, py = pivot
    verts, tris, normals = [], [], []
    count = 0
    for ring in [poly.exterior, *poly.interiors]:
        c = np.asarray(ring.coords)[:-1, :2] - (px, py)
        a, b = c, np.roll(c, -1, axis=0)
        edge = b - a
        out = np.column_stack([edge[:, 1], -edge[:, 0]])
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
        for (ax, ay), (bx, by), (nx, ny) in zip(a, b, out):
            verts += [(ax, ay, base_z), (bx, by, base_z), (bx, by, top_z), (ax, ay, top_z)]
            normals += [(nx, ny, 0.0)] * 4
            tris += [(count, count + 1, count + 2), (count, count + 2, count + 3)]
            count += 4
    roof = shapely.constrained_delaunay_triangles(poly)
    for tri in roof.geoms:
        c = np.asarray(orient(tri, 1.0).exterior.coords)[:3, :2] - (px, py)
        for x, y in c:
            verts.append((x, y, top_z))
            normals.append((0.0, 0.0, 1.0))
        tris.append((count, count + 1, count + 2))
        count += 3
    return np.array(verts), np.array(tris), np.array(normals)


def footprint_polygons(geom):
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return []


__all__ = ["building_height", "extrude", "footprint_polygons", "Polygon"]
