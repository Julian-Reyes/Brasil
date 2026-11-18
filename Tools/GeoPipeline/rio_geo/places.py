"""Places: flat terraces for sports pitches and designed hubs (Vidigal central hub), their meshes (pitch surfaces,
goals, fences, retaining walls, hub buildings) and gameplay markers.

Two halves:
  * terraces() / apply_terraces() / clear_zones() run inside build_area.py BEFORE the road solve and the building
    meshes, because they change the ground and remove houses / becos from designed spaces;
  * build() runs in scripts/build_places.py AFTER build_area.py, on the final terrain.
Hub layouts are in config `places.hubs` (UTM metres), so they can be moved after a walk-around test.
"""
import numpy as np
import shapely
from shapely.affinity import rotate
from shapely.geometry import Point, box as sbox

from .beach import box
from .buildings import extrude
from .shore import bench, tree


# --------------------------------------------------------------------------------------------- 2D layout
def rect(center, size, rot_deg):
    cx, cy = center
    w, h = size
    return rotate(sbox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), rot_deg, origin=(cx, cy))


def terraces(cfg, pitch_areas, bbox):
    """All terraces in the AOI: [{name, kind, polygon, pitch (polygon or None), blend_m, z (None = auto)}]."""
    out = []
    aoi = sbox(*bbox)
    pc = cfg["pitch_terraces"]
    if pc["enabled"] and pitch_areas is not None:
        for _, r in pitch_areas.iterrows():
            if str(r.get("leisure")) != "pitch" or not r.geometry.intersects(aoi):
                continue
            g = r.geometry if r.geometry.geom_type == "Polygon" else max(r.geometry.geoms, key=lambda p: p.area)
            name = r.get("name") if str(r.get("name")) != "nan" else f"pitch {r['osm_type']}/{r['osm_id']}"
            out.append({"name": name, "kind": "pitch", "sport": str(r.get("sport")), "pitch": g,
                        "polygon": g.buffer(pc["apron_m"], join_style="mitre"), "blend_m": pc["blend_m"], "z": None,
                        "source": f"osm:{r['osm_type']}/{r['osm_id']}"})
    for hub in cfg["hubs"]:
        if not sbox(*bbox).intersects(Point(hub["center"]).buffer(200)):
            continue
        for t in hub["terraces"]:
            poly = rect(t["center"], t["size"], hub["rotation_deg"])
            pitch = rect(t["center"], t["pitch"], hub["rotation_deg"]) if t.get("pitch") else None
            out.append({"name": f"{hub['name']}: {t['name']}", "kind": "hub_" + t["name"], "sport": "soccer",
                        "pitch": pitch, "polygon": poly, "blend_m": t["blend_m"], "z": t.get("z"),
                        "surface": t.get("surface", "pitch" if pitch is not None else "praca"), "source": "hub"})
    return out


def hub_elements(cfg, bbox):
    """Designed hub buildings / props with footprints: [{hub, type, label, polygon, levels, yaw, ...}]."""
    out = []
    for hub in cfg["hubs"]:
        if not sbox(*bbox).intersects(Point(hub["center"]).buffer(200)):
            continue
        for e in hub["elements"]:
            poly = rect(e["center"], e["size"], hub["rotation_deg"])
            fx, fy = e.get("face", hub["center"])
            yaw = np.arctan2(fy - e["center"][1], fx - e["center"][0])  # direction the front faces
            out.append({**e, "hub": hub["name"], "polygon": poly, "yaw": float(yaw)})
    return out


