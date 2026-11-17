"""Config, paths and coordinate transforms shared by every pipeline step.

Frames used throughout the pipeline:
  * lon/lat      EPSG:4326 (OSM) / EPSG:4674 (SIRGAS 2000) - interchangeable at < 1 m
  * UTM          EPSG:31983, metres  - every analysis step works here
  * UE source    centimetres, +X east, +Y south, +Z up, relative to config unreal.origin_*
"""
import datetime
import hashlib
import json
from pathlib import Path

from pyproj import Transformer

PIPELINE_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = PIPELINE_DIR.parents[1]
GEODATA_DIR = PROJECT_DIR / "GeoData"
RAW_DIR = GEODATA_DIR / "raw"
PROCESSED_DIR = GEODATA_DIR / "processed"
EXPORTS_DIR = GEODATA_DIR / "exports"
DOCS_DIR = PROJECT_DIR / "Docs" / "Map"


def load_config():
    with open(PIPELINE_DIR / "config" / "project.json") as f:
        return json.load(f)


CONFIG = load_config()
PROJECTED_CRS = CONFIG["crs"]["projected"]

_to_utm = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
_to_lonlat = Transformer.from_crs(PROJECTED_CRS, "EPSG:4326", always_xy=True)


def lonlat_to_utm(lon, lat):
    return _to_utm.transform(lon, lat)


def utm_to_lonlat(e, n):
    return _to_lonlat.transform(e, n)


def utm_to_ue(e, n, z=0.0):
    """UTM metres -> Unreal source-frame centimetres."""
    u = CONFIG["unreal"]
    return ((e - u["origin_easting"]) * 100.0,
            -(n - u["origin_northing"]) * 100.0,
            (z - u["origin_height"]) * 100.0)


def aoi_bbox_lonlat(aoi_name):
    aoi = CONFIG["aois"][aoi_name]
    if "bbox_lonlat" in aoi:
        return tuple(aoi["bbox_lonlat"])
    w, s, e, n = aoi["bbox_utm"]
    lons, lats = zip(*[utm_to_lonlat(x, y) for x, y in ((w, s), (w, n), (e, s), (e, n))])
    return (min(lons), min(lats), max(lons), max(lats))


def aoi_bbox_utm(aoi_name):
    aoi = CONFIG["aois"][aoi_name]
    if "bbox_utm" in aoi:
        return tuple(aoi["bbox_utm"])
    w, s, e, n = aoi["bbox_lonlat"]
    xs, ys = zip(*[lonlat_to_utm(x, y) for x, y in ((w, s), (w, n), (e, s), (e, n))])
    return (min(xs), min(ys), max(xs), max(ys))


def stable_unit(key):
    """Deterministic float in [0, 1) from any key, so regenerations are reproducible."""
    h = hashlib.sha1(str(key).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def write_metadata(path, **fields):
    fields.setdefault("generated_utc", datetime.datetime.now(datetime.timezone.utc).isoformat())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, indent=2, default=str))
