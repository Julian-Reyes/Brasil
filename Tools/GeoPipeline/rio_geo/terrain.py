"""Terrain: DSM -> ground estimate -> fine grid -> road-conditioned heightfield."""
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from scipy import ndimage


class Heightfield:
    """Regular grid in project CRS. z[iy, ix] at (west + ix*step, south + iy*step); iy grows northward."""

    def __init__(self, west, south, step, z):
        self.west, self.south, self.step, self.z = west, south, step, z

    @property
    def shape(self):
        return self.z.shape

    def coords(self):
        ny, nx = self.z.shape
        return self.west + np.arange(nx) * self.step, self.south + np.arange(ny) * self.step

    def sample(self, e, n, order=1, z=None):
        z = self.z if z is None else z
        cols = (np.asarray(e) - self.west) / self.step
        rows = (np.asarray(n) - self.south) / self.step
        return ndimage.map_coordinates(z, [rows, cols], order=order, mode="nearest")

    def gradient_slope(self):
        gy, gx = np.gradient(self.z, self.step)
        return np.hypot(gx, gy)


def load_dsm_window(dem_path, bounds, margin):
    w, s, e, n = bounds
    with rasterio.open(dem_path) as src:
        win = from_bounds(w - margin, s - margin, e + margin, n + margin, src.transform).round_offsets().round_lengths()
        data = src.read(1, window=win).astype(np.float64)
        transform = src.window_transform(win)
    return data, transform


def ground_from_dsm(dsm, transform, footprints, heights, gain=1.2, blur_m=15.0,
                    lowland_max_m=15.0, protect=None):
    """Copernicus GLO-30 is a surface model. Remove the built-up surface in two passes:

    1. Subtract the mapped building volume: mean building height per DEM cell (rasterised at 1/5 of the
       cell size), blurred to the radar's effective resolution. Slope-independent, so hillside
       neighbourhoods keep their shape. `gain` > 1 compensates for the blur spreading rooftops into streets.
    2. On the coastal lowland only (Leblon/Ipanema plain), take the minimum with a 150 m morphological
       opening to remove the residual roof signal. Never applied on slopes, where it would cut terraces.

    Sea (DSM <= 0.05, connected to the raster edge) becomes a gently sloping seabed.

    `protect`: polygons exempt from step 2 (coastal headlands: the Forte de Copacabana peninsula is ~120 m wide,
    so the 150 m opening flattened its 12-18 m of real relief to sea level).
    """
    res = transform.a
    shape = dsm.shape
    f = 5
    fine = rasterize(((g, h) for g, h in zip(footprints, heights)), out_shape=(shape[0] * f, shape[1] * f),
                     transform=transform * transform.scale(1 / f), fill=0, dtype="float32")
    mean_h = fine.reshape(shape[0], f, shape[1], f).mean(axis=(1, 3)).astype(np.float64)
    ground = dsm - gain * ndimage.gaussian_filter(mean_h, blur_m / res)

    size = max(3, int(round(150.0 / res)) | 1)
    opened = ndimage.gaussian_filter(ndimage.grey_opening(ground, size=(size, size)), sigma=20.0 / res)
    # Lowland = where the roof-free (opened) surface is low. A slope test does not work here: residual rooftop
    # bumps make the dense Leblon/Ipanema plain look sloped. Hills keep a high opened surface, so are untouched.
    w_low = np.clip((lowland_max_m - opened) / 5.0, 0, 1)
    w_low = ndimage.gaussian_filter(w_low, 20.0 / res)
    if protect:
        pm = rasterize(((g, 1) for g in protect), out_shape=shape, transform=transform, fill=0, dtype="uint8")
        w_low = w_low * (1 - np.clip(ndimage.gaussian_filter(pm.astype(float), 10.0 / res) * 2, 0, 1))
    ground = ground * (1 - w_low) + np.minimum(ground, opened) * w_low

    water = dsm <= 0.05
    labels, _ = ndimage.label(water)
    edge_labels = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    sea = np.isin(labels, edge_labels[edge_labels > 0])
    dist_m = ndimage.distance_transform_edt(sea) * res
    land_floor = 0.5
    ground = np.where(sea, -np.minimum(1.5 + dist_m * 0.04, 25.0), np.maximum(ground, land_floor))
    return ground, sea


