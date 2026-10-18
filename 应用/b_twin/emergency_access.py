"""Traceable public-road endpoint, without asserting an ambulance gate."""
# AI辅助：OpenAI Codex；提交前按真实使用记录补齐工具信息并人工复核。
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET

from .paths import APP, DATA, REFINED, SUMO_HOME, sumo_exe
from .boundary import THROUGH_MODEL
import sumolib

MODEL = APP / "models" / "emergency_v1"
ORIGIN = "1433971795"
DESTINATION = "149555152"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_emergency_access():
    if (MODEL / "definition.json").exists():
        definition = json.loads((MODEL / "definition.json").read_text(encoding="utf-8"))
        if sha(MODEL / "corridor.net.xml") != definition["network_sha256"]:
            raise ValueError("Archived emergency network changed; create a new model version")
        return definition
    MODEL.mkdir(parents=True, exist_ok=True)
    urban_path = REFINED / "urban.net.xml"
    urban = sumolib.net.readNet(str(urban_path))
    base_path = THROUGH_MODEL / "corridor.net.xml"
    base = sumolib.net.readNet(str(base_path))
    route, cost = urban.getFastestPath(urban.getEdge(ORIGIN), urban.getEdge(DESTINATION), vClass="passenger")
    if not route:
        raise ValueError("Public-road endpoint is unreachable under normal passenger permissions")
    included = {e.getID() for e in base.getEdges()} | {e.getID() for e in route}
    # Keep conflicting junction approaches so priority is not tested on a
    # fictitious isolated signal. Their traffic demand is still unknown.
    for edge in route:
        node = edge.getToNode()
        if node.getType() == "traffic_light":
            included.update(e.getID() for e in node.getIncoming() if e.allows("passenger"))
            included.update(e.getID() for e in node.getOutgoing() if e.allows("passenger"))
    path = MODEL / "corridor.net.xml"
    command = [sumo_exe("netconvert"), "--sumo-net-file", str(urban_path), "--keep-edges.explicit",
        ",".join(sorted(included)), "--output-file", str(path), "--no-turnarounds", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, timeout=90, env=dict(os.environ, SUMO_HOME=str(SUMO_HOME)))
    (MODEL / "build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    exported = sumolib.net.readNet(str(path))
    ids = [e.getID() for e in route]
    for a, b in zip(ids, ids[1:]):
        if exported.getEdge(b) not in exported.getEdge(a).getOutgoing():
            raise ValueError("Emergency route disconnected after export")
    if not all(exported.getEdge(e).allows("passenger") for e in ids):
        raise ValueError("Normal permissions forbid an emergency route edge")
    hospitals = json.loads((DATA / "raw/hospitals.json").read_text(encoding="utf-8-sig"))
    hospital = next(h for h in hospitals if h["institution_eng"] == "Queen Elizabeth Hospital")
    osm = ET.parse(DATA / "raw/osm/kowloon_merged.osm.xml").getroot()
    target = next(w for w in osm.findall("way") if w.get("id") == DESTINATION)
    tags = {t.get("k"): t.get("v") for t in target.findall("tag")}
    if tags.get("highway") not in {"residential", "tertiary", "secondary", "primary"} or tags.get("access") == "private":
        raise ValueError("Endpoint no longer matches the documented public-road choice")
    lon, lat = exported.convertXY2LonLat(*exported.getEdge(DESTINATION).getShape()[-1])
    definition = {"version": "emergency_v1", "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "parent_version": "boundary_v3", "network": "corridor.net.xml", "external_edges": len(exported.getEdges()),
        "base_edges": len(base.getEdges()), "added_edges": sorted(included - {e.getID() for e in base.getEdges()}),
        "network_sha256": sha(path), "origin_edge": ORIGIN, "destination_edge": DESTINATION,
        "route_edges": ids, "route_external_length_m": sum(e.getLength() for e in route),
        "route_freeflow_cost_s_including_connections": cost, "hospital_official_record": hospital,
        "endpoint": {"longitude": lon, "latitude": lat, "osm_way": DESTINATION, "osm_tags": tags,
            "label": "伊利沙伯医院路公共道路末端；非已核验的救护入口或急诊交接点",
            "selection": "Named public road approaching official hospital location, with directed connected route; not nearest geometry alone"},
        "source_sha256": {"official_hospitals": sha(DATA / "raw/hospitals.json"),
            "osm_highways": sha(DATA / "raw/osm/kowloon_merged.osm.xml"), "urban_network": sha(urban_path),
            "base_network": sha(base_path)},
        "official_hospital_source": "https://www.ha.org.hk/opendata/facility-hosp.json",
        "limitations": ["Hospital location/address and A&E service are official; route topology and road access are supplementary OSM.",
            "Ambulance gate, internal driveway, patient loading and handover are NOT verified or timed.",
            "New local-road cross traffic and actual signal timing are unknown; no claim of a calibrated local network.",
            "Free-flow route is fixed before seed comparisons; no routing benefit is claimed.",
            "Model and priority operation are designed experiments, not an actual incident or approved emergency plan."]}
    (MODEL / "definition.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    return definition
