"""Places: flat terraces for sports pitches and designed hubs (Vidigal central hub), their meshes (pitch surfaces,
goals, fences, retaining walls, hub buildings) and gameplay markers.

Two halves:
  * terraces() / apply_terraces() / clear_zones() run inside build_area.py BEFORE the road solve and the building
    meshes, because they change the ground and remove houses / becos from designed spaces;
  * build() runs in scripts/build_places.py AFTER build_area.py, on the final terrain.
Designed stairs (hub `stairs`) sit in between: their 2D paths clear houses like terraces do, their end heights
are resolved in build_area.py after the road solve (resolve_stairs: terrace / road surface / terrain), the
terrain is ramped under them, and the 3D profiles go to stairs.json for build_places.py. Fences and retaining
walls get gaps where a stair or a gate meets a terrace.
Hub layouts are in config `places.hubs` (UTM metres), so they can be moved after a walk-around test.
"""
import numpy as np
import shapely
from shapely.affinity import rotate
from shapely.geometry import Point, box as sbox

from .beach import box
from .buildings import extrude
from .shore import bench, tree
from . import stairs as st


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
            rot = t.get("rotation_deg", hub["rotation_deg"])  # per-item override, e.g. to follow a street
            poly = rect(t["center"], t["size"], rot)
            pitch = rect(t["center"], t["pitch"], rot) if t.get("pitch") else None
            out.append({"name": f"{hub['name']}: {t['name']}", "kind": "hub_" + t["name"], "sport": "soccer",
                        "pitch": pitch, "polygon": poly, "blend_m": t["blend_m"], "z": t.get("z"),
                        "surface": t.get("surface", "pitch" if pitch is not None else "praca"), "source": "hub",
                        "gates": [[*g["at"], g["width"]] for g in t.get("gates", [])]})
    return out


def hub_stairs(cfg, bbox):
    """Designed stairs: [{name, hub, path (N x 2, top end first or last, either way), width, corridor}]."""
    out = []
    for hub in cfg["hubs"]:
        if not sbox(*bbox).intersects(Point(hub["center"]).buffer(200)):
            continue
        for s in hub.get("stairs", []):
            path = np.asarray(s["path"], dtype=float)
            out.append({"name": f"{hub['name']}: {s['name']}", "hub": hub["name"], "path": path,
                        "width": float(s["width"]), "corridor": st.corridor(path, s["width"])})
    return out


def resolve_stairs(stairs, terr, terrain, road_z, cfg):
    """3D profile per stair: each end takes the level of what it stands on (terrace surface, road surface via
    road_z(x, y) -> z or None, else terrain), z linear along the path. Ramps the terrain under it. In place."""
    for s in stairs:
        ends = []
        for x, y in (s["path"][0], s["path"][-1]):
            z, src = None, "terrain"
            for t in terr:
                if t["z"] is not None and t["polygon"].buffer(-0.2).contains(Point(x, y)):
                    z, src = t["z"] + 0.03, t["name"]
                    break
            if z is None:
                rz = road_z(x, y)
                if rz is not None:
                    z, src = rz, "road"
            if z is None:
                z = float(terrain.sample([x], [y])[0])
            ends.append((float(z), src))
        p = s["path"]
        seg = np.r_[0.0, np.cumsum(np.hypot(*(p[1:] - p[:-1]).T))]
        z = ends[0][0] + (ends[1][0] - ends[0][0]) * seg / seg[-1]
        s["profile"] = np.column_stack([p, z])
        s["rise"], s["steps"] = st.stair_rise(ends[1][0] - ends[0][0], cfg.get("stair_max_rise_m", 0.17))
        s["ends"] = [{"z": round(e[0], 2), "on": e[1]} for e in ends]
        s["min_run_m"] = round(seg[-1] / s["steps"], 2)
        # terraces plus the lowered ring outside them (reflatten_terraces): a ramp there would rebuild the wedge
        protect = shapely.union_all([t["polygon"].buffer(terrain.step * 1.01, join_style="mitre")
                                     for t in terr if t["z"] is not None]) if terr else None
        st.apply_ramp(terrain, s["profile"], s["width"], protect=protect)
    return stairs


def gap_zones(stairs, terr, end_m=2.5):
    """Where fences and retaining walls must open: the last end_m of each stair at both ends (where it enters a
    terrace or meets a street; not along its length, a stair may run beside a terrace's wall) and terrace gates."""
    from shapely.ops import substring  # noqa: PLC0415
    parts = []
    for s in stairs:
        line = shapely.LineString(np.asarray(s["profile"] if "profile" in s else s["path"])[:, :2])
        for a, b in ((0.0, min(end_m, line.length)), (max(0.0, line.length - end_m), line.length)):
            parts.append(substring(line, a, b).buffer(s["width"] / 2 + 0.3, cap_style="flat"))
    for t in terr:
        parts += [Point(x, y).buffer(w / 2) for x, y, w in t.get("gates", [])]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def hub_elements(cfg, bbox):
    """Designed hub buildings / props with footprints: [{hub, type, label, polygon, levels, yaw, ...}]."""
    out = []
    for hub in cfg["hubs"]:
        if not sbox(*bbox).intersects(Point(hub["center"]).buffer(200)):
            continue
        for e in hub["elements"]:
            poly = rect(e["center"], e["size"], e.get("rotation_deg", hub["rotation_deg"]))
            fx, fy = e.get("face", hub["center"])
            yaw = np.arctan2(fy - e["center"][1], fx - e["center"][0])  # direction the front faces
            out.append({**e, "hub": hub["name"], "polygon": poly, "yaw": float(yaw)})
    return out


