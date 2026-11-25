"""Signature escadarias: one or two long, wide, painted stairways that work as landmarks and routes.

A route is a chain of OSM ways (steps, becos, steep pedestrian lanes) between configured OSM node ids, found
as the cheapest path over the pedestrian network (config `escadarias.cost`: steps cheapest, car streets
dear). Everything on it becomes a wide stair:

  build_area.py
    * resolve_routes(): retags the route's edges BEFORE infill and the road solve: highway=steps, a wide
      `width`, `escadaria` = route name. Houses then line it at the new width, the profile solve treats it
      as a stair and the terrain is carved under its full width.
    * after the road solve: buildings standing in the corridor are dropped, the houses next to it get painted
      facades, and each edge's solved profile gets landings (with_landings: a flat run every
      `landings.every_rise_m` of climb, the steps just before / after it slightly steeper so the heights meet
      the solved line again within `margin_m`). The profiles, oriented base -> top, go to escadarias.json;
      the stair edges are not meshed there.
  build_details.py
    * parts(): painted treads (colour bands by height or alternating steps), a handrail on the left going up,
      lamp posts, benches on landings, murals on the house walls facing the stair, a bar (counter, awning,
      sign, a table with stools) at one or two landings, plus markers for the base, the top and the bars.
Every random choice is keyed on stable ids (stable_unit), so rebuilds are identical.
"""
import networkx as nx
import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.geometry.polygon import orient

from . import stairs as st
from .beach import box
from .common import stable_unit
from .details import _z, wire
from .shore import bench


def _first(v):
    return str(v).split(";")[0].strip() if v is not None and str(v) != "nan" else None


# --------------------------------------------------------------------------------------------- routes
def resolve_routes(edges, cfg):
    """Retag the edges of every configured route. edges: the raw OSM edges (both directions stored).
    Returns (edges, [{name, nodes, edges: [(u, v)], length_m, classes}]) - routes whose nodes are missing
    from this AOI are skipped (listed with 'missing')."""
    edges = edges.copy()
    for col in ("width", "escadaria"):
        if col not in edges:
            edges[col] = None
        edges[col] = edges[col].astype(object)
    cls = edges["highway"].map(_first)
    G = nx.Graph()
    for (u, v), w, c in zip(zip(edges["u"], edges["v"]), edges.geometry.length, cls):
        if c not in cfg["cost"]:
            continue
        w = w * cfg["cost"][c]
        if not G.has_edge(u, v) or G.edges[u, v]["w"] > w:
            G.add_edge(u, v, w=w)
    routes, taken = [], set()
    for r in cfg["routes"]:
        nodes = [int(n) for n in r["nodes"]]
        if any(n not in G for n in nodes):
            routes.append({"name": r["name"], "missing": [n for n in nodes if n not in G]})
            continue
        try:
            path = [nodes[0]]
            for a, b in zip(nodes[:-1], nodes[1:]):
                path += nx.shortest_path(G, a, b, weight="w")[1:]
        except nx.NetworkXNoPath:
            routes.append({"name": r["name"], "missing": "no path"})
            continue
        pairs = [(a, b) for a, b in zip(path[:-1], path[1:]) if frozenset((a, b)) not in taken]
        taken |= {frozenset(p) for p in pairs}
        keys = {frozenset(p) for p in pairs}
        m = np.array([frozenset((u, v)) in keys for u, v in zip(edges["u"], edges["v"])])
        L, classes, seen = 0.0, {}, set()
        for u, v, g, c in zip(edges["u"][m], edges["v"][m], edges.geometry[m], cls[m]):
            k = (frozenset((u, v)), round(g.length, 1))
            if k in seen:  # both directions of a two-way way are stored
                continue
            seen.add(k)
            classes[c] = classes.get(c, 0.0) + g.length
            L += g.length
        edges.loc[m, "highway"] = "steps"
        edges.loc[m, "width"] = str(r.get("width_m", cfg["width_m"]))
        edges.loc[m, "escadaria"] = r["name"]
        routes.append({"name": r["name"], "nodes": path, "edges": pairs, "length_m": round(L),
                       "classes": {k: round(v) for k, v in classes.items()}})
    return edges, routes


def oriented(prof, u, v, route):
    """The edge profile oriented along the route (base -> top), using the route's node order."""
    order = {n: k for k, n in enumerate(route["nodes"])}
    if u in order and v in order and order[u] > order[v]:
        return prof[::-1]
    return prof


