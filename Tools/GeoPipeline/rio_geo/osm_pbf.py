"""Offline OSM source: clip a local .osm.pbf extract to a bbox, for use instead of Overpass.

The public Overpass servers were all down on 2026-11-24, so feature fetches can read a local extract instead:
  GeoData/raw/osm/extract/RiodeJaneiro.osm.pbf   (BBBike, ~38 MB, extent lon -43.58..-42.92, lat -23.08..-22.58)
  https://download.bbbike.org/osm/bbbike/RiodeJaneiro/RiodeJaneiro.osm.pbf

`clip_to_xml` writes a small, reference-complete OSM XML file holding every object that carries one of the
wanted tag keys and whose envelope overlaps the bbox (nodes, ways, multipolygon relations plus all nodes / member
ways they reference). That file goes into `osmnx.features_from_xml(..., polygon=bbox, tags=...)`, which applies
the exact tag values and the intersects-bbox filter, so the result has the same shape as `features_from_bbox`
(index (element, id), one row per OSM object).

Known gap vs Overpass: a multipolygon relation whose member ways each lie outside the bbox while the whole polygon
covers it (bbox fully inside a huge area) is not picked up. Irrelevant for the anchor strips used here.
"""
from pathlib import Path

import osmium

from .common import RAW_DIR

EXTRACT_DIR = RAW_DIR / "osm" / "extract"
DEFAULT_PBF = EXTRACT_DIR / "RiodeJaneiro.osm.pbf"


def pbf_extent(pbf):
    """(west, south, east, north) from the pbf header, or None if the header has no box."""
    box = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header().box()
    if not box.valid():
        return None
    return (box.bottom_left.lon, box.bottom_left.lat, box.top_right.lon, box.top_right.lat)


def covers(pbf, bbox):
    ext = pbf_extent(pbf)
    if ext is None:
        return True  # unknown extent: assume yes, the clip just comes back empty otherwise
    w, s, e, n = bbox
    return ext[0] <= w and ext[1] <= s and ext[2] >= e and ext[3] >= n


def _overlaps(env, bbox):
    w, s, e, n = bbox
    return not (env[2] < w or env[0] > e or env[3] < s or env[1] > n)


def _way_env(way):
    lons, lats = [], []
    for nd in way.nodes:
        loc = nd.location
        if loc.valid():
            lons.append(loc.lon)
            lats.append(loc.lat)
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


def clip_to_xml(pbf, bbox, keys, out_xml):
    """Write the objects tagged with any of `keys` that overlap `bbox` (lon/lat) to `out_xml`, reference-complete.
    Returns (nodes, ways, relations) counts of the selected (tagged) objects."""
    keys = set(keys)
    out_xml = Path(out_xml)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    w, s, e, n = bbox
    near_ways = set()  # ids of all ways (tagged or not) overlapping the bbox, for multipolygon members
    counts = [0, 0, 0]
    with osmium.BackReferenceWriter(str(out_xml), ref_src=str(pbf), overwrite=True) as writer:
        # Pbf order is nodes, ways, relations, so way envelopes are known before the relations are seen.
        for obj in osmium.FileProcessor(str(pbf)).with_locations():
            if obj.is_node():
                if any(k in obj.tags for k in keys) and obj.location.valid() \
                        and w <= obj.location.lon <= e and s <= obj.location.lat <= n:
                    writer.add_node(obj)
                    counts[0] += 1
            elif obj.is_way():
                env = _way_env(obj)
                if env is None or not _overlaps(env, bbox):
                    continue
                near_ways.add(obj.id)
                if any(k in obj.tags for k in keys):
                    writer.add_way(obj)
                    counts[1] += 1
            elif obj.is_relation():
                if obj.tags.get("type") not in ("multipolygon", "boundary"):
                    continue
                if not any(k in obj.tags for k in keys):
                    continue
                if any(m.type == "w" and m.ref in near_ways for m in obj.members):
                    writer.add_relation(obj)
                    counts[2] += 1
    return tuple(counts)