def clear_zones(terr, elems, cfg):
    """Union of everything that must be free of procedural / mapped houses."""
    parts = [t["polygon"].buffer(cfg["clear_margin_m"]) for t in terr]
    parts += [e["polygon"].buffer(cfg["clear_margin_m"]) for e in elems]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def hub_path_block(terr):
    """Hub terraces (shrunk 1 m): becos / stairs crossing them are dropped so nothing cuts across the field."""
    parts = [t["polygon"].buffer(-1.0) for t in terr if t["source"] == "hub"]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def apply_terraces(hf, terr, min_z=1.0):
    """Flatten the heightfield inside each terrace to its level (median ground inside, unless given) and blend
    back over blend_m (short blends = steep edges for retaining walls). Sets t['z']. In place."""
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    for t in terr:
        poly = t["polygon"]
        w, s, e, n = poly.bounds
        b = t["blend_m"] + hf.step
        win = (X >= w - b) & (X <= e + b) & (Y >= s - b) & (Y <= n + b)
        if not win.any():
            continue
        inside = np.zeros(hf.shape, bool)
        inside[win] = shapely.contains_xy(poly, X[win], Y[win])
        if not inside.any():
            continue
        if t["z"] is None:
            t["z"] = float(np.median(hf.z[inside]))
        t["z"] = max(t["z"], min_z)  # never below the land floor (coastal flats read as seabed, e.g. Sheraton courts)
        d = np.zeros(hf.shape)
        d[win] = shapely.distance(poly, shapely.points(X[win], Y[win]))
        wgt = np.where(inside, 1.0, np.clip(1 - d / max(t["blend_m"], 1e-6), 0, 1)) * win
        hf.z = hf.z * (1 - wgt) + t["z"] * wgt


# --------------------------------------------------------------------------------------------- meshes
def slab(poly, z0, z1):
    return extrude(poly, z0, z1, (0.0, 0.0))


