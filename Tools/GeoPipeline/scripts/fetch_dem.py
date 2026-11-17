"""Step 2 - download Copernicus GLO-30 DEM tiles and reproject them to the project CRS.

Outputs:
  GeoData/raw/dem/<tile>.tif                       untouched 1x1 degree source tiles (EPSG:4326)
  GeoData/processed/dem/<aoi>_<res>m.tif           mosaic clipped to the AOI, EPSG:31983, cubic resampled
  GeoData/processed/dem/<aoi>_metadata.json

Copernicus has no tiles over open ocean; missing tiles are treated as sea level.

Usage:  .venv/bin/python scripts/fetch_dem.py --aoi full
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.merge import merge
from rasterio.warp import Resampling, calculate_default_transform, reproject  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rio_geo.common import (CONFIG, PROCESSED_DIR, PROJECTED_CRS, RAW_DIR, aoi_bbox_lonlat,  # noqa: E402
                            aoi_bbox_utm, write_metadata)

MARGIN_M = 500.0


def tile_names(bbox):
    w, s, e, n = bbox
    names = []
    for lat in range(math.floor(s), math.floor(n) + 1):
        for lon in range(math.floor(w), math.floor(e) + 1):
            names.append(CONFIG["dem"]["name_template"].format(
                ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon)))
    return names


def download(name):
    path = RAW_DIR / "dem" / f"{name}.tif"
    if path.exists():
        return path
    url = CONFIG["dem"]["url_template"].format(name=name)
    r = requests.get(url, stream=True, timeout=300)
    if r.status_code in (403, 404):
        print(f"[dem] {name}: not published (open ocean) - treated as sea level")
        return None
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.rename(path)
    print(f"[dem] downloaded {name} ({path.stat().st_size / 1e6:.1f} MB)")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aoi", required=True, choices=list(CONFIG["aois"]))
    ap.add_argument("--res", type=float, default=CONFIG["dem"]["processed_resolution_m"])
    args = ap.parse_args()

    paths = [p for p in (download(n) for n in tile_names(aoi_bbox_lonlat(args.aoi))) if p]
    if not paths:
        sys.exit("[dem] no DEM tiles available for AOI")
    sources = [rasterio.open(p) for p in paths]
    mosaic, mosaic_tf = merge(sources)
    src_crs, src_nodata = sources[0].crs, sources[0].nodata

    w, s, e, n = aoi_bbox_utm(args.aoi)
    w, s, e, n = w - MARGIN_M, s - MARGIN_M, e + MARGIN_M, n + MARGIN_M
    width, height = int(math.ceil((e - w) / args.res)), int(math.ceil((n - s) / args.res))
    dst_tf = rasterio.transform.from_origin(w, n, args.res, args.res)
    dst = np.full((height, width), np.nan, dtype=np.float32)
    src = mosaic[0].astype(np.float32)
    if src_nodata is not None:
        src[src == src_nodata] = np.nan
    reproject(src, dst, src_transform=mosaic_tf, src_crs=src_crs, src_nodata=np.nan,
              dst_transform=dst_tf, dst_crs=PROJECTED_CRS, dst_nodata=np.nan,
              resampling=Resampling.cubic)
    nodata_frac = float(np.isnan(dst).mean())
    dst = np.nan_to_num(dst, nan=0.0)

    out = PROCESSED_DIR / "dem" / f"{args.aoi}_{args.res:g}m.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", width=width, height=height, count=1, dtype="float32",
                       crs=PROJECTED_CRS, transform=dst_tf, compress="deflate", predictor=3, tiled=True) as f:
        f.write(dst, 1)
    print(f"[dem] wrote {out.name}: {width}x{height} @ {args.res} m, "
          f"range {dst.min():.1f}..{dst.max():.1f} m, filled nodata {nodata_frac:.1%}")
    write_metadata(out.parent / f"{args.aoi}_metadata.json", aoi=args.aoi, tiles=[p.name for p in paths],
                   crs=PROJECTED_CRS, resolution_m=args.res, bounds_utm=[w, s, e, n],
                   height_range_m=[float(dst.min()), float(dst.max())], filled_nodata_fraction=nodata_frac,
                   license=CONFIG["dem"]["license"], attribution=CONFIG["dem"]["attribution"],
                   caveat="Copernicus GLO-30 is a surface model (DSM): it includes buildings and tree canopy.")


if __name__ == "__main__":
    main()
