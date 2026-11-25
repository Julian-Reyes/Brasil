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
    if str(row.get("building")) == "roof":  # canopies: beach kiosk roofs, fuel stations, market covers
        return cfg.get("roof_canopy_m", 3.0), "default:roof"
    if not informal and row.geometry.area < cfg.get("tiny_footprint_m2", 0.0):  # kiosks, booths, guard huts
        return storey + 0.5, "default:tiny"
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


def formal_style(key, height, area, cfg):
    """Facade material and roof extras for a formal-city building, deterministic per key.
    Returns {"material", "ground_band_m" (0 = none), "parapet_m", "machine_room": (frac, height_m) or None}."""
    names, weights = zip(*cfg["facades"].items())
    w = np.cumsum(weights) / np.sum(weights)
    mat = names[int(np.searchsorted(w, stable_unit((key, "facade"))))]
    band = cfg["ground_band_m"] if height >= cfg["ground_band_min_height_m"] and area >= 40.0 else 0.0
    room = None
    if height >= cfg["machine_room_min_height_m"] and area >= cfg["machine_room_min_area_m2"]:
        lo, hi = cfg["machine_room_frac"]
        hlo, hhi = cfg["machine_room_h_m"]
        room = (lo + (hi - lo) * stable_unit((key, "room")), hlo + (hhi - hlo) * stable_unit((key, "room_h")))
    parapet = cfg["parapet_m"] if height >= cfg["ground_band_min_height_m"] else 0.0
    return {"material": mat, "ground_band_m": band, "parapet_m": parapet, "machine_room": room}


def machine_room(poly, frac, key):
    """Rooftop box (lift machine room / water reservoir): the footprint scaled by sqrt(frac) about a point
    shifted off-centre, kept inside the roof with a 1 m margin. None if it does not fit."""
    from shapely import affinity
    k = float(np.sqrt(frac))
    inner = poly.buffer(-1.0)
    if inner.is_empty:
        return None
    c = inner.representative_point()
    room = affinity.scale(poly.minimum_rotated_rectangle, k, k, origin=c)
    room = room.intersection(inner)
    if room.is_empty or room.geom_type != "Polygon" or room.area < 6.0:
        return None
    return room


__all__ = ["building_height", "formal_style", "machine_room", "extrude", "footprint_polygons", "Polygon"]