# --------------------------------------------------------------------------------------------- landings
def with_landings(prof, cfg):
    """Insert flat landings into a 3D profile. Returns (profile (N, 3) at ~0.1 m, [landing dicts])."""
    r = st.resample(prof, 0.1)
    if r is None:
        return np.asarray(prof, float), []
    s, x, y, z = r
    L, M, every = cfg["length_m"], cfg["margin_m"], cfg["every_rise_m"]
    S = s[-1]
    zf = lambda q: np.interp(q, s, z)  # noqa: E731
    cents, last, c = [], z[0], M + L / 2
    while c <= S - M - L / 2:
        a, b = c - L / 2 - M, c + L / 2 + M
        grade = abs(zf(b) - zf(a)) / (b - a)
        if abs(zf(c) - last) >= every and grade >= cfg["min_grade"]:
            cents.append(c)
            last = zf(c)
            c += L + 2 * M
        else:
            c += 0.1
    z2 = z.copy()
    for c in cents:
        a, b = c - L / 2, c + L / 2
        pre = (s >= a - M) & (s < a)
        z2[pre] = zf(a - M + (s[pre] - (a - M)) * (c - (a - M)) / M)
        z2[(s >= a) & (s <= b)] = zf(c)
        post = (s > b) & (s <= b + M)
        z2[post] = zf(c + (s[post] - b) * ((b + M) - c) / M)
    out = np.column_stack([x, y, z2])
    land = []
    for c in cents:
        p = np.array([np.interp(c, s, x), np.interp(c, s, y)])
        q = np.array([np.interp(c + 0.2, s, x), np.interp(c + 0.2, s, y)]) - \
            np.array([np.interp(c - 0.2, s, x), np.interp(c - 0.2, s, y)])
        t = q / max(np.linalg.norm(q), 1e-9)
        land.append({"xy": p.tolist(), "z": float(zf(c)), "t": t.tolist(), "s": float(c)})
    return out, land


def paint_houses(buildings, corridor, cfg, key="esc"):
    """Facade paint on the favela houses within reach of the stair (most of them, colours from cfg).
    In place on the buildings frame; returns how many were painted."""
    near = buildings.geometry.distance(corridor) <= cfg["reach_m"]
    n = 0
    for i in np.flatnonzero((near & buildings["informal"]).to_numpy()):
        idx = buildings.index[i]
        u = stable_unit((key, str(buildings.at[idx, "osmid"]), i))
        if u < cfg["share"]:
            mats = cfg["materials"]
            buildings.at[idx, "material"] = mats[int(stable_unit((key, "c", str(buildings.at[idx, "osmid"]), i))
                                                     * len(mats))]
            n += 1
    return n


