"""Extend the southern boundary as a controlled topology diagnostic.

AI-assisted: OpenAI Codex; model/version and release date unavailable in this
runtime. Actual usage and human review must be recorded before submission.
This does not infer missing downstream demand or calibrate tunnel capacity.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess

from .paths import APP, REFINED, SUMO_HOME, sumo_exe
import sumolib

MODEL = APP / "models" / "boundary_v1"
FEEDER_MODEL = APP / "models" / "boundary_v2"
THROUGH_MODEL = APP / "models" / "boundary_v3"


def prepare_extended():
    MODEL.mkdir(parents=True, exist_ok=True)
    urban_file = REFINED / "urban.net.xml"
    urban = sumolib.net.readNet(str(urban_file))
    original = sumolib.net.readNet(str(REFINED / "corridor.net.xml"))
    first, last = "528507294", "45740459"
    path, distance = urban.getShortestPath(urban.getEdge(first), urban.getEdge(last), vClass="passenger")
    if not path:
        raise RuntimeError("No connected downstream mainline extension")
    extension = [e.getID() for e in path]
    included = {e.getID() for e in original.getEdges()} | set(extension)
    # Keep adjacent merging/exiting roads so geometry is not cut at every merge.
    for e in path:
        included.update(x.getID() for x in e.getIncoming() if x.allows("passenger"))
        included.update(x.getID() for x in e.getOutgoing() if x.allows("passenger"))
    network = MODEL / "corridor.net.xml"
    env = dict(os.environ, SUMO_HOME=str(SUMO_HOME))
    command = [sumo_exe("netconvert"), "--sumo-net-file", str(urban_file),
               "--keep-edges.explicit", ",".join(sorted(included)), "--output-file", str(network),
               "--no-turnarounds", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, env=env, timeout=90)
    (MODEL / "build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    net = sumolib.net.readNet(str(network))
    if any(not net.hasEdge(e) for e in included):
        raise RuntimeError("Some selected edges disappeared during export")
    definition = {"version": "boundary_v1", "network": "corridor.net.xml",
        "original_edges": len(original.getEdges()), "extended_edges": len(net.getEdges()),
        "extension_edges": extension, "extension_length_m": distance,
        "extend_only_routes_ending_at": first, "last_edge": last,
        "network_sha256": hashlib.sha256(network.read_bytes()).hexdigest(),
        "purpose": "Same upstream demand, extra downstream geometry; diagnostic only",
        "limitations": ["Additional downstream merging traffic remains unknown and is not injected.",
            "Not a calibrated tunnel-capacity boundary; no actual queue state is supplied.",
            "Other original route exits stay unchanged.",
            "AID01213 is a provisional mapping with a direction-review flag; not a fitting target."]}
    (MODEL / "definition.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    return definition


def feeder_path(net, last, blocked, via=None, minimum=400):
    """Trace an explicit inlet upstream, retaining a usable insertion edge.

    This selects a candidate approach, not an observed turning proportion.
    At a fork the widest predecessor is a documented geometric assumption.
    """
    path = list(via or [last])
    distance = sum(net.getEdge(e).getLength() for e in path)
    choices = []
    while distance < minimum or net.getEdge(path[0]).getLength() < 50:
        incoming = [e for e in net.getEdge(path[0]).getIncoming()
                    if e.allows("passenger") and e.getID() not in set(path) | blocked]
        if not incoming:
            break
        incoming.sort(key=lambda e: (-e.getLaneNumber(), -e.getLength(), e.getID()))
        chosen = incoming[0]
        if len(incoming) > 1:
            choices.append({"at_edge": path[0], "candidate_predecessors": [e.getID() for e in incoming],
                "selected": chosen.getID(), "reason": "widest_then_longest_geometric_prior_NOT_observed_turn"})
        path.insert(0, chosen.getID())
        distance += chosen.getLength()
        if len(path) > 60:
            raise ValueError("Upstream feeder trace too long")
    if distance < minimum or net.getEdge(path[0]).getLength() < 20:
        raise ValueError("Insufficient upstream feeder geometry for " + last)
    return path, distance, choices


def prepare_feeders():
    """Versioned topology with three feeder routes into the two missing inlets."""
    if not (MODEL / "definition.json").exists():
        prepare_extended()
    FEEDER_MODEL.mkdir(parents=True, exist_ok=True)
    urban_file = REFINED / "urban.net.xml"
    urban = sumolib.net.readNet(str(urban_file))
    base = sumolib.net.readNet(str(MODEL / "corridor.net.xml"))
    source = json.loads((MODEL / "definition.json").read_text(encoding="utf-8"))
    included = {e.getID() for e in base.getEdges()}
    blocked = set(json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))["core_edges"])
    blocked |= set(source["extension_edges"])
    approaches = [
        ("bg_gascoigne", "45740769", None, "247845479"),
        ("bg_hongchong_turn", "1095134801", ["1095134830", "254006970", "1095134800", "1095134801"], "249233908"),
        ("bg_chatham", "1095134801", ["844368138", "254006970", "1095134800", "1095134801"], "249233908")]
    feeders = []
    for rid, inlet, via, merge in approaches:
        path, length, choices = feeder_path(urban, inlet, blocked, via)
        suffix = source["extension_edges"][source["extension_edges"].index(merge):]
        full = path + suffix
        for a, b in zip(full, full[1:]):
            if urban.getEdge(b) not in urban.getEdge(a).getOutgoing():
                raise ValueError(f"Disconnected feeder {rid}: {a}->{b}")
        feeders.append({"route_id": rid, "direction": "south", "edges": full, "approach_edges": path,
            "inlet_edge": inlet, "merge_edge": merge, "approach_length_m": length,
            "origin_edge_length_m": urban.getEdge(path[0]).getLength(), "upstream_choices": choices,
            "provenance": "OSM_connected_candidate_route_NOT_observed_OD_or_turning_fraction"})
        included.update(full)
    command = [sumo_exe("netconvert"), "--sumo-net-file", str(urban_file),
        "--keep-edges.explicit", ",".join(sorted(included)), "--output-file", str(FEEDER_MODEL / "corridor.net.xml"),
        "--no-turnarounds", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, env=dict(os.environ, SUMO_HOME=str(SUMO_HOME)), timeout=90)
    (FEEDER_MODEL / "build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    exported = sumolib.net.readNet(str(FEEDER_MODEL / "corridor.net.xml"))
    for route in feeders:
        for a, b in zip(route["edges"], route["edges"][1:]):
            if exported.getEdge(b) not in exported.getEdge(a).getOutgoing():
                raise ValueError("Feeder disconnected after network export")
    definition = dict(source, version="boundary_v2", parent_version="boundary_v1", feeders=feeders,
        extended_edges=len(exported.getEdges()), tls_count=len(exported.getTrafficLights()),
        network_sha256=hashlib.sha256((FEEDER_MODEL / "corridor.net.xml").read_bytes()).hexdigest(),
        urban_source_sha256=hashlib.sha256(urban_file.read_bytes()).hexdigest(),
        purpose="Preserve upstream feeder geometry; background rate is a separate explicit scenario input",
        limitations=["Feeder routes and shares are geometric assumptions, not measured OD.",
            "No missing background flow is inferred solely by adding these roads.",
            "Per-lane bus and turn conditions need auditing; generated signal plans, if present, are assumptions.",
            "Downstream residual supply and initial queues remain unknown.",
            "Original detector mapping flags are preserved; additional stations are diagnostics only."])
    (FEEDER_MODEL / "definition.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    return definition


def prepare_continuation():
    """Carry mainline terminal routes through the downstream boundary.

    Candidate-route enumeration has BOTH a terminal at the last core edge
    and an explicit outlet one edge farther on. The first extension only
    caught the latter and left most southbound through traffic disappearing
    immediately after the detector. Keep that failure as versions 1/2.
    """
    if not (FEEDER_MODEL / "definition.json").exists():
        prepare_feeders()
    THROUGH_MODEL.mkdir(parents=True, exist_ok=True)
    source = json.loads((FEEDER_MODEL / "definition.json").read_text(encoding="utf-8"))
    core = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))
    last_core = core["south_mainline"][-1]
    suffix = source["extension_edges"]
    net = sumolib.net.readNet(str(FEEDER_MODEL / "corridor.net.xml"))
    if net.getEdge(suffix[0]) not in net.getEdge(last_core).getOutgoing():
        raise ValueError("Last core mainline does not connect to its downstream continuation")
    shutil.copyfile(FEEDER_MODEL / "corridor.net.xml", THROUGH_MODEL / "corridor.net.xml")
    definition = dict(source, version="boundary_v3", parent_version="boundary_v2",
        route_continuations={last_core: suffix, suffix[0]: suffix[1:]},
        purpose="Repair artificial mainline sinks at the last core detector; keep explicit side exits unchanged")
    definition["limitations"] = source["limitations"] + [
        "Synthetic original OD endpoints are cut boundaries, not observed destinations.",
        "Both southbound mainline terminal representations are continued; explicit side-road destinations stay unchanged."]
    definition.pop("extend_only_routes_ending_at", None)
    (THROUGH_MODEL / "definition.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    return definition
