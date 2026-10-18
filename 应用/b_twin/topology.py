"""Audit directional road evidence without silently accepting sensor mappings."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际使用记录补齐并人工复核。
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

import pandas as pd

from .paths import APP, DATA, REFINED
import sumolib


def nearest_bearing(point, shape):
    best = (float("inf"), None)
    for a, b in zip(shape, shape[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        squared = dx * dx + dy * dy
        if not squared:
            continue
        t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / squared))
        distance = math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
        if distance < best[0]:
            best = distance, math.degrees(math.atan2(dx, dy)) % 360
    return best


def angle_difference(a, b):
    return abs((a - b + 180) % 360 - 180)


def audit_topology():
    output = APP / "results" / "stage03_topology_audit"
    output.mkdir(parents=True, exist_ok=True)
    net = sumolib.net.readNet(str(REFINED / "urban.net.xml"))
    sites = pd.read_csv(DATA / "processed/detector_locations.csv").set_index("AID_ID_Number")
    mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    official_map = pd.read_csv(DATA / "processed/detector_official_mapping.csv", dtype={"ROUTE_ID": str}).set_index("detector_id")
    with gzip.open(DATA / "processed/official_centerline.geojson.gz", "rt", encoding="utf-8") as h:
        official = {f["properties"]["ROUTE_ID"]: f for f in json.load(h)["features"]}
    records = []
    for row in mapping.itertuples():
        s = sites.loc[row.detector_id]
        o = official_map.loc[row.detector_id]
        f = official[o.ROUTE_ID]
        shape = [net.convertLonLat2XY(*p) for p in f["geometry"]["coordinates"]]
        point = net.convertLonLat2XY(s.Longitude, s.Latitude)
        edge = net.getEdge(row.sumo_edge)
        official_dist, official_heading = nearest_bearing(point, shape)
        osm_dist, osm_heading = nearest_bearing(point, edge.getShape())
        directed = f["properties"]["TRAVEL_DIRECTION"] == "3"
        agreement = angle_difference(official_heading, osm_heading)
        records.append({"detector_id": row.detector_id, "sumo_edge": row.sumo_edge, "official_route": o.ROUTE_ID,
            "official_oneway_digitized": directed, "official_distance_m": official_dist, "osm_distance_m": osm_dist,
            "sensor_rotation_degrees": float(s.Rotation), "official_local_heading": official_heading,
            "sumo_local_heading": osm_heading, "official_sumo_heading_difference": agreement,
            "original_mapping_requires_review": bool(row.requires_review),
            "independent_direction_support": directed and agreement < 15 and official_dist < 20 and osm_dist < 20,
            "decision": "Independent directional support only; sensor lane coverage/position not field verified. Original flags retained."})
    pd.DataFrame(records).to_csv(output / "direction_crosscheck.csv", index=False, encoding="utf-8-sig")
    tags = {w.get("id"): {t.get("k"): t.get("v") for t in w.findall("tag")}
        for w in ET.parse(DATA / "raw/osm/kowloon_merged.osm.xml").getroot().findall("way")}
    connections, evidence = [], []
    for eid in ["150771648", "1433971787", "150771619", "1095134808", "1095134817", "45740769", "1095134801"]:
        edge = net.getEdge(eid)
        raw = re.match(r"-?(\d+)", eid).group(1)
        t = tags[raw]
        evidence.append({"edge_id": eid, "name": edge.getName(), "lanes": edge.getLaneNumber(),
            "length_m": edge.getLength(), "junction_type": edge.getToNode().getType(),
            "osm_evidence": {k: v for k, v in t.items() if k in {"lanes", "layer", "bridge", "tunnel", "turn:lanes", "change:lanes"}
                or "conditional" in k}, "provenance": "source_OSM_tags_NOT_actual_signal_or_turn_counts"})
        for downstream, cs in edge.getOutgoing().items():
            for c in cs:
                connections.append({"from_edge": eid, "to_edge": downstream.getID(),
                    "from_lane": c.getFromLane().getIndex(), "to_lane": c.getToLane().getIndex(),
                    "direction": c.getDirection(), "priority_state": c.getState(),
                    "evidence": "Generated SUMO connection, needs lane/turn validation; minor state m can create yield delay"})
    pd.DataFrame(connections).to_csv(output / "lane_connections.csv", index=False, encoding="utf-8-sig")
    result = {"source_gis_direction_definition": "TRAVEL_DIRECTION=3 means travel in digitized direction; raw data_specification_gml.txt lines 217-222",
        "mapping_changes_applied": False, "osm_lane_and_turn_evidence": evidence,
        "finding": "01213 road direction can be crosschecked against official one-way geometry independently of its coarse sensor Rotation. This does not establish individual lane correspondence or correct reported speeds.",
        "limitations": ["No arbitrary lane widening, turn deletion or traffic signal timing changes applied.",
            "OSM conditional bus/vehicle permissions are not a verified time-dependent lane inventory.",
            "Side-road yield queues cannot be treated as mainline congestion validation."],
        "source_sha256": {str(p.relative_to(DATA)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [REFINED / "urban.net.xml", DATA / "processed/official_centerline.geojson.gz", REFINED / "detector_mapping.csv"]}}
    (output / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "mapping_flags_preserved": True,
        "AID01213": next(r for r in records if r["detector_id"] == "AID01213")}
