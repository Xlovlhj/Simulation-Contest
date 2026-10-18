"""Explicit residual supply scenario outside the observed core network.

The virtual signal is a numerical boundary device, NOT an observed or proposed
real traffic signal. It constrains exit service; no observed speed is imposed.
"""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际记录补齐并人工复核。
from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET

from .paths import SUMO_HOME, sumo_exe
import sumolib


def service_cycle(target_vph, lanes, cycle=60., saturation_per_lane=1800., startup_loss=2., yellow=3.):
    """Convert assumed effective capacity to a numerical green-time scenario."""
    nominal = lanes * saturation_per_lane
    if not 0 < target_vph < nominal * (cycle - startup_loss - yellow) / cycle:
        raise ValueError("Supply scenario outside feasible virtual cycle range")
    green = cycle * target_vph / nominal + startup_loss
    return {"target_vph_assumed": target_vph, "lanes": lanes, "cycle_seconds": cycle,
        "saturation_vph_per_lane_assumed": saturation_per_lane, "startup_loss_seconds_assumed": startup_loss,
        "green_seconds": green, "yellow_seconds": yellow, "red_seconds": cycle - green - yellow}


def install_boundary_supply(output, target_vph):
    path = output / "network.net.xml"
    net = sumolib.net.readNet(str(path))
    last = net.getEdge("45740459")
    node = last.getFromNode()
    outgoing = [e.getID() for e in node.getOutgoing() if e.allows("passenger")]
    if outgoing != [last.getID()]:
        raise ValueError("Virtual boundary must only serve the final exit")
    plan = service_cycle(target_vph, last.getLaneNumber())
    converted = output / "supply_network.net.xml"
    command = [sumo_exe("netconvert"), "--sumo-net-file", str(path), "--tls.set", node.getID(),
        "--output-file", str(converted), "--no-turnarounds", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, env=dict(os.environ, SUMO_HOME=str(SUMO_HOME)), timeout=90)
    (output / "supply_build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    tree = ET.parse(converted)
    root = tree.getroot()
    logic = next((t for t in root.findall("tlLogic") if t.get("id") == node.getID()), None)
    if logic is None:
        raise ValueError("Virtual boundary traffic light not generated")
    links = [c for c in root.findall("connection") if c.get("tl") == node.getID()]
    if any(c.get("to") != last.getID() for c in links):
        raise ValueError("Virtual supply would affect an unrelated movement")
    size = max(int(c.get("linkIndex")) for c in links) + 1
    for phase in list(logic):
        logic.remove(phase)
    logic.set("programID", "boundary_supply_assumed")
    for duration, state in [(plan["green_seconds"], "G"), (plan["yellow_seconds"], "y"), (plan["red_seconds"], "r")]:
        ET.SubElement(logic, "phase", duration=str(round(duration, 6)), state=state * size)
    ET.indent(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    converted.unlink()  # Only an intermediate generated copy inside this new run.
    plan.update(node_id=node.getID(), exit_edge=last.getID(), controlled_links=size,
        provenance="NUMERICAL_RESIDUAL_SUPPLY_SCENARIO_NOT_actual_traffic_signal_or_measured_capacity")
    plan["limitations"] = ["Target rate is nominal; actual discharge depends on fleet, car-following and available arrivals.",
        "Cycle, saturation, startup loss and initial phase are assumptions; no observed speed is forced.",
        "This is an external supply hypothesis, not a congestion governance treatment.",
        "No actual incident, capacity reduction or traffic light at this boundary is claimed."]
    (output / "boundary_supply.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan
