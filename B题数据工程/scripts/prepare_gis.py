"""Subset official Hong Kong EPSG:2326 CityGML; export WGS84 GeoJSON/CSV."""
from __future__ import annotations

import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".deps"))
from pyproj import Transformer
import pandas as pd

from prepare_observations import BBOX

GML = "{http://www.opengis.net/gml}"
GEN = "{http://www.opengis.net/citygml/generics/2.0}"
TO_GRID = Transformer.from_crs(4326, 2326, always_xy=True)
TO_GEO = Transformer.from_crs(2326, 4326, always_xy=True)
x0, y0 = TO_GRID.transform(BBOX["west"], BBOX["south"])
x1, y1 = TO_GRID.transform(BBOX["east"], BBOX["north"])


def objects(path):
    context = ET.iterparse(path, events=("start", "end"))
    _, root = next(context)
    for event, node in context:
        if event == "end" and node.tag.endswith("cityObjectMember"):
            obj = node.find(GEN + "GenericCityObject")
            attrs = {a.get("name"): a.findtext(GEN + "value") for a in obj if a.get("name")}
            pos = obj.find(".//" + GML + "posList")
            if pos is None:
                pos = obj.find(".//" + GML + "pos")
            pairs = []
            if pos is not None and pos.text:
                dim = 2
                nums = list(map(float, pos.text.split()))
                pairs = list(zip(nums[::dim], nums[1::dim]))
            yield attrs, pairs
            node.clear()
            root.clear()


def intersects(pairs):
    if not pairs:
        return False
    xs, ys = zip(*pairs)
    return not (max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1)


def main():
    out = ROOT / "processed"
    features, rows, routes = [], [], set()
    for attrs, pairs in objects(ROOT / "raw/official_gis/centerline.gml"):
        if not intersects(pairs):
            continue
        routes.add(attrs["ROUTE_ID"])
        rows.append(attrs)
        coords = [list(TO_GEO.transform(x, y)) for x, y in pairs]
        features.append({"type": "Feature", "properties": attrs,
                         "geometry": {"type": "LineString", "coordinates": coords}})
    with gzip.open(out / "official_centerline.geojson.gz", "wt", encoding="utf-8") as h:
        json.dump({"type": "FeatureCollection", "features": features}, h, ensure_ascii=False)
    pd.DataFrame(rows).to_csv(out / "official_centerline_attributes.csv", index=False, encoding="utf-8-sig")
    stats = {"centerline_segments": len(rows), "bbox_wgs84": BBOX,
             "raw_crs": "EPSG:2326", "export_crs": "EPSG:4326 (lon,lat)",
             "official_revision_cutoff": "2026-10-08",
             "signal_plan_available": False}
    for name in ["intersection", "speed_limit", "traffic_features", "turn"]:
        rows, feats = [], []
        for attrs, pairs in objects(ROOT / f"raw/official_gis/{name}.gml"):
            relevant = intersects(pairs)
            if name == "turn":
                relevant = any(attrs.get(f"EDGE{i}FID") in routes for i in range(1, 9))
            if not relevant:
                continue
            if len(pairs) == 1:
                lon, lat = TO_GEO.transform(*pairs[0])
                attrs.update(longitude=lon, latitude=lat)
            rows.append(attrs)
            if pairs:
                coords = [list(TO_GEO.transform(x, y)) for x, y in pairs]
                geometry = {"type": "Point", "coordinates": coords[0]} if len(coords) == 1 else {
                    "type": "LineString", "coordinates": coords}
                feats.append({"type": "Feature", "properties": attrs, "geometry": geometry})
        pd.DataFrame(rows).to_csv(out / f"official_{name}.csv", index=False, encoding="utf-8-sig")
        with gzip.open(out / f"official_{name}.geojson.gz", "wt", encoding="utf-8") as h:
            json.dump({"type": "FeatureCollection", "features": feats}, h, ensure_ascii=False)
        stats[name + "_features"] = len(rows)
        if name == "intersection":
            stats["official_signalized_junctions"] = sum(r.get("INT_TYPE") == "1" for r in rows)
        print(name, len(rows), flush=True)
    speed = ET.parse(ROOT / "raw/hk_transport/irnAvgSpeed_latest.xml").getroot()
    snapshot = []
    for segment in speed.findall("./segments/segment"):
        if segment.findtext("segment_id") in routes:
            snapshot.append({c.tag: c.text for c in segment})
    pd.DataFrame(snapshot).to_csv(out / "official_segment_speed_snapshot.csv", index=False, encoding="utf-8-sig")
    stats["segment_speed_snapshot_matches"] = len(snapshot)
    stats["segment_speed_observed_date"] = speed.findtext("date")
    stats["segment_speed_observed_time"] = speed.findtext("time")
    stats["segment_speed_irn_version"] = speed.findtext("irn_version")
    (out / "gis_quality.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