def oriented_axes(poly):
    """Centre, long-axis unit vector, length and width of a (roughly rectangular) pitch."""
    mrr = poly.minimum_rotated_rectangle
    c = np.asarray(mrr.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    if np.linalg.norm(e1) < np.linalg.norm(e2):
        e1, e2 = e2, e1
    centre = c.mean(axis=0)
    return centre, e1 / np.linalg.norm(e1), np.linalg.norm(e1), np.linalg.norm(e2)


def goal_parts(centre, axis, length, width, z, cfg):
    """Two goal frames (posts + crossbar) at the pitch ends, sized by the pitch."""
    gw = cfg["goal_m"]["large" if length >= cfg["large_pitch_m"] else "small"]
    gh = 2.0 if gw < 6 else 2.44
    yaw = np.arctan2(axis[1], axis[0])
    side = np.array([-axis[1], axis[0]])
    parts = []
    for sgn in (-1, 1):
        g = centre + axis * sgn * (length / 2 - 0.3)
        for k in (-1, 1):
            parts.append(box(g + side * k * gw / 2, yaw, (0.12, 0.12), z, z + gh))
        parts.append(box(g, yaw + np.pi / 2, (gw + 0.12, 0.12), z + gh - 0.12, z + gh))
    return parts


def fence_parts(poly, z, height, spacing=3.0):
    """Posts every `spacing` m plus a top and a middle rail along the outline (the netting is a later material)."""
    ring = np.asarray(poly.exterior.coords)
    parts = []
    for a, b in zip(ring[:-1], ring[1:]):
        L = np.hypot(*(b - a))
        if L < 0.5:
            continue
        yaw = np.arctan2(b[1] - a[1], b[0] - a[0])
        for f in np.arange(0, L, spacing) / L:
            parts.append(box(a + (b - a) * f, yaw, (0.1, 0.1), z, z + height))
        mid = (a + b) / 2
        for hz in (height, height * 0.5):
            parts.append(box(mid, yaw, (L, 0.06), z + hz - 0.06, z + hz))
    return parts


def wall_quads(poly, z_top, terrain, out_m, min_drop=0.6, step=2.0):
    """Retaining walls along a terrace outline: vertical faces between the terrace level and the terrain just
    outside it, wherever they differ by more than min_drop. Returns (verts, tris) in world coords."""
    ring = poly.exterior
    L = ring.length
    s = np.arange(0, L + step, step)
    pts = np.array([ring.interpolate(min(v, L)).coords[0] for v in s])
    t = np.gradient(pts, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    nrm = np.column_stack([t[:, 1], -t[:, 0]])
    if not poly.contains(Point(*(pts[0] + nrm[0] * 0.5))):  # make nrm point outwards
        pass
    else:
        nrm = -nrm
    outside = pts + nrm * out_m
    zo = terrain.sample(outside[:, 0], outside[:, 1])
    verts, tris = [], []
    for k in range(len(pts) - 1):
        lo = min(z_top, zo[k], zo[k + 1]) - 0.3
        hi = max(z_top, zo[k], zo[k + 1])
        if hi - min(z_top, zo[k], zo[k + 1]) < min_drop:
            continue
        a, b = pts[k], pts[k + 1]
        o = len(verts)
        verts += [(a[0], a[1], lo), (b[0], b[1], lo), (b[0], b[1], hi), (a[0], a[1], hi)]
        # double-sided: the wall is seen from the terrace and from below
        tris += [(o, o + 1, o + 2), (o, o + 2, o + 3), (o, o + 2, o + 1), (o, o + 3, o + 2)]
    return np.array(verts).reshape(-1, 3), np.array(tris, dtype=np.int64).reshape(-1, 3)


def building_parts(e, z, cfg):
    """Hub building: body (levels x storey), flat laje roof with a low parapet, optional awning on the front,
    optional water tank. Returns [(material, verts, tris)]."""
    hc = cfg["hub_buildings"]
    poly, yaw = e["polygon"], e["yaw"]
    top = z + e.get("levels", 1) * hc["storey_m"]
    mat = {"house": "M_PlayerHouse", "hangout": "M_Hangout", "workshop": "M_Workshop"}.get(e["type"], "M_Shop")
    if e["type"] == "stand":
        parts = []
    else:
        parts = [(mat, *slab(poly, z - 0.4, top)[:2])]
        ring = np.asarray(poly.exterior.coords)
        if hc["parapet_m"] > 0:  # low wall around the laje: one thin box per footprint edge
            for a, b in zip(ring[:-1], ring[1:]):
                L = float(np.hypot(*(b - a)))
                if L > 0.3:
                    v, t = box((a + b) / 2, np.arctan2(b[1] - a[1], b[0] - a[0]), (L, 0.15), top, top + hc["parapet_m"])
                    parts.append(("M_Parapet", v, t))
    c = np.asarray(poly.centroid.coords[0])
    fwd = np.array([np.cos(yaw), np.sin(yaw)])
    if e["type"] in ("shop", "workshop", "hangout"):
        depth = hc["awning_depth_m"]
        # front edge = the footprint's extent along the facing direction
        ext = max((np.asarray(poly.exterior.coords)[:, :2] - c) @ fwd)
        width = e["size"][0] if abs(np.cos(yaw)) < 0.7 else e["size"][1]
        ac = c + fwd * (ext + depth / 2)
        v, t = box(ac, yaw + np.pi / 2, (width, depth), z + hc["awning_z_m"], z + hc["awning_z_m"] + 0.15)
        parts.append((e.get("awning", "M_Awning"), v, t))
    if e.get("water_tank", True):
        v, t = box(c + np.array([0.8, 0.8]), 0.0, (1.2, 1.2), top, top + 1.1)
        parts.append(("M_WaterTank", v, t))
    if e["type"] == "stand":  # mototaxi stand: open canopy on four posts, no walls
        w, d = e["size"]
        parts = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                p = c + np.array([sx * (w / 2 - 0.2), sy * (d / 2 - 0.2)])
                parts.append(("M_Parapet", *box(p, 0.0, (0.15, 0.15), z, z + 2.6)))
        parts.append(("M_Awning", *box(c, 0.0, (w + 0.4, d + 0.4), z + 2.6, z + 2.8)))
    return parts


def build(terr, elems, terrain, tm, cfg, seed_name="places"):
    """Meshes for all terraces and hub elements into the TiledMesh. Returns stats."""
    pc = cfg["pitch_terraces"]
    stats = {"terraces": 0, "pitches": 0, "walls_m": 0.0, "hub_buildings": 0}
    for t in terr:
        if t["z"] is None:
            continue
        z = t["z"]
        anchor = np.asarray(t["polygon"].centroid.coords[0])
        surface = "M_Praca" if t.get("surface") == "praca" else "M_Concrete"
        v, tr, _ = slab(t["polygon"], z - 0.3, z + 0.03)
        tm.add_object(surface, v, tr, anchor)
        if t["pitch"] is not None:
            v, tr, _ = slab(t["pitch"], z, z + 0.06)
            tm.add_object("M_Pitch", v, tr, anchor)
            centre, axis, length, width = oriented_axes(t["pitch"])
            if t["sport"] in ("soccer", "multi") or "futsal" in t["sport"] or t["kind"] == "hub_campinho":
                for v, tr in goal_parts(centre, axis, length, width, z + 0.06, pc):
                    tm.add_object("M_Goal", v, tr, anchor)
            for v, tr in fence_parts(t["polygon"].buffer(-0.2, join_style="mitre"), z, pc["fence_m"]):
                tm.add_object("M_Fence", v, tr, anchor)
            stats["pitches"] += 1
        v, tr = wall_quads(t["polygon"], z, terrain, t["blend_m"] + 0.6)
        if len(tr):
            tm.add_object("M_RetainingWall", v, tr, anchor)
            stats["walls_m"] += len(tr) / 4 * 2.0
        stats["terraces"] += 1
    for e in elems:
        c = np.asarray(e["polygon"].centroid.coords[0])
        ring = np.asarray(e["polygon"].exterior.coords)
        z = float(terrain.sample(ring[:, 0], ring[:, 1]).min())
        e["z"] = round(z, 2)
        for mat, v, tr in building_parts(e, z, cfg):
            tm.add_object(mat, v, tr, c)
        stats["hub_buildings"] += 1
    for hub in cfg["hubs"]:  # praca furniture: trees at the corners, benches along the edges
        for t in terr:
            if t["kind"] != "hub_praca" or t["z"] is None or not t["name"].startswith(hub["name"]):
                continue
            mrr = np.asarray(t["polygon"].exterior.coords)[:4]
            cen = mrr.mean(axis=0)
            for k, corner in enumerate(mrr):
                p = cen + (corner - cen) * 0.8
                for mat, (v, tr) in tree(np.r_[p, t["z"]], 8.0, 3.0, (seed_name, k)).items():
                    tm.add_object(mat, v, tr, p)
            for k in range(4):
                a, b = mrr[k], mrr[(k + 1) % 4]
                p = cen + ((a + b) / 2 - cen) * 0.75
                yaw = np.arctan2(b[1] - a[1], b[0] - a[0])
                for v, tr in bench(p, yaw, t["z"], {"size_m": [1.8, 0.45, 0.45]}):
                    tm.add_object("M_Bench", v, tr, p)
    return stats


def markers(elems, landmark_feats, terrain, utm_to_ue):
    """Gameplay markers: hub elements (player_home, crew_hangout, shops, ...) and named real places."""
    out = []
    for e in elems:
        c = e["polygon"].centroid
        out.append({"name": e["label"], "kind": e.get("marker", e["type"]), "hub": e["hub"],
                    "utm": [round(c.x, 2), round(c.y, 2), e.get("z")], "location_cm": list(utm_to_ue(c.x, c.y, e.get("z") or 0.0)),
                    "source": "hub"})
    for name, kind, x, y, src in landmark_feats:
        z = float(terrain.sample([x], [y])[0])
        out.append({"name": name, "kind": kind, "utm": [round(x, 2), round(y, 2), round(z, 2)],
                    "location_cm": list(utm_to_ue(x, y, z)), "source": src})
    return out
