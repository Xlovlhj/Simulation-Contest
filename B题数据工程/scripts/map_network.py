"""Map observations and official GIS to SUMO; retain quality flags/candidates."""
from __future__ import annotations

import gzip
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path[:0] = [str(ROOT / ".deps"), str(SUMO_HOME / "tools")]
import sumolib
from pyproj import Transformer


def nearest_segment(point, shape):
    best = (float("inf"), None)
    for a, b in zip(shape, shape[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx * dx + dy * dy == 0:
            continue
        t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        distance = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
        if distance < best[0]:
            best = distance, math.degrees(math.atan2(dx, dy)) % 360
    return best


def name_match(name, road):
    base = road.split(" near ")[0].lower().replace(" flyover", "")
    return base in name.lower()


def main():
    out = ROOT / "processed"
    net = sumolib.net.readNet(str(out / "kowloon.net.xml"))
    stations = pd.read_csv(out / "detector_locations.csv")
    coverage = pd.read_csv(out / "coverage_by_detector_day.csv")
    lanes = coverage.groupby("detector_id").expected_lanes.max().to_dict()
    with gzip.open(out / "official_centerline.geojson.gz", "rt", encoding="utf-8") as h:
        official = json.load(h)["features"]
    grid = Transformer.from_crs(4326, 2326, always_xy=True)
    official_shapes = []
    for f in official:
        xy = [grid.transform(*p) for p in f["geometry"]["coordinates"]]
        official_shapes.append((f["properties"], xy))
    edges = [e for e in net.getEdges() if e.allows("passenger")]
    mappings, candidates, official_map = [], [], []
    for s in stations.itertuples():
        xy = net.convertLonLat2XY(s.Longitude, s.Latitude)
        ranked = []
        for edge in edges:
            distance, bearing = nearest_segment(xy, edge.getShape())
            if distance > 100 or bearing is None:
                continue
            delta = abs((bearing - s.Rotation + 180) % 360 - 180)
            matched = name_match(edge.getName(), s.Road_EN)
            score = distance + delta * 0.45 + (0 if matched else 45)
            ranked.append({"detector_id": s.AID_ID_Number, "sumo_edge": edge.getID(),
                           "sumo_name": edge.getName(), "distance_m": round(distance, 2),
                           "bearing_difference_deg": round(delta, 1), "road_name_match": matched,
                           "score": round(score, 2), "sumo_lanes": edge.getLaneNumber(),
                           "observed_lane_labels": lanes.get(s.AID_ID_Number),
                           "sumo_speed_limit_kmh": round(edge.getSpeed() * 3.6, 1)})
        ranked.sort(key=lambda x: x["score"])
        candidates.extend(dict(r, rank=i + 1) for i, r in enumerate(ranked[:5]))
        if ranked:
            best = ranked[0].copy()
            best["score_gap"] = round(ranked[1]["score"] - best["score"], 2) if len(ranked) > 1 else None
            best["requires_manual_review"] = (best["distance_m"] > 25 or
                best["bearing_difference_deg"] > 55 or not best["road_name_match"] or
                (best["score_gap"] is not None and best["score_gap"] < 8) or
                best["sumo_lanes"] != best["observed_lane_labels"])
            best["mapping_status"] = "provisional_geometry_direction_name"
            mappings.append(best)
        p = grid.transform(s.Longitude, s.Latitude)
        ranks = []
        for attrs, shape in official_shapes:
            distance, bearing = nearest_segment(p, shape)
            if distance > 80 or bearing is None:
                continue
            delta = abs((bearing - s.Rotation + 180) % 360 - 180)
            if attrs.get("TRAVEL_DIRECTION") == "1":
                delta = min(delta, abs(180 - delta))
            matched = name_match(attrs.get("STREET_ENAME", ""), s.Road_EN)
            ranks.append((distance + delta * 0.45 + (0 if matched else 45), attrs, distance, delta))
        if ranks:
            ranks.sort(key=lambda x: x[0])
            _, a, d, angle = ranks[0]
            official_map.append({"detector_id": s.AID_ID_Number, "ROUTE_ID": a["ROUTE_ID"],
                "official_name": a["STREET_ENAME"], "ELEVATION": a["ELEVATION"],
                "TRAVEL_DIRECTION": a["TRAVEL_DIRECTION"], "distance_m": round(d, 2),
                "bearing_difference_deg": round(angle, 1), "status": "provisional"})
    pd.DataFrame(mappings).to_csv(out / "detector_sumo_mapping.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(candidates).to_csv(out / "detector_sumo_candidates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(official_map).to_csv(out / "detector_official_mapping.csv", index=False, encoding="utf-8-sig")
    node_rows = []
    for n in net.getNodes():
        lon, lat = net.convertXY2LonLat(*n.getCoord()[:2])
        node_rows.append({"sumo_node": n.getID(), "type": n.getType(), "longitude": lon, "latitude": lat})
    pd.DataFrame(node_rows).to_csv(out / "sumo_nodes.csv", index=False, encoding="utf-8-sig")
    edge_rows = [{"sumo_edge": e.getID(), "name": e.getName(), "from_node": e.getFromNode().getID(),
        "to_node": e.getToNode().getID(), "lanes": e.getLaneNumber(), "speed_limit_kmh": e.getSpeed() * 3.6,
        "length_m": e.getLength(), "source": "OSM_plus_netconvert_defaults_requires_audit"} for e in edges]
    pd.DataFrame(edge_rows).to_csv(out / "sumo_edges.csv", index=False, encoding="utf-8-sig")
    tls = ET.parse(out / "kowloon.net.xml").getroot().findall("tlLogic")
    signal_rows = [{"tls_id": t.get("id"), "program_id": t.get("programID"),
        "phase_index": i, "duration_seconds": p.get("duration"), "state": p.get("state"),
        "provenance": "generated_by_netconvert_NOT_observed_timing"}
        for t in tls for i, p in enumerate(t.findall("phase"))]
    pd.DataFrame(signal_rows).to_csv(out / "signal_plans_assumed.csv", index=False, encoding="utf-8-sig")
    accesses = []
    for h in pd.read_csv(out / "hospitals_region.csv").itertuples():
        xy = net.convertLonLat2XY(h.longitude, h.latitude)
        best = sorted((nearest_segment(xy, e.getShape())[0], e.getID(), e.getName()) for e in edges)[:3]
        for rank, (dist, eid, name) in enumerate(best, 1):
            accesses.append({"hospital": h.institution_eng, "with_AE_service": h.with_AE_service_eng,
                "longitude": h.longitude, "latitude": h.latitude, "candidate_rank": rank,
                "sumo_edge": eid, "sumo_name": name, "distance_m": round(dist, 2),
                "status": "nearest_public_road_candidate_NOT_verified_ambulance_gate"})
    pd.DataFrame(accesses).to_csv(out / "hospital_access_candidates.csv", index=False, encoding="utf-8-sig")
    report = {"sumo_edges": len(net.getEdges()), "passenger_edges": len(edges), "sumo_nodes": len(net.getNodes()),
        "sumo_tls": len(tls), "mapped_detectors": len(mappings),
        "mappings_flagged_for_review": sum(r["requires_manual_review"] for r in mappings),
        "official_detector_matches": len(official_map), "left_hand_traffic": True,
        "warnings": ["All automatic maps are provisional, especially overlapping flyovers.",
            "OSM lane counts/speed defaults require comparison with official GIS.",
            "SUMO signal phase plans are assumptions, not actual timing.",
            "A successful network import is not traffic-model validation."]}
    (out / "network_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