# --------------------------------------------------------------------------------------------- meshes
def _frame(prof):
    p = np.asarray(prof, float)
    seg = np.hypot(*np.diff(p[:, :2], axis=0).T)
    s = np.r_[0.0, np.cumsum(seg)]
    t = np.gradient(p[:, :2], axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    left = np.column_stack([-t[:, 1], t[:, 0]])
    return p, s, t, left


def _at(p, s, q):
    return np.array([np.interp(q, s, p[:, 0]), np.interp(q, s, p[:, 1]), np.interp(q, s, p[:, 2])])


def _tread_top(p, s, q, half=0.35):
    """Height of the tread surface around station q (treads take the highest profile point of their run)."""
    m = (s >= q - half) & (s <= q + half)
    return float(p[m, 2].max()) if m.any() else float(np.interp(q, s, p[:, 2]))


def _paint(top, z0, k, paint):
    """Tread colour: 'alternate' cycles with the tread count k along the route, 'bands' by height above z0."""
    cols = paint["colours"]
    if paint["mode"] == "alternate":
        return cols[k % len(cols)]
    return cols[int(np.floor((top - z0) / paint["band_m"])) % len(cols)]


def parts(esc, houses, terrain, cfg, rise):
    """[(material, verts, tris, anchor)] for one escadaria (an entry of escadarias.json), plus markers and stats."""
    out, markers = [], []
    W = esc["width"]
    z0 = min(min(pt[2] for pt in e["profile"]) for e in esc["edges"])
    stats = {"treads": 0, "landings": 0, "rail_m": 0.0, "lamps": 0, "benches": 0, "murals": 0, "bars": 0}
    lamp_at, lamp_every = cfg["lamps"]["spacing_m"] / 2, cfg["lamps"]["spacing_m"]
    route_s, tread_k, last_top, lamps = 0.0, -1, None, []
    for e in esc["edges"]:
        p, s, t, left = _frame(e["profile"])
        if s[-1] < 0.3:
            continue
        # painted treads
        blocks, info = st.step_blocks(p, W, rise, depth=cfg["tread_depth_m"])
        for v, tr in blocks:  # long treads come as several blocks with the same top: one tread, one colour
            v = np.asarray(v, float)
            top = float(v[:, 2].max())
            if last_top is None or abs(top - last_top) > 0.02:
                tread_k += 1
            last_top = top
            out.append((_paint(top, z0, tread_k, esc["paint"]), v, tr, v[:, :2].mean(axis=0)))
        stats["treads"] += info["steps"]
        # handrail on the left going up: posts and a tube between their tops
        rc = cfg["rail"]
        off = W / 2 - rc["inset_m"]
        qs = np.linspace(0.2, s[-1] - 0.2, max(2, int(np.ceil((s[-1] - 0.4) / rc["post_spacing_m"])) + 1))
        tops = []
        for q in qs:
            k = min(int(np.searchsorted(s, q)), len(s) - 1)
            c = _at(p, s, q)
            xy = c[:2] + left[k] * off
            zt = _tread_top(p, s, q)
            yaw = float(np.arctan2(t[k, 1], t[k, 0]))
            out.append(("M_Railing", *box(xy, yaw, (0.05, 0.05), zt - 0.2, zt + rc["height_m"]), xy))
            tops.append(np.array([xy[0], xy[1], zt + rc["height_m"]]))
        for a, b in zip(tops[:-1], tops[1:]):
            v, tr = wire(a, b, 0.0, rc["radius_m"], 1)
            out.append(("M_Railing", v, tr, a[:2]))
            stats["rail_m"] += float(np.linalg.norm(b - a))
        # lamp posts on the right, every spacing_m along the whole route
        lc = cfg["lamps"]
        while lamp_at <= route_s + s[-1]:
            q = lamp_at - route_s
            k = min(int(np.searchsorted(s, q)), len(s) - 1)
            c = _at(p, s, q)
            xy = c[:2] - left[k] * (W / 2 - 0.15)
            zt = _tread_top(p, s, q)
            yaw = float(np.arctan2(t[k, 1], t[k, 0]))
            arm, head = xy + left[k] * 0.35, xy + left[k] * 0.65
            lamps.append((xy, [("M_Pole", *box(xy, yaw, (0.12, 0.12), zt - 0.2, zt + lc["height_m"]), xy),
                               ("M_Pole", *box(arm, yaw + np.pi / 2, (0.7, 0.06), zt + lc["height_m"] - 0.08,
                                               zt + lc["height_m"]), xy),
                               ("M_Lamp", *box(head, yaw, (0.28, 0.18), zt + lc["height_m"] - 0.22,
                                               zt + lc["height_m"] - 0.06), xy)]))
            stats["lamps"] += 1
            lamp_at += lamp_every
        route_s += s[-1]
        # benches on every n-th landing, against the right side
        for ld in e["landings"]:
            stats["landings"] += 1
            if cfg["bench_every_landing"] and stats["landings"] % cfg["bench_every_landing"] == 1 % cfg["bench_every_landing"]:
                tt = np.asarray(ld["t"])
                lf = np.array([-tt[1], tt[0]])
                c = np.asarray(ld["xy"]) - lf * (W / 2 - 0.35)
                for v, tr in bench(c, float(np.arctan2(tt[1], tt[0])), ld["z"], {"size_m": [1.4, 0.42, 0.45]}):
                    out.append(("M_Bench", v, tr, c))
                stats["benches"] += 1
    # murals on house walls facing the stair
    line = shapely.union_all([LineString(np.asarray(e["profile"])[:, :2]) for e in esc["edges"]])
    corridor = line.buffer(W / 2)
    mc = cfg["murals"]
    for i in houses.tree.query(corridor.buffer(mc["reach_m"]), predicate="intersects"):
        poly = orient(shapely.simplify(houses.geoms[i], 0.2, preserve_topology=True), 1.0)
        if poly.geom_type != "Polygon":
            continue
        ring = np.asarray(poly.exterior.coords)[:, :2]
        best = None
        for a, b in zip(ring[:-1], ring[1:]):
            L = float(np.hypot(*(b - a)))
            if L < mc["min_wall_m"]:
                continue
            tw = (b - a) / L
            nw = np.array([tw[1], -tw[0]])
            m = (a + b) / 2
            if corridor.distance(Point(*m)) > mc["reach_m"] or houses.at(*(m + nw * 0.5), exclude=i) is not None:
                continue
            # the wall must face the stair: its outward normal points at the nearest stair point
            q = np.asarray(line.interpolate(line.project(Point(*m))).coords[0])[:2]
            if np.dot(q - m, nw) < 0.5 * np.linalg.norm(q - m):
                continue
            if best is None or L > best[0]:
                best = (L, a, b, tw, nw)
        if best is None:
            continue
        L, a, b, tw, nw = best
        key = ("mural", houses.ids[i], round(a[0], 1), round(a[1], 1))
        if stable_unit(key) >= mc["share"]:
            continue
        zb = max(_z(terrain, *a), _z(terrain, *b)) + 0.35
        ztop = min(houses.top[i] - 0.3, zb + mc["height_m"])
        if ztop - zb < 1.4:
            continue
        w = L - 0.4
        mat = mc["materials"][int(stable_unit(key + ("mat",)) * len(mc["materials"]))]
        c = (a + b) / 2 + nw * 0.03
        out.append((mat, *box(c, float(np.arctan2(tw[1], tw[0])), (w, 0.04), zb, ztop), c))
        stats["murals"] += 1
    # bars at landings: counter, awning and sign on the nearest house wall beside the landing, a table + stools
    lands = [ld for e in esc["edges"] for ld in e["landings"]]
    nb = int(esc.get("bars", 0))
    if nb and lands:
        want = [len(lands) * (k + 1) / (nb + 1) for k in range(nb)]
        used = set()
        for wk in want:
            order = sorted(range(len(lands)), key=lambda j: abs(j - wk))
            for j in order:
                if j in used:
                    continue
                bar = _bar(lands[j], W, houses, esc["name"], cfg["bars"])
                if bar is None:
                    continue
                used.add(j)
                out += bar["parts"]
                stats["bars"] += 1
                markers.append({"name": f"{esc['name']}: bar {stats['bars']}", "kind": "bar",
                                "xy": bar["xy"], "z": lands[j]["z"]})
                break
    bars = [np.asarray(m["xy"]) for m in markers if m["kind"] == "bar"]
    for xy, lp in lamps:  # no lamp post through a bar's awning
        if all(np.hypot(*(xy - b)) > cfg["bars"]["awning_m"] + cfg["bars"]["counter_m"] for b in bars):
            out += lp
        else:
            stats["lamps"] -= 1
    base, top = esc["edges"][0]["profile"][0], esc["edges"][-1]["profile"][-1]
    markers.append({"name": f"{esc['name']}: base", "kind": "escadaria", "xy": base[:2], "z": base[2]})
    markers.append({"name": f"{esc['name']}: topo", "kind": "escadaria", "xy": top[:2], "z": top[2]})
    stats["rail_m"] = round(stats["rail_m"])
    return out, markers, stats


def _bar(ld, W, houses, name, cfg):
    c, z = np.asarray(ld["xy"]), ld["z"]
    tt = np.asarray(ld["t"])
    lf = np.array([-tt[1], tt[0]])
    best = None
    for side in (-1, 1):  # right side first: the rail runs on the left
        probe = c + lf * side * (W / 2 + 0.6)
        for i in houses.tree.query(Point(*probe).buffer(1.5), predicate="intersects"):
            if not houses.informal[i] or houses.top[i] - z < 3.2:
                continue
            d = houses.geoms[i].distance(Point(*c))
            if d <= W / 2 + 1.0 and (best is None or (side, d) < best[:2]):
                best = (side, d, int(i))
        if best is not None:
            break
    if best is None:
        return None
    side, _, i = best
    wall = c + lf * side * (W / 2)
    # wall tangent = stair tangent (houses line the stair); the bar faces into the stair (-side * lf)
    face = -lf * side
    yaw = float(np.arctan2(tt[1], tt[0]))
    P = []
    P.append(("M_BarCounter", *box(wall + face * 0.25, yaw, (cfg["counter_m"], 0.45), z - 0.1, z + 1.05), c))
    P.append(("M_Awning", *box(wall + face * cfg["awning_m"] / 2, yaw, (cfg["counter_m"] + 0.8, cfg["awning_m"]),
                               z + 2.45, z + 2.55), c))
    P.append(("M_BarSign", *box(wall + face * 0.05, yaw, (cfg["counter_m"], 0.06), z + 2.6, z + 3.1), c))
    tc = wall + face * 1.25 + tt * (0.9 if stable_unit((name, "bar", round(c[0]), round(c[1]))) < 0.5 else -0.9)
    P.append(("M_Plastic", *box(tc, yaw, (0.6, 0.6), z + 0.68, z + 0.72), c))
    P.append(("M_Plastic", *box(tc, yaw, (0.08, 0.08), z, z + 0.68), c))
    for o in (-0.5, 0.5):
        P.append(("M_Plastic", *box(tc + tt * o, yaw, (0.34, 0.34), z, z + 0.44), c))
    return {"parts": P, "xy": (wall + face * 0.8).tolist()}
