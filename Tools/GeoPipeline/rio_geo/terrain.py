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
                    lowland_max_m=15.0):
    """Copernicus GLO-30 is a surface model. Remove the built-up surface in two passes:

    1. Subtract the mapped building volume: mean building height per DEM cell (rasterised at 1/5 of the
       cell size), blurred to the radar's effective resolution. Slope-independent, so hillside
       neighbourhoods keep their shape. `gain` > 1 compensates for the blur spreading rooftops into streets.
    2. On the coastal lowland only (Leblon/Ipanema plain), take the minimum with a 150 m morphological
       opening to remove the residual roof signal. Never applied on slopes, where it would cut terraces.

    Sea (DSM <= 0.05, connected to the raster edge) becomes a gently sloping seabed.
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