def resample_to_grid(src, src_transform, bounds, step):
    """Cubic resample of a raster window onto a regular grid aligned to `bounds`."""
    w, s, e, n = bounds
    xs = np.arange(w, e + step * 0.5, step)
    ys = np.arange(s, n + step * 0.5, step)
    X, Y = np.meshgrid(xs, ys)
    inv = ~src_transform
    col, row = inv * (X, Y)
    z = ndimage.map_coordinates(src, [row - 0.5, col - 0.5], order=3, mode="nearest")
    return Heightfield(w, s, step, z)


def carve_roads(hf, samples, chunk=4000):
    """Flatten the heightfield under road corridors and blend back to natural ground.

    samples: structured fields e, n, z, half_width, shoulder, blend, rank (one row per ~2 m of centreline).
    For each grid vertex the strongest influence wins (weight, then road rank), so intersections take the
    height of the more important road and narrow becos never override an avenue.
    Returns (carved_z, weight).
    """
    ny, nx = hf.shape
    best_score = np.full(ny * nx, -1.0)
    best_target = np.zeros(ny * nx)
    best_w = np.zeros(ny * nx)
    reach = samples["half_width"] + samples["shoulder"] + samples["blend"]
    kmax = int(np.ceil(reach.max() / hf.step)) + 1
    off = np.arange(-kmax, kmax + 1)
    dx, dy = [a.ravel() for a in np.meshgrid(off, off)]
    for start in range(0, len(samples), chunk):
        s = samples[start:start + chunk]
        cx = np.rint((s["e"] - hf.west) / hf.step).astype(np.int64)
        cy = np.rint((s["n"] - hf.south) / hf.step).astype(np.int64)
        ix = cx[:, None] + dx[None, :]
        iy = cy[:, None] + dy[None, :]
        d = np.hypot(hf.west + ix * hf.step - s["e"][:, None], hf.south + iy * hf.step - s["n"][:, None])
        flat = (s["half_width"] + s["shoulder"])[:, None]
        t = np.clip((d - flat) / s["blend"][:, None], 0, 1)
        w = 1 - t * t * (3 - 2 * t)
        ok = (w > 0) & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        cell = (iy * nx + ix)[ok]
        score = (w + s["rank"][:, None] * 1e-3 - d * 1e-7)[ok]
        target = np.broadcast_to(s["z"][:, None], w.shape)[ok]
        wv = w[ok]
        order = np.lexsort((score, cell))
        cell, score, target, wv = cell[order], score[order], target[order], wv[order]
        last = np.r_[cell[1:] != cell[:-1], True]
        cell, score, target, wv = cell[last], score[last], target[last], wv[last]
        better = score > best_score[cell]
        best_score[cell[better]] = score[better]
        best_target[cell[better]] = target[better]
        best_w[cell[better]] = wv[better]
    z = hf.z.ravel()
    carved = z * (1 - best_w) + best_target * best_w
    return carved.reshape(ny, nx), best_w.reshape(ny, nx)


def grid_mesh(hf_z, west, south, step, ix0, ix1, iy0, iy1, pivot_e, pivot_n):
    """Triangulated grid for vertex index ranges [ix0, ix1] x [iy0, iy1] (inclusive)."""
    sub = hf_z[iy0:iy1 + 1, ix0:ix1 + 1]
    ny, nx = sub.shape
    xs = west + np.arange(ix0, ix1 + 1) * step - pivot_e
    ys = south + np.arange(iy0, iy1 + 1) * step - pivot_n
    X, Y = np.meshgrid(xs, ys)
    verts = np.column_stack([X.ravel(), Y.ravel(), sub.ravel()])
    gy, gx = np.gradient(sub, step)
    normals = np.column_stack([-gx.ravel(), -gy.ravel(), np.ones(sub.size)])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
    c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
    tris = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
    return verts, tris, normals


