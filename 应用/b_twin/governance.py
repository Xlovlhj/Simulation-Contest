"""Causal feeder metering under explicit, fixed external supply scenarios.

All arms use identical assumed facilities and exogenous agents. A free arm
leaves meters green. No strategy changes demand, routes or downstream supply.
"""
# AI辅助：OpenAI Codex；精确型号/发布日期不可得，提交前据真实使用记录补齐并人工核查。
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import xml.etree.ElementTree as ET

from .paths import SUMO_HOME, sumo_exe
from .evaluation import WARMUP
import sumolib
import traci.constants as tc

POLICIES = {"free", "fixed", "feedback"}
UPDATE = 30.
SATURATION = 1800.
STARTUP = 2.
YELLOW = 3.
TARGET_OCCUPANCY_PCT = 15.
GAIN_VPH_PER_PP = 30.
MIN_RATE = 120.


def feedback_rate(previous, occupancy_pct, queue_fraction, maximum):
    """ALINEA-style integral occupancy feedback with an explicit queue override.

    Occupancy target/gain/storage are design parameters, not field-calibrated.
    This uses previous-interval simulation sensors; no future observations.
    """
    if not all(math.isfinite(x) for x in [previous, occupancy_pct, queue_fraction, maximum]):
        raise ValueError("Controller inputs must be finite")
    if queue_fraction >= .8:
        return maximum, "queue_override"
    return max(MIN_RATE, min(maximum, previous + GAIN_VPH_PER_PP * (TARGET_OCCUPANCY_PCT - occupancy_pct))), "occupancy_feedback"


def phase_state(elapsed, green, cycle=UPDATE):
    offset = elapsed % cycle
    return "G" if offset < green else "y" if offset < green + YELLOW else "r"