def clear_zones(terr, elems, cfg, stairs=()):
    """Union of everything that must be free of procedural / mapped houses."""
    parts = [t["polygon"].buffer(cfg["clear_margin_m"]) for t in terr]
    parts += [e["polygon"].buffer(cfg["clear_margin_m"]) for e in elems]
    parts += [s["corridor"].buffer(1.0) for s in stairs]
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


def trim_terraces(terr, corridors):
    """Cut street corridors out of terrace outlines (largest piece kept). In place; returns the trimmed names."""
    names = []
    for t in terr:
        if not t["polygon"].intersects(corridors):
            continue
        g = t["polygon"].difference(corridors)
        if g.is_empty:
            continue
        g = g if g.geom_type == "Polygon" else max(g.geoms, key=lambda p: p.area)
        if g.area < t["polygon"].area - 0.5:
            t["polygon"] = g
            names.append(t["name"])
    return names


def reflatten_terraces(hf, terr, keep=None):
    """Road carving runs after apply_terraces, and its blend zone (up to 10 m beside a street) pulls the ground
    inside a nearby terrace back toward the street level (the campinho had hills poking through the pitch). Set
    the grid cells inside a terrace to its level again (the retaining walls cover the edge). On a 2 m grid one
    cell at the boundary is always a slope; on the uphill side it must lie outside the wall, not as a wedge over
    the apron, so the ring of nodes within one cell outside the terrace is lowered to the terrace level where
    higher (the wall's top ledge covers that trench), except inside `keep` (street / path corridors). In place.
    Returns (largest correction in metres, that terrace's name)."""
    xs, ys = hf.coords()
    worst = (0.0, None)
    for t in terr:
        if t["z"] is None:
            continue
        w, s_, e, n = t["polygon"].buffer(hf.step * 1.5).bounds
        ix = np.flatnonzero((xs >= w) & (xs <= e))
        iy = np.flatnonzero((ys >= s_) & (ys <= n))
        if not len(ix) or not len(iy):
            continue
        X, Y = np.meshgrid(xs[ix], ys[iy])
        inside = shapely.contains_xy(t["polygon"], X, Y)
        # hard inside the outline shrunk by one cell; in the edge band the ground may stay lower (it slopes
        # down behind the retaining wall instead of sticking out in front of it as grid sawtooth) but never higher
        core = shapely.contains_xy(t["polygon"].buffer(-hf.step), X, Y)
        sub = hf.z[np.ix_(iy, ix)]
        if core.any():
            worst = max(worst, (float(np.abs(sub[core] - t["z"]).max()), t["name"]), key=lambda v: v[0])
        band = inside & ~core
        sub[core] = t["z"]
        sub[band] = np.minimum(sub[band], t["z"])
        ring_poly = t["polygon"].buffer(hf.step * 1.01, join_style="mitre").difference(t["polygon"])
        if keep is not None and not keep.is_empty:
            ring_poly = ring_poly.difference(keep)
        ring = shapely.contains_xy(ring_poly, X, Y) & ~inside
        sub[ring] = np.minimum(sub[ring], t["z"])
        hf.z[np.ix_(iy, ix)] = sub
    return worst


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


def fence_parts(poly, z, height, spacing=3.0, gaps=None):
    """Posts every `spacing` m plus a top and a middle rail along the outline (the netting is a later material).
    `gaps` (polygon): openings (gates, stair landings); a post closes each side of a gap."""
    line = poly.exterior
    if gaps is not None and not gaps.is_empty:
        line = line.difference(gaps)
    pieces = list(line.geoms) if hasattr(line, "geoms") else [line]
    parts = []
    for piece in pieces:
        ring = np.asarray(piece.coords)
        if len(ring) < 2:
            continue
        for a, b in zip(ring[:-1], ring[1:]):
            L = np.hypot(*(b - a))
            if L < 0.05:
                continue
            yaw = np.arctan2(b[1] - a[1], b[0] - a[0])
            for f in np.arange(0, L, spacing) / L:
                parts.append(box(a + (b - a) * f, yaw, (0.1, 0.1), z, z + height))
            mid = (a + b) / 2
            for hz in (height, height * 0.5):
                parts.append(box(mid, yaw, (L, 0.06), z + hz - 0.06, z + hz))
        parts.append(box(ring[-1], 0.0, (0.1, 0.1), z, z + height))
    return parts