# --------------------------------------------------------------------------------------------- inland water
def select_water_bodies(ref_polygons, bbox, wcfg):
    """Inland water polygons (lagoons, lakes, canals, ...) from the OSM reference layer that touch the AOI.
    The sea is handled separately by ground_from_dsm (DSM <= 0.05 connected to the raster edge)."""
    import shapely
    kinds = set(wcfg["kinds"])
    is_water = ref_polygons["water"].astype(str).isin(kinds) | (
        ref_polygons["natural"].astype(str).eq("water") & ~ref_polygons["water"].astype(str).isin(wcfg["exclude"]))
    sel = ref_polygons[is_water & (ref_polygons.area >= wcfg["min_area_m2"])]
    sel = sel[sel.intersects(shapely.box(*bbox))]
    return sel


def water_level(poly, dsm, transform, name, wcfg):
    """Configured level, else the median DSM inside the polygon (Copernicus flattens water surfaces)."""
    override = wcfg["levels_m"].get(str(name))
    if override is not None:
        return float(override), "config"
    inset = poly.buffer(-wcfg["dsm_inset_m"])
    if inset.is_empty:
        inset = poly
    m = rasterize([inset], out_shape=dsm.shape, transform=transform, fill=0, default_value=1, dtype="uint8") > 0
    if m.sum() < 3:
        return float(wcfg["default_level_m"]), "default"
    return float(np.median(dsm[m])), "dsm"


def carve_water(hf, bodies, wcfg):
    """Lower the ground inside each water polygon to a bed below its surface: shore_depth at the edge, then
    bed_slope per metre from the shore, down to max_depth. bodies: [(polygon, level)]. Returns a mask of cells
    that are under water (used for statistics)."""
    import shapely
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    wet = np.zeros(hf.shape, bool)
    for poly, level in bodies:
        w, s, e, n = poly.bounds
        win = (X >= w - hf.step) & (X <= e + hf.step) & (Y >= s - hf.step) & (Y <= n + hf.step)
        if not win.any():
            continue
        inside = np.zeros(hf.shape, bool)
        inside[win] = shapely.contains_xy(poly, X[win], Y[win])
        if not inside.any():
            continue
        dist = ndimage.distance_transform_edt(inside) * hf.step
        depth = np.minimum(wcfg["shore_depth_m"] + dist * wcfg["bed_slope"], wcfg["max_depth_m"])
        hf.z = np.where(inside, np.minimum(hf.z, level - depth), hf.z)
        wet |= inside
    return wet


def water_surface_triangles(poly, level, pivot):
    """Flat water surface for one (tile-clipped) polygon, holes (islands) preserved. Returns verts, tris."""
    import shapely
    from shapely.geometry.polygon import orient
    verts, tris = [], []
    for part in getattr(poly, "geoms", [poly]):
        if part.geom_type != "Polygon" or part.area < 1.0:
            continue
        for tri in shapely.constrained_delaunay_triangles(part).geoms:
            c = np.asarray(orient(tri, 1.0).exterior.coords)[:3, :2] - pivot
            o = len(verts)
            verts.extend((x, y, level) for x, y in c)
            tris.append((o, o + 1, o + 2))
    return np.array(verts).reshape(-1, 3), np.array(tris, dtype=np.int64).reshape(-1, 3)