def install_meters(output, definition):
    """Only control feeder-only junctions BEFORE their original priority merge."""
    path = output / "network.net.xml"
    net = sumolib.net.readNet(str(path))
    specs = [
        {"id": "gascoigne", "stop_edge": "514553082", "next_edge": "45740769", "sensor_edge": "247845479",
         "routes": ["bg_gascoigne"], "initial_vph": 300., "maximum_vph": 1200.},
        {"id": "hongchong", "stop_edge": "1095134800", "next_edge": "1095134801", "sensor_edge": "249233908",
         "routes": ["bg_hongchong_turn", "bg_chatham"], "initial_vph": 600., "maximum_vph": 1800.}]
    for spec in specs:
        edge = net.getEdge(spec["stop_edge"])
        node = edge.getToNode()
        if {e.getID() for e in node.getIncoming()} != {spec["stop_edge"]} or {e.getID() for e in node.getOutgoing()} != {spec["next_edge"]}:
            raise ValueError("Meter must not directly control an unrelated or mainline movement")
        approaches = set()
        for route in definition["feeders"]:
            if route["route_id"] in spec["routes"]:
                approaches.update(route["approach_edges"][:route["approach_edges"].index(spec["stop_edge"]) + 1])
        storage = sum(net.getEdge(e).getLength() * net.getEdge(e).getLaneNumber() for e in approaches) / 7.5
        spec.update(node=node.getID(), lanes=edge.getLaneNumber(), approach_edges=sorted(approaches),
            storage_slots_assumed=storage, jam_spacing_m_assumed=7.5)
    converted = output / "meter_conversion.net.xml"
    command = [sumo_exe("netconvert"), "--sumo-net-file", str(path), "--tls.set", ",".join(s["node"] for s in specs),
        "--output-file", str(converted), "--no-turnarounds", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, env=dict(os.environ, SUMO_HOME=str(SUMO_HOME)), timeout=90)
    (output / "meter_build.log").write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    tree = ET.parse(converted)
    root = tree.getroot()
    for spec in specs:
        links = [c for c in root.findall("connection") if c.get("tl") == spec["node"]]
        if not links or any(c.get("from") != spec["stop_edge"] or c.get("to") != spec["next_edge"] for c in links):
            raise ValueError("Meter affects an unrelated movement")
        spec["links"] = max(int(c.get("linkIndex")) for c in links) + 1
        logic = next(t for t in root.findall("tlLogic") if t.get("id") == spec["node"])
        for p in list(logic):
            logic.remove(p)
        logic.set("programID", "assumed_feeder_meter")
        ET.SubElement(logic, "phase", duration="100000", state="G" * spec["links"])
    ET.indent(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    converted.unlink()
    plan = {"facilities": specs, "update_seconds_assumed": UPDATE, "saturation_per_lane_vph_assumed": SATURATION,
        "startup_loss_seconds_assumed": STARTUP, "yellow_seconds_assumed": YELLOW,
        "target_occupancy_pct_assumed": TARGET_OCCUPANCY_PCT, "gain_vph_per_percentage_point_assumed": GAIN_VPH_PER_PP,
        "minimum_rate_vph_assumed": MIN_RATE, "queue_override_fraction_assumed": .8,
        "provenance": "PROPOSED_CONTROL_FACILITIES_AND_PARAMETERS_NOT_observed_real_signals",
        "limitations": ["Placement and storage are geometric feasibility assumptions; legal/installability/field safety unverified.",
            "Simulation state feedback is not live official observation assimilation.",
            "All policies retain original mainline/ramp priority merges and fixed downstream supply.",
            "All-green, fixed and feedback arms have identical converted network and exogenous agents."]}
    (output / "meter_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


class MeterController:
    def __init__(self, connection, output, policy, plan):
        if policy not in POLICIES:
            raise ValueError("Unknown meter policy")
        self.c, self.policy, self.plan = connection, policy, plan
        self.rates = {s["id"]: s["initial_vph"] for s in plan["facilities"]}
        self.greens = {}
        self.samples = {s["id"]: [] for s in plan["facilities"]}
        self.previous_states = {}
        self.next_update = WARMUP
        self.handle = (output / "control_30s.csv").open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.handle, fieldnames=["simulation_seconds", "meter", "policy", "sensor_occupancy_pct",
            "approach_halting", "queue_fraction_assumed", "rate_vph_nominal", "green_seconds", "reason"])
        self.writer.writeheader()
        for s in plan["facilities"]:
            self.set_state(s, "G")

    def set_state(self, spec, state):
        if self.previous_states.get(spec["id"]) != state:
            self.c.trafficlight.setRedYellowGreenState(spec["node"], state * spec["links"])
            self.previous_states[spec["id"]] = state

    def step(self, now):
        for s in self.plan["facilities"]:
            values = self.samples[s["id"]]
            values.append(self.c.edge.getSubscriptionResults(s["sensor_edge"])[tc.LAST_STEP_OCCUPANCY])
            del values[:-int(UPDATE / .5)]
        if now + 1e-7 >= self.next_update:
            for s in self.plan["facilities"]:
                values = self.samples[s["id"]]
                occupancy = sum(values) / len(values)
                queue = sum(self.c.edge.getSubscriptionResults(e)[tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for e in s["approach_edges"])
                fraction = queue / s["storage_slots_assumed"]
                if self.policy == "feedback":
                    self.rates[s["id"]], reason = feedback_rate(self.rates[s["id"]], occupancy, fraction, s["maximum_vph"])
                else:
                    reason = "always_green_reference" if self.policy == "free" else "fixed_rate"
                green = min(UPDATE - YELLOW - .5, UPDATE * self.rates[s["id"]] / (s["lanes"] * SATURATION) + STARTUP)
                self.greens[s["id"]] = green
                self.writer.writerow({"simulation_seconds": now, "meter": s["id"], "policy": self.policy,
                    "sensor_occupancy_pct": occupancy, "approach_halting": queue, "queue_fraction_assumed": fraction,
                    "rate_vph_nominal": self.rates[s["id"]], "green_seconds": UPDATE if self.policy == "free" else green, "reason": reason})
                self.samples[s["id"]].clear()
            self.handle.flush()
            self.next_update += UPDATE
        for s in self.plan["facilities"]:
            state = "G" if now < WARMUP or self.policy == "free" else phase_state(now - WARMUP, self.greens[s["id"]])
            self.set_state(s, state)
    def close(self):
        self.handle.close()
