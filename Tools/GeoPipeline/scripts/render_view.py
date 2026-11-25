"""Perspective 3D view of a built AOI from the exported OBJ tiles: a small numpy z-buffer rasteriser, so
generated content can be eyeballed on a machine without Unreal (the 2017 MacBook).

Flat shading per triangle, double-sided, colours from the blockout / places / beach / shore palettes.

Usage:  .venv/bin/python scripts/render_view.py --aoi test_vidigal --target 680830 7456048 40 \
            --eye 680790 7455990 75 --out /tmp/hub.png  [--radius 90] [--fov 55] [--size 1400 900]
        --eye-rel AZ_DEG EL_DEG DIST instead of --eye: camera direction from the target (azimuth from north,
        clockwise; elevation above the horizon).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import EXPORTS_DIR  # noqa: E402

COLOURS = {
    "M_Terrain": (0.72, 0.70, 0.60), "M_RoadMajor": (0.22, 0.22, 0.23), "M_RoadMinor": (0.30, 0.30, 0.31),
    "M_Path": (0.80, 0.70, 0.50), "M_Steps": (0.86, 0.58, 0.25), "M_BuildingInformal": (0.78, 0.42, 0.26),
    "M_BuildingFormal": (0.86, 0.85, 0.80), "M_WaterTank": (0.12, 0.35, 0.80), "M_Sea": (0.15, 0.35, 0.55),
    "M_Bench": (0.42, 0.29, 0.16), "M_TreeTrunk": (0.40, 0.30, 0.20), "M_TreeCrown": (0.20, 0.48, 0.20),
}
KINDS = ("Terrain", "Roads", "Buildings", "Places", "Details", "Wires", "Beach", "Shore", "Water")


def palette():
    cols = dict(COLOURS)
    try:
        from build_area import PALETTE as B  # noqa: PLC0415
        cols.update({k: v for k, v in B.items() if k.startswith("M_House")})
    except Exception:  # noqa: BLE001 - optional
        pass
    for mod in ("build_places", "build_details", "build_beach", "build_shore"):
        try:
            cols.update(__import__(mod).PALETTE)
        except Exception:  # noqa: BLE001 - optional
            pass
    return cols


def load_triangles(manifest, centre, radius, kinds):
    """World-space triangles (N, 3, 3) and their material names within radius (xy) of centre."""
    cx, cy = centre
    tris, mats = [], []
    for a in manifest["actors"]:
        if a["kind"] not in kinds or not Path(a["obj"]).exists():
            continue
        if "pivot_utm" in a:
            px, py = a["pivot_utm"]
        else:  # whole-AOI meshes (sea) are pivoted at the AOI's south-west corner; skip them
            continue
        V, faces, mat = [], [], None
        with open(a["obj"]) as fh:
            for ln in fh:
                if ln.startswith("v "):
                    V.append(ln.split()[1:4])
                elif ln.startswith("usemtl"):
                    mat = ln.split()[1]
                elif ln.startswith("f "):
                    faces.append((mat, [int(t.split("/")[0]) - 1 for t in ln.split()[1:4]]))
        if not faces:
            continue
        V = np.asarray(V, dtype=float) / 100.0 + [px, py, 0.0]
        idx = np.array([f[1] for f in faces])
        T = V[idx]
        c = T.mean(axis=1)
        keep = np.hypot(c[:, 0] - cx, c[:, 1] - cy) <= radius
        tris.append(T[keep])
        mats += [f[0] for f, k in zip(faces, keep) if k]
    return (np.concatenate(tris) if tris else np.zeros((0, 3, 3))), mats


def render(tris, mats, eye, target, size=(1400, 900), fov_deg=55.0, near=0.3):
    W, H = size
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    P = tris - eye
    cam = np.stack([P @ r, P @ u, P @ f], axis=-1)  # x right, y up, z forward
    vis = (cam[..., 2] > near).all(axis=1)
    cam, tris = cam[vis], tris[vis]
    mats = [m for m, v in zip(mats, vis) if v]
    fpx = (W / 2) / np.tan(np.radians(fov_deg) / 2)
    sx = W / 2 + fpx * cam[..., 0] / cam[..., 2]
    sy = H / 2 - fpx * cam[..., 1] / cam[..., 2]
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    light = np.array([-0.45, -0.35, 0.82])
    light /= np.linalg.norm(light)
    shade = 0.42 + 0.58 * np.abs(n @ light)
    cols = palette()
    base = np.array([cols.get(m, (0.6, 0.6, 0.6)) for m in mats]).reshape(-1, 3)
    rgb = np.clip(base * shade[:, None], 0, 1)
    img = np.empty((H, W, 3))
    img[:] = np.linspace([0.62, 0.76, 0.92], [0.88, 0.92, 0.96], H)[:, None, :]
    zbuf = np.full((H, W), np.inf)
    invz = 1.0 / cam[..., 2]
    for k in range(len(sx)):
        x0, x1 = int(max(0, np.floor(sx[k].min()))), int(min(W - 1, np.ceil(sx[k].max())))
        y0, y1 = int(max(0, np.floor(sy[k].min()))), int(min(H - 1, np.ceil(sy[k].max())))
        if x0 > x1 or y0 > y1:
            continue
        (ax, bx, cx_), (ay, by, cy_) = sx[k], sy[k]
        den = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
        if abs(den) < 1e-9:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((by - cy_) * (X - cx_) + (cx_ - bx) * (Y - cy_)) / den
        w1 = ((cy_ - ay) * (X - cx_) + (ax - cx_) * (Y - cy_)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = 1.0 / (w0 * invz[k, 0] + w1 * invz[k, 1] + w2 * invz[k, 2])
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        m = inside & (z < sub)
        sub[m] = z[m]
        img[y0:y1 + 1, x0:x1 + 1][m] = rgb[k]
    # the sea (sea level z = 0, endless): empty pixels whose view ray goes down to it
    Y, X = np.mgrid[0:H, 0:W] + 0.5
    d = f[None, None, :] + ((X - W / 2) / fpx)[..., None] * r + (-(Y - H / 2) / fpx)[..., None] * u
    sea = np.isinf(zbuf) & (d[..., 2] < 0) & (eye[2] > 0)
    img[sea] = cols.get("M_Sea", (0.15, 0.35, 0.55))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True)
    ap.add_argument("--target", type=float, nargs=3, required=True, metavar=("E", "N", "Z"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--eye", type=float, nargs=3, metavar=("E", "N", "Z"))
    g.add_argument("--eye-rel", type=float, nargs=3, metavar=("AZ", "EL", "DIST"))
    ap.add_argument("--radius", type=float, default=90.0, help="only triangles within this distance (xy) of target")
    ap.add_argument("--fov", type=float, default=55.0)
    ap.add_argument("--size", type=int, nargs=2, default=(1400, 900))
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    t0 = time.time()
    manifest = json.loads((EXPORTS_DIR / args.aoi / "manifest.json").read_text())
    if args.eye_rel:
        az, el, d = np.radians(args.eye_rel[0]), np.radians(args.eye_rel[1]), args.eye_rel[2]
        eye = np.asarray(args.target) + d * np.array([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)])
    else:
        eye = np.asarray(args.eye)
    tris, mats = load_triangles(manifest, args.target[:2], args.radius, KINDS)
    img = render(tris, mats, eye, args.target, tuple(args.size), args.fov)
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    fig = plt.figure(figsize=(args.size[0] / 100, args.size[1] / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(img)
    ax.set_axis_off()
    ax.text(8, 18, args.title or f"{args.aoi}: eye {np.round(eye, 1).tolist()} -> target {args.target}",
            fontsize=9, color="black", bbox=dict(fc="white", alpha=0.7, lw=0))
    fig.savefig(args.out, dpi=100)
    print(f"{len(tris)} triangles, {time.time() - t0:.1f} s -> {args.out}")


if __name__ == "__main__":
    main()