def load_terrain_from_manifest(manifest, bbox, step):
    """Rebuild the generated (road-carved) terrain heightfield from build_area's Terrain tile OBJs."""
    w, s, e, n = bbox
    nx, ny = int(round((e - w) / step)) + 1, int(round((n - s) / step)) + 1
    z = np.full((ny, nx), np.nan)
    for a in manifest["actors"]:
        if a["kind"] != "Terrain":
            continue
        with open(a["obj"]) as f:
            v = np.array([ln.split()[1:4] for ln in f if ln.startswith("v ")], dtype=float) / 100.0
        pe, pn = a["pivot_utm"]
        ix = np.rint((v[:, 0] + pe - w) / step).astype(int)
        iy = np.rint((v[:, 1] + pn - s) / step).astype(int)
        z[iy, ix] = v[:, 2]
    if np.isnan(z).any():
        raise RuntimeError(f"terrain tiles incomplete: {int(np.isnan(z).sum())} missing grid vertices")
    return Heightfield(w, s, step, z)


def polygon_mask(hf, polys):
    """Boolean grid mask of cells inside any polygon."""
    import shapely
    if not polys:
        return np.zeros(hf.shape, bool)
    u = shapely.union_all(list(polys))
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    w, s, e, n = u.bounds
    win = (X >= w) & (X <= e) & (Y >= s) & (Y <= n)
    m = np.zeros(hf.shape, bool)
    m[win] = shapely.contains_xy(u, X[win], Y[win])
    return m


def rebuild_headland(hf, poly, peak_xy, ele, hcfg):
    """Rock whose relief the DSM lost (Copernicus' water mask sets the Pedra do Arpoador to 0 m). Inside `poly`
    the ground becomes  ele x smoothstep(edge distance x face_slope / ele) x (1 - summit_falloff x d_peak / d_max):
    a steep face from the waterline, highest at the OSM peak. Only raises. Returns the max height reached."""
    inside = polygon_mask(hf, [poly])
    if not inside.any():
        return None
    d_edge = ndimage.distance_transform_edt(inside) * hf.step
    t = np.clip(d_edge * hcfg["face_slope"] / ele, 0, 1)
    s = t * t * (3 - 2 * t)
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    dp = np.hypot(X - peak_xy[0], Y - peak_xy[1])
    f = 1 - hcfg["summit_falloff"] * np.clip(dp / max(dp[inside].max(), 1.0), 0, 1)
    z = ndimage.gaussian_filter(np.where(inside, ele * s * f, 0.0), 1.2)  # soften the grid-stepped faces
    hf.z = np.where(inside, np.maximum(hf.z, z), hf.z)
    return float(hf.z[inside].max())


def flatten_area(hf, poly, blend_m):
    """Set the ground inside `poly` to its median height (airport runways / aprons), blending back to the
    natural ground over blend_m outside. Returns (level, max abs change)."""
    inside = polygon_mask(hf, [poly])
    if not inside.any():
        return None, 0.0
    level = float(np.median(hf.z[inside]))
    d = ndimage.distance_transform_edt(~inside) * hf.step
    t = np.clip(d / max(blend_m, 1e-6), 0, 1)
    w = 1 - t * t * (3 - 2 * t)
    before = hf.z.copy()
    hf.z = hf.z * (1 - w) + level * w
    return level, float(np.abs(hf.z - before).max())


