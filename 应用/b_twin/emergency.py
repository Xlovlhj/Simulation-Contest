"""Emergency transport experiments with a fixed route and ordinary safety rules."""
# AI辅助：OpenAI Codex；提交前据真实记录补齐工具信息并人工复核。
from __future__ import annotations

import csv
import json
import shutil
import time
import xml.etree.ElementTree as ET

import pandas as pd

from .emergency_access import MODEL, build_emergency_access
from .evaluation import evaluate, WARMUP
from .runner import prepare_run, run_traci, write_xml, digest
from .boundary_supply import install_boundary_supply
from .governance import install_meters
import sumolib

VEHICLE = "designed_ambulance_01"
DISPATCH = WARMUP + 35 * 60
POLICIES = {"baseline", "yield", "combined"}


def event_active(now, begin, end):
    return begin <= now < end


def prepare_emergency(seed, policy, run_id, with_task=True, with_event=True):
    if policy not in POLICIES:
        raise ValueError("Unknown emergency policy")
    definition = build_emergency_access()
    output, meta = prepare_run("peak", "through", seed, run_id, "time15", 1800, .5, 900, 900, "feedback")
    # A new versioned network and all final inputs are archived for every arm.
    shutil.copyfile(MODEL / "corridor.net.xml", output / "network.net.xml")
    shutil.copyfile(MODEL / "definition.json", output / "emergency_network_definition.json")
    meta["boundary_supply"] = install_boundary_supply(output, 900)
    feeder_definition = json.loads((output / "network_definition.json").read_text(encoding="utf-8"))
    meta["meter_plan"] = install_meters(output, feeder_definition)
    meta["meter_plan_sha256"] = digest(output / "meter_plan.json")
    meta["network_sha256"] = digest(output / "network.net.xml")
    meta["network_version"] = "emergency_v1"
    net = sumolib.net.readNet(str(output / "network.net.xml"))
    scenario = {"task_id": VEHICLE, "with_task": with_task, "with_event": with_event,
        "dispatch_seconds": DISPATCH, "physical_dispatch": "2026-10-15T08:35:00+08:00",
        "origin_edge": definition["origin_edge"], "destination_edge": definition["destination_edge"],
        "fixed_route_edges": definition["route_edges"], "deadline_seconds_assumed": 180,
        "incident": {"lane": "771547540_0", "begin_seconds": WARMUP + 30 * 60,
            "end_seconds": WARMUP + 45 * 60, "speed_limit_mps_assumed": 3.,
            "label": "08:30—08:45单车道低速作业情景；非已发生事故或实测封道"},
        "yield": {"lookahead_m_assumed": 80., "interval_seconds": 2., "request_duration_s": 3.},
        "signal": {"lookahead_m_assumed": 120., "maximum_extension_s_assumed": 10.,
            "rule": "Extend an already green current phase once; never grant a conflicting green or bypass red"},
        "evidence": "DESIGNED_EVENT_AND_TASK_NOT_real_emergency_records",
        "limitations": definition["limitations"] + [
            "Ambulance proxy has passenger permissions and unchanged safety/lane-change modes in every arm.",
            "No blue-light device, forced speed, moveTo, red-light bypass, signal phase skipping or route change.",
            "Ordinary local-road background demand is unknown and not supplied; results are conditional.",
            "180-second deadline is an experimental threshold, not an official emergency response standard."]}
    (output / "emergency_scenario.json").write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    root = ET.parse(output / "demand.rou.xml").getroot()
    if with_task:
        root.insert(0, ET.Element("vType", id="designed_ambulance_proxy", vClass="passenger", guiShape="emergency",
            length="5.5", minGap="2.5", maxSpeed="19.44", speedFactor="1", speedDev="0", sigma="0.5"))
        root.insert(len(root.findall("vType")), ET.Element("route", id="emergency_fixed_route",
            edges=" ".join(definition["route_edges"])))
        ET.SubElement(root, "vehicle", id=VEHICLE, type="designed_ambulance_proxy", route="emergency_fixed_route",
            depart=str(DISPATCH), departLane="best", departSpeed="0", departPos="0")
        vehicles = pd.read_csv(output / "vehicles.csv")
        task = {"vehicle_id": VEHICLE, "depart": DISPATCH, "phase": "evaluation", "route_id": "emergency_fixed_route",
            "origin_edge": definition["origin_edge"], "original_destination_edge": definition["destination_edge"],
            "base_type": "designed_ambulance_proxy", "sumo_vclass": "passenger", "length_m_assumed": 5.5,
            "speed_factor_assumed": 1., "sigma_assumed": .5, "depart_lane": "best", "depart_speed": "0",
            "source_window": "DESIGNED_TASK", "fleet_provenance": "DESIGNED_TASK", "provenance": scenario["evidence"],
            "population": "emergency"}
        vehicles = pd.concat([vehicles, pd.DataFrame([task])], ignore_index=True).sort_values(["depart", "vehicle_id"])
        vehicles.to_csv(output / "vehicles.csv", index=False, encoding="utf-8-sig")
        agents = sorted(root.findall("vehicle"), key=lambda v: (float(v.get("depart")), v.get("id")))
        for v in root.findall("vehicle"):
            root.remove(v)
        root.extend(agents)
        manifest_path = output / "demand_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(planned_vehicles=len(vehicles), vehicle_manifest_sha256=digest(output / "vehicles.csv"),
            emergency_task=scenario["task_id"])
        manifest["planned_evaluation_vehicles"] += 1
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in root.findall("route"):
        edges = [net.getEdge(e) for e in r.get("edges").split()]
        if any(b not in a.getOutgoing() for a, b in zip(edges, edges[1:])):
            raise ValueError("Disconnected route after emergency network export: " + r.get("id"))
    write_xml(root, output / "demand.rou.xml")
    meta.update(emergency_policy=policy, emergency_scenario=scenario,
        emergency_scenario_sha256=digest(output / "emergency_scenario.json"),
        emergency_network_definition_sha256=digest(output / "emergency_network_definition.json"),
        planned_vehicles=len(pd.read_csv(output / "vehicles.csv")),
        vehicle_manifest_sha256=digest(output / "vehicles.csv"), demand_sha256=digest(output / "demand.rou.xml"))
    (output / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, meta


class EmergencyController:
    def __init__(self, connection, output, metadata):
        self.c, self.meta, self.scenario = connection, metadata, metadata["emergency_scenario"]
        self.policy = metadata["emergency_policy"]
        self.net = sumolib.net.readNet(str(output / "network.net.xml"))
        self.lane_speed = self.c.lane.getMaxSpeed(self.scenario["incident"]["lane"])
        self.restricted, self.departed, self.arrived = False, None, None
        self.next_action = DISPATCH
        self.extended, self.seen_phase = set(), {}
        self.yield_requests = self.green_extensions = 0
        self.default_modes = None
        self.handle = (output / "emergency_commands.csv").open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.handle, fieldnames=["simulation_seconds", "command", "target", "details"])
        self.writer.writeheader()
        self.trace_handle = (output / "emergency_trace_2s.csv").open("w", encoding="utf-8-sig", newline="")
        self.trace_writer = csv.DictWriter(self.trace_handle, fieldnames=["simulation_seconds", "edge", "lane_index",
            "lane_position_m", "speed_mps", "x", "y", "speed_mode", "lane_change_mode"])
        self.trace_writer.writeheader()
        self.output = output

    def record(self, now, command, target, details):
        self.writer.writerow(dict(simulation_seconds=now, command=command, target=target,
            details=json.dumps(details, ensure_ascii=False)))
        self.handle.flush()

    def step(self, now):
        inc = self.scenario["incident"]
        active = self.scenario["with_event"] and event_active(now, inc["begin_seconds"], inc["end_seconds"])
        if active != self.restricted:
            self.c.lane.setMaxSpeed(inc["lane"], inc["speed_limit_mps_assumed"] if active else self.lane_speed)
            self.restricted = active
            self.record(now, "incident_start" if active else "incident_end", inc["lane"],
                {"speed_limit_mps": inc["speed_limit_mps_assumed"] if active else self.lane_speed})
        if VEHICLE in self.c.simulation.getArrivedIDList():
            self.arrived = now
            self.record(now, "task_arrived", VEHICLE, {"endpoint": self.scenario["destination_edge"]})
        if VEHICLE in self.c.simulation.getDepartedIDList():
            self.departed = now
            self.default_modes = {"speed_mode": self.c.vehicle.getSpeedMode(VEHICLE),
                "lane_change_mode": self.c.vehicle.getLaneChangeMode(VEHICLE)}
            self.record(now, "task_inserted", VEHICLE, self.default_modes)
        if self.departed is None or self.arrived is not None or now < self.next_action:
            return
        self.next_action = now + self.scenario["yield"]["interval_seconds"]
        x, y = self.c.vehicle.getPosition(VEHICLE)
        self.trace_writer.writerow({"simulation_seconds": now, "edge": self.c.vehicle.getRoadID(VEHICLE),
            "lane_index": self.c.vehicle.getLaneIndex(VEHICLE), "lane_position_m": self.c.vehicle.getLanePosition(VEHICLE),
            "speed_mps": self.c.vehicle.getSpeed(VEHICLE), "x": x, "y": y,
            "speed_mode": self.c.vehicle.getSpeedMode(VEHICLE), "lane_change_mode": self.c.vehicle.getLaneChangeMode(VEHICLE)})
        if self.policy in {"yield", "combined"}:
            edge = self.c.vehicle.getRoadID(VEHICLE)
            if edge and not edge.startswith(":"):
                lane = self.c.vehicle.getLaneIndex(VEHICLE)
                pos = self.c.vehicle.getLanePosition(VEHICLE)
                lanes = self.net.getEdge(edge).getLanes()
                alternatives = [i for i in [lane - 1, lane + 1] if 0 <= i < len(lanes) and lanes[i].allows("passenger")]
                if alternatives:
                    for vid in self.c.edge.getLastStepVehicleIDs(edge):
                        gap = self.c.vehicle.getLanePosition(vid) - pos
                        if vid != VEHICLE and 0 < gap < self.scenario["yield"]["lookahead_m_assumed"] and self.c.vehicle.getLaneIndex(vid) == lane:
                            permitted = [i for i in alternatives if lanes[i].allows(self.c.vehicle.getVehicleClass(vid))]
                            if not permitted:
                                continue
                            self.c.vehicle.changeLane(vid, permitted[0], self.scenario["yield"]["request_duration_s"])
                            self.yield_requests += 1
                            self.record(now, "safe_lane_change_request", vid, {"target_lane_index": permitted[0],
                                "gap_m": gap, "lane_change_mode": self.c.vehicle.getLaneChangeMode(vid)})
        if self.policy == "combined":
            for tls, link, distance, state in self.c.vehicle.getNextTLS(VEHICLE)[:1]:
                if distance > self.scenario["signal"]["lookahead_m_assumed"]:
                    continue
                phase = self.c.trafficlight.getPhase(tls)
                # One extension per observed green episode, retaining the
                # generated program's compatible movements and clearance.
                previous = self.seen_phase.get(tls)
                if previous is not None and previous != phase:
                    self.extended.discard(tls)
                self.seen_phase[tls] = phase
                if state in "gG" and tls not in self.extended:
                    remaining = max(0., self.c.trafficlight.getNextSwitch(tls) - now)
                    extra = self.scenario["signal"]["maximum_extension_s_assumed"]
                    self.c.trafficlight.setPhaseDuration(tls, remaining + extra)
                    self.extended.add(tls)
                    self.green_extensions += 1
                    self.record(now, "extend_existing_green", tls,
                        {"phase": phase, "link_index": link, "state": state, "extra_seconds": extra,
                            "state_unchanged": self.c.trafficlight.getRedYellowGreenState(tls)})

    def state(self, now):
        result = {"task_id": VEHICLE, "policy": self.policy,
            "status": "arrived" if self.arrived is not None else "running" if self.departed is not None else
                "pending" if now >= DISPATCH and self.scenario["with_task"] else "not_dispatched",
            "incident_active": self.restricted, "yield_requests": self.yield_requests,
            "green_extensions": self.green_extensions, "actual_inserted_seconds": self.departed,
            "arrival_seconds": self.arrived}
        if self.departed is not None and self.arrived is None:
            result.update(edge=self.c.vehicle.getRoadID(VEHICLE), speed_mps=self.c.vehicle.getSpeed(VEHICLE),
                x=self.c.vehicle.getPosition(VEHICLE)[0], y=self.c.vehicle.getPosition(VEHICLE)[1])
        return result

    def close(self):
        self.handle.close()
        self.trace_handle.close()
        result = self.state(self.meta["simulation_end_seconds"])
        result.update(default_safety_modes=self.default_modes, deadline_seconds_assumed=self.scenario["deadline_seconds_assumed"],
            elapsed_from_dispatch_s=self.arrived - DISPATCH if self.arrived is not None else None,
            completed=self.arrived is not None)
        (self.output / "emergency_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_emergency(seed, policy, run_id, with_task=True, with_event=True):
    output, metadata = prepare_emergency(seed, policy, run_id, with_task, with_event)
    started = time.perf_counter()
    try:
        run_traci(output, metadata, EmergencyController)
        metrics = evaluate(output, "peak", metadata)
        metadata.update(status="completed", backend="traci", elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        metadata.update(status="failed", backend="traci", error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise
    finally:
        (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_dir": str(output), "emergency": json.loads((output / "emergency_result.json").read_text(encoding="utf-8")),
        "teleports": metrics["teleports"], "collisions": metrics["collisions"]}