def wall_quads(poly, z_top, terrain, out_m, min_drop=0.6, step=2.0, gaps=None, cap_m=0.0, beside=()):
    """Retaining walls along a terrace outline: vertical faces between the terrace level and the terrain out_m
    outside it, wherever they differ by more than min_drop, except inside `gaps` (stair landings, gates). Walls
    holding back higher ground get a flat top ledge cap_m wide over the lowered ring behind them (see
    reflatten_terraces). Where a designed stair runs along the outline (`beside`: stairs with profiles), the wall
    top follows the stair line instead and gets no ledge. Returns (verts, tris) in world coords."""
    along = []
    for s in beside:
        prof = np.asarray(s["profile"])
        line = shapely.LineString(prof[:, :2])
        sp = np.r_[0.0, np.cumsum(np.hypot(*(prof[1:, :2] - prof[:-1, :2]).T))]
        along.append((s["corridor"].buffer(0.8), line, sp, prof[:, 2]))
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
        mid = Point(*((a + b) / 2))
        if gaps is not None and not gaps.is_empty and gaps.contains(mid):
            continue
        stair_z = None
        for corr, line, sp, zp in along:
            if corr.contains(mid):
                stair_z = float(np.interp(line.project(mid), sp, zp))
                break
        if stair_z is not None:
            hi = max(stair_z, z_top)
            if hi - lo - 0.3 < min_drop:
                continue
        o = len(verts)
        verts += [(a[0], a[1], lo), (b[0], b[1], lo), (b[0], b[1], hi), (a[0], a[1], hi)]
        # double-sided: the wall is seen from the terrace and from below
        tris += [(o, o + 1, o + 2), (o, o + 2, o + 3), (o, o + 2, o + 1), (o, o + 3, o + 2)]
        if cap_m > 0 and hi > z_top + min_drop and stair_z is None:
            ca, cb = a + nrm[k] * cap_m, b + nrm[k + 1] * cap_m
            o = len(verts)
            verts += [(a[0], a[1], hi), (b[0], b[1], hi), (cb[0], cb[1], hi), (ca[0], ca[1], hi)]
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
    if e["type"] in ("shop", "workshop", "hangout", "kiosk"):
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


def build(terr, elems, terrain, tm, cfg, seed_name="places", stairs=(), streets=None):
    """Meshes for all terraces, designed stairs and hub elements into the TiledMesh. `streets` (polygon): road
    surfaces, where no stair side wall may stand (a stair's top landing starts on the street). Returns stats."""
    pc = cfg["pitch_terraces"]
    stats = {"terraces": 0, "pitches": 0, "walls_m": 0.0, "hub_buildings": 0, "stairs": []}
    gaps = gap_zones(stairs, terr)
    for s in stairs:
        anchor = np.asarray(s["profile"][0][:2])
        blocks, info = st.step_blocks(s["profile"], s["width"], s["rise"], depth=0.6)
        for v, tr in blocks:
            tm.add_object("M_Stair", v, tr, anchor)
        for mat, pieces in (("M_Parapet", st.cheek_walls(s["profile"], s["width"], s["rise"])),
                            ("M_RetainingWall", st.side_walls(s["profile"], s["width"], terrain))):
            for v, tr in pieces:
                c = np.asarray(v)[:, :2].mean(axis=0)
                if streets is not None and streets.contains(Point(*c)):
                    continue
                tm.add_object(mat, v, tr, anchor)
        stats["stairs"].append({"name": s["name"], "steps": info["steps"], "rise_m": round(s["rise"], 3),
                                "tread_m": info["min_tread_m"], "ends": s.get("ends")})
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
            for v, tr in fence_parts(t["polygon"].buffer(-0.2, join_style="mitre"), z, pc["fence_m"], gaps=gaps):
                tm.add_object("M_Fence", v, tr, anchor)
            stats["pitches"] += 1
        v, tr = wall_quads(t["polygon"], z, terrain, 2 * terrain.step + 0.2, gaps=gaps, cap_m=2 * terrain.step,
                           beside=stairs)
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
    for hub in cfg["hubs"]:  # praca furniture: trees around the edge every ~9 m, benches between them
        for t in terr:
            if t["kind"] != "hub_praca" or t["z"] is None or not t["name"].startswith(hub["name"]):
                continue
            keep_out = shapely.union_all([gaps] + [e["polygon"].buffer(1.5) for e in elems]
                                         + [s["corridor"].buffer(1.5) for s in stairs])
            inner = t["polygon"].buffer(-2.0, join_style="mitre").exterior
            n = max(4, int(round(inner.length / 9.0)))
            for k in range(n):
                p = np.asarray(inner.interpolate(k / n, normalized=True).coords[0])
                if keep_out.contains(Point(*p)):
                    continue
                for mat, (v, tr) in tree(np.r_[p, t["z"]], 8.0, 3.0, (seed_name, k)).items():
                    tm.add_object(mat, v, tr, p)
                q = np.asarray(inner.interpolate((k + 0.5) / n, normalized=True).coords[0])
                q2 = np.asarray(inner.interpolate((k + 0.52) / n, normalized=True).coords[0])
                if keep_out.contains(Point(*q)):
                    continue
                yaw = np.arctan2(q2[1] - q[1], q2[0] - q[0])
                for v, tr in bench(q, yaw, t["z"], {"size_m": [1.8, 0.45, 0.45]}):
                    tm.add_object("M_Bench", v, tr, q)
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