def cap_flat_shores(hf, anchors, ccfg, protect_mask=None):
    """Flat-shore cap. The 10 m DSM smears tall waterfront buildings, trees and the hillsides behind them onto
    the narrow coastal strips, which the building subtraction and the lowland opening do not fully remove
    (Lagoa east shore: ground 11.6 m where the road is at ~2.5 m; beachfront road bumps of 3-5 m).
    Near a flat shore (lagoon / lake / large sand beach - never plain coastline, so cliffs are untouched)
    the ground may rise at most  level + base_m + slope * distance; full effect within band_m, fading out over
    blend_m. Land in the same zone is also kept at least bank_m above the water, so the water mesh never floats
    above its own bank. anchors: [(polygon, level)] or [(polygon, level, "beach")].

    Beaches (third element "beach") get no bank floor: their outside is partly open sea, and flooring the seabed
    there raised a 30-40 m strip of fake land at level + bank_m in the surf. Instead the sand itself, which the
    DSM sea test usually classes as sea (a 2-5 m pit under the beach), becomes a ramp from beach_top_m on its
    land side down to beach_bottom_m on its sea side. Returns (z_before - z_after) for statistics."""
    import shapely
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    reach = ccfg["band_m"] + ccfg["blend_m"]
    cap = np.full(hf.shape, np.inf)
    floor = np.full(hf.shape, -np.inf)
    weight = np.zeros(hf.shape)
    beach_fill = []
    for anchor in anchors:
        poly, level = anchor[:2]
        is_beach = len(anchor) > 2 and anchor[2] == "beach"
        w, s, e, n = poly.bounds
        win = (X >= w - reach) & (X <= e + reach) & (Y >= s - reach) & (Y <= n + reach)
        if not win.any():
            continue
        inside = np.zeros(hf.shape, bool)
        inside[win] = shapely.contains_xy(poly, X[win], Y[win])
        if not inside.any():
            continue
        d = ndimage.distance_transform_edt(~inside) * hf.step
        c = level + ccfg["base_m"] + ccfg["slope"] * d
        t = np.clip((d - ccfg["band_m"]) / ccfg["blend_m"], 0, 1)
        wt = 1 - t * t * (3 - 2 * t)
        better = c < cap
        cap = np.where(better & (wt > 0), c, cap)
        if is_beach:
            beach_fill.append((inside, win))
        else:
            floor = np.where((wt > 0) & ~inside, np.maximum(floor, level + ccfg["bank_m"]), floor)
        weight = np.maximum(weight, np.where(wt > 0, wt, 0))
    if protect_mask is not None:  # coastal headlands keep their relief
        weight = np.where(protect_mask, 0.0, weight)
    before = hf.z.copy()
    capped = np.minimum(hf.z, np.where(np.isfinite(cap), cap, hf.z))
    capped = np.maximum(capped, np.where(np.isfinite(floor), floor, capped))
    hf.z = hf.z * (1 - weight) + capped * weight
    top, bottom = ccfg.get("beach_top_m", 0.6), ccfg.get("beach_bottom_m", -0.4)
    for inside, win in beach_fill:
        rows, cols = np.flatnonzero(win.any(axis=1)), np.flatnonzero(win.any(axis=0))
        sl = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
        ins, z0 = inside[sl], before[sl]
        land_out, sea_out = ~ins & (z0 >= 0), ~ins & (z0 < 0)
        if not land_out.any() or not sea_out.any():
            continue
        dl = ndimage.distance_transform_edt(~land_out)
        ds = ndimage.distance_transform_edt(~sea_out)
        ramp = bottom + (top - bottom) * ds / np.maximum(ds + dl, 1e-6)
        hf.z[sl] = np.where(ins, ramp, hf.z[sl])
    return before - hf.z


