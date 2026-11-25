"""Beach life on the hero beach: barracas (vendor stalls) with their umbrellas, chairs and towels, plus
volleyball / footvolley courts. Placed in the same centreline + profile frame as rio_geo/beach.py, so it
regenerates on the compressed map's beach unchanged.

Layout across the sand (offsets seaward of the calcadao edge):
  courts band   from_sand_edge_m .. + court width   (open sand next to the calcadao, as in Ipanema / Leblon)
  barraca line  max(barraca.min_from_sand_edge_m, barraca.offset_frac x sand width)
  umbrellas     from the barraca line to keep_from_water_m before the waterline, in loose rows
Each barraca "owns" the stretch of beach around it and its umbrellas mostly share its colour, as the rented
umbrellas of one stall do. Occupancy follows a density curve along the beach that peaks at the busy Postos
(life.posto_density, Gaussian falloff posto_sigma_m) on top of density_base.

All objects go to a separate TiledMesh (kind "BeachLife", no collision): props should not block walking.
"""
import numpy as np

from .beach import box
from .common import stable_unit

UMBRELLA_MATS = ["M_UmbrellaRed", "M_UmbrellaYellow", "M_UmbrellaBlue", "M_UmbrellaGreen", "M_UmbrellaWhite",
                 "M_UmbrellaOrange"]


def _r(*key):
    return stable_unit(("life",) + key)


def _rng(lo_hi, *key):
    lo, hi = lo_hi
    return lo + (hi - lo) * _r(*key)


def _rot(yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, -s], [s, c]])


def quad(p, double=False):
    v = np.asarray(p, float)
    t = [(0, 1, 2), (0, 2, 3)]
    if double:
        t += [(0, 2, 1), (0, 3, 2)]
    return v, np.array(t)


def flat_rect(c, yaw, size, z):
    sx, sy = size[0] / 2, size[1] / 2
    xy = np.array([[-sx, -sy], [sx, -sy], [sx, sy], [-sx, sy]]) @ _rot(yaw).T + c[:2]
    return quad(np.column_stack([xy, np.full(4, z)]))


def umbrella(base, radius, height, tilt_dir, tilt, sides=8):
    """Pole + octagonal canopy (double-sided), canopy tilted by `tilt` radians towards tilt_dir (xy unit)."""
    pole_v, pole_t = box(base, 0.0, (0.05, 0.05), base[2] - 0.25, base[2] + height)
    apex = np.array([0.0, 0.0, height])
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    rim = np.column_stack([radius * np.cos(ang), radius * np.sin(ang), np.full(sides, height - 0.35)])
    pts = np.vstack([apex, rim])
    # tilt: rotate about the horizontal axis perpendicular to tilt_dir, around the pole top's base point
    ax = np.array([-tilt_dir[1], tilt_dir[0], 0.0])
    k = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    R = np.eye(3) + np.sin(tilt) * k + (1 - np.cos(tilt)) * (k @ k)
    pivot = np.array([0.0, 0.0, height - 0.6])
    pts = (pts - pivot) @ R.T + pivot + base
    tris = []
    for a in range(sides):
        b = (a + 1) % sides
        tris += [(0, 1 + a, 1 + b), (0, 1 + b, 1 + a)]
    return (pole_v, pole_t), (pts, np.array(tris))


def chair(c, yaw, z):
    """Low folding beach chair facing +yaw: frame seat box + tilted fabric back (double-sided quad)."""
    seat = box(c, yaw, (0.5, 0.45), z - 0.05, z + 0.25)
    R = _rot(yaw)
    back = np.array([[-0.25, -0.22], [-0.25, 0.22]])  # rear edge (local x = forward)
    lo = back @ R.T + c[:2]
    hi = (back + [-0.22, 0.0]) @ R.T + c[:2]
    fabric = quad([[*lo[0], z + 0.25], [*lo[1], z + 0.25], [*hi[1], z + 0.78], [*hi[0], z + 0.78]], double=True)
    return seat, fabric


def barraca(c, yaw, z, cfg, key):
    """Vendor stall: counter, four posts, a pyramid roof in the stall's colour, a stack of folded chairs."""
    sz = cfg["size_m"]
    parts = [("M_Barraca", box(c, yaw, sz[:2], z - 0.2, z + sz[2]))]
    rx, ry = cfg["roof_m"][0] / 2, cfg["roof_m"][1] / 2
    h = _rng(cfg["roof_h_m"], "bh", key)
    R = _rot(yaw)
    corners = np.array([[-rx, -ry], [rx, -ry], [rx, ry], [-rx, ry]]) * 0.92 @ R.T + c[:2]
    for p in corners:
        parts.append(("M_ChairFrame", box(p, yaw, (0.06, 0.06), z - 0.2, z + h)))
    rim = np.column_stack([np.array([[-rx, -ry], [rx, -ry], [rx, ry], [-rx, ry]]) @ R.T + c[:2], np.full(4, z + h)])
    apex = np.array([c[0], c[1], z + h + 0.7])
    pv = np.vstack([apex, rim])
    pt = []
    for a in range(4):
        b = (a + 1) % 4
        pt += [(0, 1 + a, 1 + b), (0, 1 + b, 1 + a)]
    parts.append(("ROOF", (pv, np.array(pt))))
    stack = c[:2] + R @ np.array([0.0, sz[1] / 2 + 0.6])
    parts.append(("M_ChairFrame", box(stack, yaw, (0.55, 0.12), z, z + 1.1)))
    return parts


