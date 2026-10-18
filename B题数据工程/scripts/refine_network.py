"""Cross-check mapping with OSM lane/layer evidence; isolate an arterial corridor.

Source data are preserved. Urban default speed and generated signals remain
explicit assumptions; map ranking is an automatic inference, not field review.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pandas as pd

from download_sources import ROOT
from map_network import nearest_segment, name_match

OUT = ROOT / "refined"
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path[:0] = [str(ROOT / ".deps"), str(SUMO_HOME / "tools")]
import sumolib


def restricted_path(net, first, last, allowed):
    queue = [(0., first, (first,))]
    visited = set()
    while queue:
        cost, eid, path = heapq.heappop(queue)
        if eid in visited:
            continue
        visited.add(eid)
        if eid == last:
            return path
        for next_edge in net.getEdge(eid).getOutgoing():
            other = next_edge.getID()
            if other in allowed and next_edge.allows("passenger"):
                heapq.heappush(queue, (cost + next_edge.getLength(), other, path + (other,)))
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-urban", action="store_true", help="Reuse the previously built urban net")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    osm = ET.parse(ROOT / "raw/osm/kowloon_merged.osm.xml")
    tags = {w.get("id"): {t.get("k"): t.get("v") for t in w.findall("tag")}
            for w in osm.getroot().findall("way")}
    defaults = []
    for way in osm.getroot().findall("way"):
        attrs = tags[way.get("id")]
        if attrs.get("highway") and "maxspeed" not in attrs:
            ET.SubElement(way, "tag", k="maxspeed", v="50")
            defaults.append({"osm_way": way.get("id"), "speed_kmh": 50,
                "provenance": "assumed_urban_default_NOT_observed"})
    osm_file = OUT / "osm_with_assumed_defaults.xml"
    osm.write(osm_file, encoding="utf-8", xml_declaration=True)
    (OUT / "speed_default_assumptions.json").write_text(json.dumps(defaults, indent=2), encoding="utf-8")
    exe = str(SUMO_HOME / "bin/netconvert.exe")
    command = [exe, "--osm-files", str(osm_file), "--output-file", str(OUT / "urban.net.xml"),
        "--lefthand", "true", "--geometry.remove", "true", "--junctions.join", "true",
        "--tls.guess-signals", "true", "--tls.discard-simple", "true", "--tls.join", "false",
        "--tls.cycle.time", "90", "--no-turnarounds", "true", "--remove-edges.isolated", "true",
        "--keep-edges.by-vclass", "passenger", "--keep-edges.in-geo-boundary", "114.164,22.300,114.189,22.326",
        "--output.original-names", "true", "--output.street-names", "true",
        "--log", str(OUT / "netconvert.log"), "--error-log", str(OUT / "netconvert_warnings.log")]
    if not (args.reuse_urban and (OUT / "urban.net.xml").exists()):
        result = subprocess.run(command, capture_output=True, timeout=90)
        result.check_returncode()
    net = sumolib.net.readNet(str(OUT / "urban.net.xml"))
    stations = pd.read_csv(ROOT / "processed/detector_locations.csv")
    official = pd.read_csv(ROOT / "processed/detector_official_mapping.csv").set_index("detector_id")
    lanes = pd.read_csv(ROOT / "processed/coverage_by_detector_day.csv").groupby("detector_id").expected_lanes.max()
    old = pd.read_csv(ROOT / "processed/detector_sumo_mapping.csv").set_index("detector_id")
    rankings, mappings = [], []
    for s in stations.itertuples():
        p = net.convertLonLat2XY(s.Longitude, s.Latitude)
        observed_lanes = int(lanes[s.AID_ID_Number])
        official_elevation = int(official.loc[s.AID_ID_Number, "ELEVATION"])
        rank = []
        for edge in net.getEdges():
            if not edge.allows("passenger"):
                continue
            distance, bearing = nearest_segment(p, edge.getShape())
            if distance > 80 or bearing is None:
                continue
            angle = abs((bearing - s.Rotation + 180) % 360 - 180)
            rawid = re.match(r"-?(\d+)", edge.getID()).group(1)
            attrs = tags.get(rawid, {})
            is_bridge = attrs.get("bridge", "no") != "no"
            is_tunnel = attrs.get("tunnel", "no") != "no"
            # Layer numbers differ between providers; only grade category is used.
            grade_conflict = (official_elevation > 0 and not is_bridge) or (official_elevation < 0 and not is_tunnel)
            matched = name_match(edge.getName(), s.Road_EN) or name_match(edge.getName(), str(official.loc[s.AID_ID_Number, "official_name"]).title())
            score = distance + .45 * angle + (0 if matched else 40) + 12 * abs(edge.getLaneNumber() - observed_lanes) + (35 if grade_conflict else 0)
            rank.append({"detector_id": s.AID_ID_Number, "sumo_edge": edge.getID(), "sumo_name": edge.getName(),
                "distance_m": round(distance, 2), "bearing_difference_deg": round(angle, 1),
                "sumo_lanes": edge.getLaneNumber(), "observed_lane_labels": observed_lanes,
                "road_name_match": matched, "official_elevation": official_elevation,
                "osm_bridge": is_bridge, "osm_tunnel": is_tunnel, "grade_conflict": grade_conflict,
                "score": round(score, 3), "osm_maxspeed_tag": attrs.get("maxspeed", "missing_assumed_50"),
                "old_sumo_edge": old.loc[s.AID_ID_Number, "sumo_edge"]})
        rank.sort(key=lambda x: x["score"])
        rankings.extend(dict(r, rank=i + 1) for i, r in enumerate(rank[:5]))
        best = rank[0].copy()
        gap = rank[1]["score"] - best["score"] if len(rank) > 1 else 100
        best["score_gap"] = gap
        best["requires_review"] = (best["distance_m"] > 25 or best["bearing_difference_deg"] > 45 or
            not best["road_name_match"] or best["grade_conflict"] or best["sumo_lanes"] != observed_lanes or gap < 6)
        best["status"] = "automatically_cross_checked_provisional_NOT_field_verified"
        mappings.append(best)
    mapping = pd.DataFrame(mappings)
    mapping.to_csv(OUT / "detector_mapping.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rankings).to_csv(OUT / "detector_candidates.csv", index=False, encoding="utf-8-sig")
    index = mapping.set_index("detector_id")
    allowed = {e.getID() for e in net.getEdges() if "Princess Margaret" in e.getName()
               and e.getLaneNumber() >= 2 and not tags.get(re.match(r"-?(\d+)", e.getID()).group(1), {}).get("highway", "").endswith("_link")}
    north = restricted_path(net, index.loc["AID01113", "sumo_edge"], index.loc["AID01116", "sumo_edge"], allowed)
    south = restricted_path(net, index.loc["AID01208", "sumo_edge"], index.loc["AID01212", "sumo_edge"], allowed)
    if not north or not south:
        raise RuntimeError("Arterial mainline corridor is not connected")
    core = set(north) | set(south)
    boundary_sources, boundary_sinks = set(), set()
    for eid in core:
        edge = net.getEdge(eid)
        boundary_sources.update(e.getID() for e in edge.getIncoming() if e.getID() not in core and e.allows("passenger"))
        boundary_sinks.update(e.getID() for e in edge.getOutgoing() if e.getID() not in core and e.allows("passenger"))
    included = core | boundary_sources | boundary_sinks
    edge_file = OUT / "corridor_edges.txt"
    edge_file.write_text("\n".join(sorted(included)), encoding="utf-8")
    # Explicit IDs avoid Windows netconvert's plain-text file/path decoding failure.
    result = subprocess.run([exe, "--sumo-net-file", str(OUT / "urban.net.xml"), "--keep-edges.explicit", ",".join(sorted(included)),
        "--output-file", str(OUT / "corridor.net.xml"), "--no-turnarounds", "true"], capture_output=True, timeout=60)
    (OUT / "corridor_build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    corridor_net = sumolib.net.readNet(str(OUT / "corridor.net.xml"))
    (OUT / "corridor_definition.json").write_text(json.dumps({"north_mainline": north, "south_mainline": south,
        "core_edges": sorted(core), "upstream_cut_edges": sorted(boundary_sources), "downstream_cut_edges": sorted(boundary_sinks),
        "note": "Reduced mainline and immediate ramp boundaries for data/model sanity checks; not full B implementation"}, indent=2), encoding="utf-8")
    report = {"old_mapping_review_flags": int(old.requires_manual_review.sum()),
        "new_mapping_review_flags": int(mapping.requires_review.sum()), "cross_checked_sites": len(mapping),
        "changed_mapping_ids": mapping.loc[mapping.sumo_edge != mapping.old_sumo_edge, "detector_id"].tolist(),
        "corridor_edges": len(corridor_net.getEdges()), "corridor_tls": len(corridor_net.getTrafficLights()),
        "corridor_mapped_detectors": mapping.loc[mapping.sumo_edge.isin(core), "detector_id"].tolist(),
        "assumptions": ["Missing OSM maxspeed uses assumed 50 km/h; explicit source tags are preserved.",
            "No guessed extra ramp lanes; OSM lane tags are kept.",
            "Generated 90-second signal timing is an assumption, never actual timing.",
            "Automatic GIS/OSM cross-checks do not establish surveyed lane inventory or exact sensor coverage."]}
    (OUT / "network_refinement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