def cap_front_roads(hf, lines, fcfg, footprints=None, protect=None):
    """Beachfront ground cap. Behind dense beachfront towers (south Copacabana) the DSM keeps 10-16 m of rooftop
    signal 30-60 m inland, where the real plain is 2-4 m; the beach-based flat-shore cap does not reach that far.
    Within ground_band_m of the named avenues (flat-ended buffers, so headlands past the road ends - Forte de
    Copacabana, Morro do Leme - are untouched) the ground may rise at most max_m + ground_slope x distance,
    fading out over ground_blend_m. Only lowers. Returns (z_before - z_after).

    Real hills inside the band (Morro do Leme, the foot of Dois Irmaos at the Mirante do Leblon, the slopes at
    Tunel Novo) must keep their height. The residue comes from buildings, so the cut is scaled by the formal
    building footprint coverage within ground_coverage_window_m (full at ground_coverage_full); `protect`
    polygons (natural cover, favelas) are never cut."""
    import shapely
    if not lines:
        return np.zeros(hf.shape)
    band, blend = fcfg["ground_band_m"], fcfg["ground_blend_m"]
    roads = shapely.union_all(lines)
    zone = shapely.union_all([shapely.buffer(g, band + blend, cap_style="flat") for g in lines])
    xs, ys = hf.coords()
    X, Y = np.meshgrid(xs, ys)
    w, s, e, n = zone.bounds
    win = (X >= w) & (X <= e) & (Y >= s) & (Y <= n)
    inside = np.zeros(hf.shape, bool)
    inside[win] = shapely.contains_xy(zone, X[win], Y[win])
    if not inside.any():
        return np.zeros(hf.shape)
    d = shapely.distance(shapely.points(X[inside], Y[inside]), roads)
    cap = fcfg["max_m"] + fcfg["ground_slope"] * d
    t = np.clip((d - band) / blend, 0, 1)
    wt = 1 - t * t * (3 - 2 * t)
    if footprints is not None and len(footprints):
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        # hf rows run south -> north (row 0 = south edge); rasterize top-down, then flip
        tf = from_origin(hf.west - hf.step / 2, hf.south + (hf.shape[0] - 0.5) * hf.step, hf.step, hf.step)
        cov = rasterize(((g, 1) for g in footprints), out_shape=hf.shape, transform=tf, fill=0, dtype="uint8")[::-1]
        k = max(3, int(round(fcfg["ground_coverage_window_m"] / hf.step)) | 1)
        cov = ndimage.uniform_filter(cov.astype(float), size=k)
        wt = wt * np.clip(cov[inside] / fcfg["ground_coverage_full"], 0, 1)
    if protect is not None and len(protect):
        keep = shapely.contains_xy(shapely.union_all(list(protect)), X[inside], Y[inside])
        wt = np.where(keep, 0.0, wt)
    before = hf.z.copy()
    z = hf.z[inside]
    hf.z[inside] = z * (1 - wt) + np.minimum(z, cap) * wt
    return before - hf.z


def raise_landmark_peaks(z, transform, peaks):
    """Landmark-summit correction (planned in Docs/Map/DataSources.md). The 30 m Copernicus DEM cuts steep granite
    summits short (Irmao Maior 382 m vs 539 m surveyed, Corcovado 527 m vs 710 m). For each configured peak
    (name, x, y, surveyed ele, radius_m) the ground at the OSM summit point is raised to `ele` with a smooth dome
    falloff w = (1 - (d/R)^2)^2 (zero beyond R), capped at `ele` so a nearby DEM high point cannot overshoot.
    Peaks are applied one after another, so overlapping domes (the two Dois Irmaos, 210 m apart) stay correct.
    z is a raster with an affine `transform` (the 10 m ground window). Modified in place; returns a report."""
    ny, nx = z.shape
    cols, rows = np.meshgrid(np.arange(nx) + 0.5, np.arange(ny) + 0.5)
    X, Y = transform * (cols, rows)
    inv = ~transform
    report = []
    for name, x, y, ele, radius in peaks:
        c, r = inv * (x, y)
        if not (0 <= c < nx - 1 and 0 <= r < ny - 1):
            report.append({"name": name, "status": "outside DEM window"})
            continue
        current = float(ndimage.map_coordinates(z, [[r - 0.5], [c - 0.5]], order=1)[0])
        delta = ele - current
        if delta <= 0:
            report.append({"name": name, "ele": ele, "dem": round(current, 1), "raised_m": 0.0})
            continue
        w = np.clip(1 - (np.hypot(X - x, Y - y) / radius) ** 2, 0, None) ** 2
        z[:] = np.where(w > 0, np.minimum(z + delta * w, np.maximum(z, ele)), z)
        report.append({"name": name, "ele": ele, "dem": round(current, 1), "raised_m": round(delta, 1),
                       "radius_m": radius})
    return report