def court(c, yaw, z, size, net_h, kind):
    """Sand court, long axis along yaw: boundary tape, two posts, net across the middle (double-sided)."""
    L, W = size
    R = _rot(yaw)
    parts = []
    for (dx, dy, lx, ly) in ((0, -W / 2, L, 0.06), (0, W / 2, L, 0.06), (-L / 2, 0, 0.06, W), (L / 2, 0, 0.06, W)):
        parts.append(("M_CourtLine", flat_rect(c[:2] + R @ np.array([dx, dy]), yaw, (lx, ly), z + 0.03)))
    ends = [c[:2] + R @ np.array([0.0, s * (W / 2 + 0.5)]) for s in (-1, 1)]
    for p in ends:
        parts.append(("M_NetPost", box(p, yaw, (0.08, 0.08), z - 0.3, z + net_h + 0.1)))
    (a, b) = ends
    parts.append(("M_Net", quad([[*a, z + net_h - 0.9], [*b, z + net_h - 0.9], [*b, z + net_h], [*a, z + net_h]],
                                double=True)))
    return parts


def build_life(cl, dbg, sand_z, tm, cfg, postos, kiosks, features=None):
    """Fill tm (a beach.TiledMesh) with beach life. dbg: build_geometry's debug arrays (calc_out, water, canal,
    palms). sand_z(i, offset) -> sand surface height. features: beach.gpkg features (OSM courts) or None.
    Returns statistics."""
    calc_out, water, canal = dbg["calc_out"], dbg["water"], dbg["canal"]
    n = len(cl.s)
    step = cl.s[1] - cl.s[0]
    stats = {"barracas": 0, "umbrellas": 0, "chairs": 0, "towels": 0, "courts_osm": 0, "courts_procedural": 0}

    def idx(s):
        return int(np.clip(np.searchsorted(cl.s, s), 0, n - 1))

    def point(s, o):
        i = idx(s)
        return i, cl.xy[i] + cl.nrm[i] * o + cl.t[i] * (s - cl.s[i])

    def near_canal(i, margin_m):
        k = int(np.ceil(margin_m / step))
        return bool(canal[max(i - k, 0):i + k + 1].any())

    named = {p["name"]: cl.s[p["i"]] for p in postos}
    peaks = [(named[nm], d) for nm, d in cfg["posto_density"].items() if nm in named]

    def density(s):
        d = cfg["density_base"]
        for sp, peak in peaks:
            d = max(d, cfg["density_base"] + (peak - cfg["density_base"]) * np.exp(-((s - sp) / cfg["posto_sigma_m"]) ** 2))
        return d

    blockers = [cl.s[q["i"]] for q in postos] + [cl.s[q["i"]] for q in kiosks if "xyz" in q]
    blockers = np.array(blockers or [-1e9])

    def blocked(s, clear):
        return np.min(np.abs(blockers - s)) < clear

    def add(mat, vt, anchor):
        tm.add_object(mat, vt[0], vt[1], anchor)

    # ---- courts: OSM volleyball pitches on the sand first, then procedural fill
    cc = cfg["courts"]
    court_s = []
    if features is not None and len(features):
        f = features[features["leisure"].astype(str).eq("pitch")
                     & features["sport"].astype(str).str.contains("volley")]
        for _, r in f.iterrows():
            i, o, along = cl.project(r.geometry.x, r.geometry.y)
            if not (calc_out[i] < o < water[i]) or near_canal(i, cc["clear_m"]):
                continue
            court_s.append((cl.s[i] + along, "osm", "volley"))
    s = cc["spacing_m"] / 2
    while s < cl.length - cc["spacing_m"] / 4:
        st = s + (_r("court", round(s)) - 0.5) * 2 * cc["jitter_m"]
        s += cc["spacing_m"]
        if any(abs(st - c[0]) < cc["spacing_m"] * 0.6 for c in court_s):
            continue
        court_s.append((st, "procedural", "footvolley" if _r("ckind", round(st)) < cc["footvolley_p"] else "volley"))
    courts_xy = []
    for st, src, kind in sorted(court_s):
        size = cc["footvolley_m"] if kind == "footvolley" else cc["volley_m"]
        i = idx(st)
        if blocked(st, cc["clear_m"] + size[0] / 2) or near_canal(i, cc["clear_m"] + size[0] / 2):
            continue
        o = calc_out[i] + cc["from_sand_edge_m"] + size[1] / 2
        if o + size[1] / 2 > water[i] - 15:
            continue
        i, c = point(st, o)
        yaw = np.arctan2(cl.t[i, 1], cl.t[i, 0])
        z = sand_z(i, o)
        for mat, vt in court(c, yaw, z, size, cc["net_h_m"][kind], kind):
            add(mat, vt, c)
        courts_xy.append(c)
        stats["courts_osm" if src == "osm" else "courts_procedural"] += 1

    # ---- barracas + their umbrellas
    bc, uc = cfg["barraca"], cfg["umbrella"]
    barracas_xy, umbrellas_xy = [], []
    s = bc["spacing_m"] / 2
    k_b = 0
    while s < cl.length - bc["spacing_m"] / 4:
        sb = s + (_r("barraca", round(s)) - 0.5) * 2 * bc["jitter_m"]
        s += bc["spacing_m"]
        i = idx(sb)
        width = water[i] - calc_out[i]
        if near_canal(i, bc["clear_m"]) or blocked(sb, bc["clear_m"]) or width < bc["min_sand_width_m"]:
            continue
        ob = calc_out[i] + max(bc["min_from_sand_edge_m"], bc["offset_frac"] * width)
        home = UMBRELLA_MATS[int(_r("home", round(sb)) * len(UMBRELLA_MATS))]
        dens = density(sb)
        if _r("bskip", round(sb)) > min(1.0, dens * bc["presence_gain"]):
            continue
        i, c = point(sb, ob)
        yaw = np.arctan2(cl.t[i, 1], cl.t[i, 0])
        z = sand_z(i, ob)
        for mat, vt in barraca(c, yaw, z, bc, round(sb)):
            add(home if mat == "ROOF" else mat, vt, c)
        barracas_xy.append(c)
        stats["barracas"] += 1
        k_b += 1
        # umbrella field owned by this barraca: along-shore +-spacing/2, seaward from the barraca line
        half = bc["spacing_m"] / 2
        a_vals = np.arange(-half + uc["col_m"] / 2, half, uc["col_m"])
        o_far = water[i] - uc["keep_from_water_m"]
        o_vals = np.arange(ob + uc["first_row_m"], o_far, uc["row_m"])
        for ro, o0 in enumerate(o_vals):
            t = (o0 - ob) / max(o_far - ob, 1.0)
            for a0 in a_vals:
                key = (round(sb), ro, round(a0, 1))
                p_occ = dens * (uc["near_weight"] + (1 - uc["near_weight"]) * (1 - t))
                if _r("occ", *key) > p_occ:
                    continue
                su = sb + a0 + (_r("ja", *key) - 0.5) * 2 * uc["jitter_m"]
                ou = o0 + (_r("jo", *key) - 0.5) * 2 * uc["jitter_m"]
                iu = idx(su)
                if near_canal(iu, bc["clear_m"]) or blocked(su, uc["clear_m"]) or ou > water[iu] - uc["keep_from_water_m"]:
                    continue
                iu, cu = point(su, ou)
                zu = sand_z(iu, ou)
                own = _r("own", *key) < uc["home_colour_p"]
                mat = home if own else UMBRELLA_MATS[int(_r("col", *key) * len(UMBRELLA_MATS))]
                if _r("towel_only", *key) < uc["towel_only_p"]:  # no umbrella, just people on towels
                    for q in range(1 + int(_r("nt", *key) * 2)):
                        ty = np.arctan2(cl.nrm[iu, 1], cl.nrm[iu, 0]) + (_r("ty", *key, q) - 0.5) * 0.6
                        tc = cu + cl.t[iu] * (q - 0.5) * 1.1
                        add(UMBRELLA_MATS[int(_r("tc", *key, q) * len(UMBRELLA_MATS))],
                            flat_rect(tc, ty, (1.8, 0.9), zu + 0.03), tc)
                        stats["towels"] += 1
                    continue
                tilt_dir = -cl.nrm[iu] if _r("tdir", *key) < 0.5 else cl.t[iu] * (1 if _r("ts", *key) < 0.5 else -1)
                pole, canopy = umbrella(np.r_[cu, zu], _rng(uc["radius_m"], "rad", *key),
                                        _rng(uc["height_m"], "h", *key), tilt_dir,
                                        np.radians(uc["tilt_deg"]) * _r("tilt", *key))
                add("M_ChairFrame", pole, cu)
                add(mat, canopy, cu)
                umbrellas_xy.append(cu)
                stats["umbrellas"] += 1
                face = np.arctan2(cl.nrm[iu, 1], cl.nrm[iu, 0])  # chairs face the sea
                n_ch = int(_rng(uc["chairs"], "nch", *key) + 0.5)
                for q in range(n_ch):
                    side = (q - (n_ch - 1) / 2) * 0.65
                    ch = cu + cl.t[iu] * side + cl.nrm[iu] * 0.35
                    seat, fabric = chair(ch, face + (_r("cy", *key, q) - 0.5) * 0.5, zu)
                    add("M_ChairFrame", seat, ch)
                    add(mat, fabric, ch)
                    stats["chairs"] += 1
    stats["density_peaks"] = {nm: d for nm, d in cfg["posto_density"].items() if nm in named}
    stats["_debug"] = {"barracas": np.array(barracas_xy).reshape(-1, 2), "umbrellas": np.array(umbrellas_xy).reshape(-1, 2),
                       "courts": np.array(courts_xy).reshape(-1, 2)}
    return stats
