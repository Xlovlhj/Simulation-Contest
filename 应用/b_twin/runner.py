"""Prepare isolated runs, execute SUMO and keep reproducible state/results.

AI-assisted: OpenAI Codex; exact model/release date unavailable here. Before
submission record actual usage and human review per the competition rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import csv
from bisect import bisect_left
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

import pandas as pd

from .paths import APP, INPUTS, REFINED, SUMO_HOME, sumo_exe
from .boundary import MODEL, FEEDER_MODEL, THROUGH_MODEL, prepare_extended, prepare_feeders, prepare_continuation
from .evaluation import CASES, WARMUP, evaluate
import sumolib

TZ = timezone(timedelta(hours=8))


def write_xml(root, path):
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_run(case, network_version, seed, run_id=None, demand_mode="time15", clearance=900, entry_ridge=0,
                background_vph=0, supply_vph=0, meter_policy=None):
    if case not in CASES or network_version not in {"original", "extended", "feeders", "through"}:
        raise ValueError("Unknown case or network version")
    if demand_mode not in {"legacy", "fixedmean", "time15"} or clearance < 0:
        raise ValueError("Invalid demand mode or clearance duration")
    if entry_ridge < 0 or (demand_mode != "time15" and entry_ridge != 0):
        raise ValueError("Entry penalty must be nonnegative and requires time15 demand")
    if background_vph < 0 or (background_vph and (network_version not in {"feeders", "through"} or demand_mode == "legacy")):
        raise ValueError("Background scenario requires feeder network and fixed agents")
    if network_version in {"feeders", "through"} and demand_mode == "legacy":
        raise ValueError("Feeder scenario requires fixed agents")
    if supply_vph < 0 or (supply_vph and network_version != "through"):
        raise ValueError("Residual supply scenario requires corrected through network")
    if not all(math.isfinite(x) for x in [entry_ridge, background_vph, supply_vph]):
        raise ValueError("Scenario parameters must be finite")
    if meter_policy is not None and (meter_policy not in {"free", "fixed", "feedback"} or network_version != "through"):
        raise ValueError("Meter policies require the corrected through network")
    if run_id and (not run_id.replace("_", "").replace("-", "").isalnum()):
        raise ValueError("Run ID must contain only letters, numbers, underscore or hyphen")
    run_id = run_id or datetime.now(TZ).strftime("%Y%m%d_%H%M%S_%f") + f"_{case}_{network_version}_{seed}"
    output = APP / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    definition = None
    if network_version in {"extended", "feeders", "through"}:
        model = {"extended": MODEL, "feeders": FEEDER_MODEL, "through": THROUGH_MODEL}[network_version]
        if not (model / "definition.json").exists():
            {"extended": prepare_extended, "feeders": prepare_feeders, "through": prepare_continuation}[network_version]()
        definition = json.loads((model / "definition.json").read_text(encoding="utf-8"))
        network = model / "corridor.net.xml"
        shutil.copyfile(model / "definition.json", output / "network_definition.json")
    else:
        network = REFINED / "corridor.net.xml"
    # Each run is self-contained for network, demand, detectors and config.
    shutil.copyfile(network, output / "network.net.xml")
    supply = None
    if supply_vph:
        from .boundary_supply import install_boundary_supply
        supply = install_boundary_supply(output, supply_vph)
    meter_plan = None
    if meter_policy is not None:
        from .governance import install_meters
        meter_plan = install_meters(output, definition)
    demand_manifest = None
    if demand_mode == "legacy":
        routes = ET.parse(INPUTS / f"{case}_diagnostic.rou.xml").getroot()
    else:
        from .demand import build_fixed_demand
        routes, demand_manifest = build_fixed_demand(output, case, demand_mode, seed, entry_ridge)
    extended = 0
    if definition:
        suffix = definition["extension_edges"]
        continuations = definition.get("route_continuations", {suffix[0]: suffix[1:]})
        for route in routes.findall("route"):
            edges = route.get("edges").split()
            if edges[-1] in continuations:
                route.set("edges", " ".join(edges + continuations[edges[-1]]))
                extended += 1
    net = sumolib.net.readNet(str(output / "network.net.xml"))
    if network_version in {"feeders", "through"}:
        from .background import add_background
        routes, demand_manifest = add_background(output, routes, demand_manifest, definition, net, case, seed, background_vph)
    for route in routes.findall("route"):
        edges = [net.getEdge(e) for e in route.get("edges").split()]
        if any(b not in a.getOutgoing() for a, b in zip(edges, edges[1:])):
            raise ValueError("Disconnected route " + route.get("id"))
    if demand_manifest:
        by_route = {r.get("id"): r.get("edges").split() for r in routes.findall("route")}
        vehicles = pd.read_csv(output / "vehicles.csv")
        for rid, group in vehicles.groupby("route_id"):
            for vclass in group.sumo_vclass.unique():
                if not all(net.getEdge(e).allows(vclass) for e in by_route[rid]):
                    raise ValueError(f"Network forbids fixed vehicle class: {rid}/{vclass}; do not resample paired demand")
    write_xml(routes, output / "demand.rou.xml")
    detectors = ET.parse(INPUTS / f"{case}_detectors.add.xml").getroot()
    for detector in detectors:
        detector.set("file", "e1.xml")
        detector.set("period", "300")
    if network_version in {"feeders", "through"}:
        # Added stations remain provisional diagnostics; original flags are
        # preserved, and safe core stations keep the same primary metric basis.
        mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
        sites = pd.read_csv(REFINED.parent / "processed/detector_locations.csv").set_index("AID_ID_Number")
        existing = {d.get("id").split("__")[0] for d in detectors}
        for row in mapping.itertuples():
            if row.detector_id in existing or not net.hasEdge(row.sumo_edge):
                continue
            xy = net.convertLonLat2XY(sites.loc[row.detector_id, "Longitude"], sites.loc[row.detector_id, "Latitude"])
            for lane in net.getEdge(row.sumo_edge).getLanes():
                pos, _ = lane.getClosestLanePosAndDist(xy)
                ET.SubElement(detectors, "inductionLoop", id=f"{row.detector_id}__{lane.getIndex()}",
                    lane=lane.getID(), pos=str(max(.1, min(lane.getLength() - .1, pos))), period="300", file="e1.xml")
        # Leave room for the entire vehicle to clear this auxiliary loop.
        # At sink - 0.1 m, arriving vehicles can disappear before their rear
        # clears; historical nVehContrib then undercounts actual exit arrivals.
        tail_margin = float(vehicles.length_m_assumed.max()) + 5.
        for lane in net.getEdge("45740459").getLanes():
            if lane.getLength() <= tail_margin:
                raise ValueError("Exit edge too short for a complete boundary loop crossing")
            ET.SubElement(detectors, "inductionLoop", id=f"boundary_out__{lane.getIndex()}",
                lane=lane.getID(), pos=str(lane.getLength() - tail_margin), period="300", file="boundary_out.xml")
    write_xml(detectors, output / "detectors.add.xml")
    cfg = ET.Element("configuration")
    for section, settings in {
        "input": {"net-file": "network.net.xml", "route-files": "demand.rou.xml", "additional-files": "detectors.add.xml"},
        "time": {"step-length": "0.5", "end": str(CASES[case][1] + WARMUP + clearance)},
        "output": {"summary-output": "summary.xml", "statistic-output": "statistics.xml",
            "tripinfo-output": "tripinfo.xml", "tripinfo-output.write-unfinished": "true",
            "tripinfo-output.write-undeparted": "true"},
        "report": {"no-step-log": "true", "duration-log.disable": "true", "xml-validation": "never"},
        "random_number": {"seed": str(seed)},
    }.items():
        part = ET.SubElement(cfg, section)
        for key, value in settings.items():
            ET.SubElement(part, key, value=value)
    if supply_vph or meter_policy is not None:
        processing = ET.SubElement(cfg, "processing")
        # Queueing at an explicit exit constraint is intentional; vehicles
        # must stay accounted for instead of jumping after a 300-second wait.
        ET.SubElement(processing, "time-to-teleport", value="-1")
    if meter_policy is not None:
        outputs = cfg.find("output")
        for key, value in {"vehroute-output": "vehroute.xml", "vehroute-output.exit-times": "true",
                "vehroute-output.last-route": "true", "vehroute-output.write-unfinished": "true"}.items():
            ET.SubElement(outputs, key, value=value)
    write_xml(cfg, output / "run.sumocfg")
    metadata = {"run_id": run_id, "created_at": datetime.now(TZ).isoformat(), "case": case,
        "network_version": network_version, "seed": seed, "physical_start": CASES[case][0],
        "step_seconds": .5, "warmup_seconds": WARMUP, "evaluation_seconds": CASES[case][1],
        "demand_mode": demand_mode, "clearance_seconds": clearance, "entry_ridge": entry_ridge,
        "background_vph_scenario": background_vph,
        "residual_supply_vph_scenario": supply_vph, "boundary_supply": supply,
        "teleport_timeout_seconds": -1 if supply_vph or meter_policy is not None else 300,
        "meter_policy": meter_policy, "meter_plan": meter_plan,
        "meter_plan_sha256": digest(output / "meter_plan.json") if meter_plan else None,
        "core_vehicle_manifest_sha256": demand_manifest.get("core_vehicle_manifest_sha256") if demand_manifest else None,
        "simulation_end_seconds": CASES[case][1] + WARMUP + clearance,
        "demand": "Nonunique inferred route demand, not observed OD",
        "vehicle_manifest_sha256": demand_manifest["vehicle_manifest_sha256"] if demand_manifest else None,
        "planned_vehicles": demand_manifest["planned_vehicles"] if demand_manifest else None,
        "extended_route_count": extended, "network_sha256": digest(output / "network.net.xml"),
        "route_continuations": definition.get("route_continuations", {}) if definition else {},
        "network_definition_sha256": digest(output / "network_definition.json") if definition else None,
        "demand_sha256": digest(output / "demand.rou.xml"),
        "limitations": ["No live official observation assimilation in this run; any feedback uses simulated sensors.",
            "Background demand, when nonzero, is a designed sensitivity scenario, not measured or uniquely inferred.",
            "Initial congestion is not observed; 300-second warmup starts from an empty network.",
            "Residual supply, when enabled, is a numerical exit constraint, NOT an observed or real traffic signal.",
            "Clearance is a separate diagnostic with no new departures, excluded from station error metrics.",
            "Legacy flows are not a fixed paired vehicle list." if demand_mode == "legacy" else
                "Fixed synthetic vehicles are not real identities; per-step SUMO stochastic draws can diverge."]}
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, metadata


def run_traci(output, metadata, service_factory=None):
    import traci
    import traci.constants as tc
    label = metadata["run_id"]
    connection = None
    controller = None
    accounting_handle = None
    service = None
    monitored = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    net = sumolib.net.readNet(str(output / "network.net.xml"))
    monitored = monitored[monitored.sumo_edge.map(net.hasEdge)]
    try:
        traci.start([sumo_exe(), "-c", str(output / "run.sumocfg"),
                     "--log", str(output / "sumo.log"), "--error-log", str(output / "warnings.log")], label=label)
        connection = traci.getConnection(label)
        if metadata.get("meter_policy") is not None:
            from .governance import MeterController
            sensors = {s["sensor_edge"] for s in metadata["meter_plan"]["facilities"]}
            for edge in net.getEdges():
                variables = [tc.LAST_STEP_VEHICLE_NUMBER, tc.LAST_STEP_VEHICLE_HALTING_NUMBER]
                if edge.getID() in sensors:
                    variables.append(tc.LAST_STEP_OCCUPANCY)
                connection.edge.subscribe(edge.getID(), variables)
            controller = MeterController(connection, output, metadata["meter_policy"], metadata["meter_plan"])
            accounting_handle = (output / "network_state_30s.csv").open("w", encoding="utf-8-sig", newline="")
            accounting_writer = csv.DictWriter(accounting_handle, fieldnames=["simulation_seconds", "physical_time", "phase",
                "running", "pending", "arrived_cumulative", "inserted_cumulative", "scheduled_so_far", "not_yet_attempted",
                "external_edge_vehicles", "internal_edge_vehicles", "south_mainline_vehicles", "feeder_vehicles", "halting_external"])
            accounting_writer.writeheader()
            departures = sorted(pd.read_csv(output / "vehicles.csv").depart)
            core_definition = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))
            south = set(core_definition["south_mainline"]) | set(metadata["route_continuations"]["1095134817"])
            feeders = set(e for s in metadata["meter_plan"]["facilities"] for e in s["approach_edges"])
            inserted = arrived = 0
            next_accounting = 0.
        if service_factory is not None:
            service = service_factory(connection, output, metadata)
        end = metadata["simulation_end_seconds"]
        t0 = datetime.fromisoformat(metadata["physical_start"]).replace(tzinfo=TZ)
        next_sample = 0.
        with (output / "states_60s.jsonl").open("w", encoding="utf-8") as handle:
            while connection.simulation.getTime() < end:
                connection.simulationStep()
                now = connection.simulation.getTime()
                if controller is not None:
                    controller.step(now)
                    inserted += connection.simulation.getDepartedNumber()
                    arrived += connection.simulation.getArrivedNumber()
                    if now >= next_accounting:
                        es = connection.edge.getAllSubscriptionResults()
                        counts = {e: v[tc.LAST_STEP_VEHICLE_NUMBER] for e, v in es.items()}
                        running = connection.vehicle.getIDCount()
                        pending = len(connection.simulation.getPendingVehicles())
                        scheduled = bisect_left(departures, now)
                        accounting_writer.writerow({"simulation_seconds": now,
                            "physical_time": (t0 + timedelta(seconds=now - WARMUP)).isoformat(),
                            "phase": "warmup" if now < WARMUP else "evaluation" if now < WARMUP + metadata["evaluation_seconds"] else "clearance",
                            "running": running, "pending": pending, "arrived_cumulative": arrived, "inserted_cumulative": inserted,
                            "scheduled_so_far": scheduled, "not_yet_attempted": scheduled - inserted - pending,
                            "external_edge_vehicles": sum(counts.values()), "internal_edge_vehicles": running - sum(counts.values()),
                            "south_mainline_vehicles": sum(counts.get(e, 0) for e in south),
                            "feeder_vehicles": sum(counts.get(e, 0) for e in feeders),
                            "halting_external": sum(v[tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for v in es.values())})
                        accounting_handle.flush()
                        next_accounting = (int(now // 30) + 1) * 30
                if service is not None:
                    service.step(now)
                if now < next_sample:
                    continue
                state = {"simulation_seconds": now, "physical_time": (t0 + timedelta(seconds=now - WARMUP)).isoformat(),
                    "mode": "historical_model_diagnostic", "phase": "warmup" if now < WARMUP else
                        "evaluation" if now < WARMUP + metadata["evaluation_seconds"] else "clearance",
                    "running": connection.vehicle.getIDCount(), "pending": len(connection.simulation.getPendingVehicles()),
                    "edges": []}
                if service is not None:
                    state["emergency"] = service.state(now)
                for row in monitored.itertuples():
                    e = net.getEdge(row.sumo_edge)
                    lon, lat = net.convertXY2LonLat(*e.getShape()[len(e.getShape()) // 2])
                    state["edges"].append({"detector_id": row.detector_id, "edge_id": row.sumo_edge,
                        "longitude": lon, "latitude": lat, "speed_kmh": 3.6 * connection.edge.getLastStepMeanSpeed(row.sumo_edge),
                        "vehicles": connection.edge.getLastStepVehicleNumber(row.sumo_edge),
                        "halting_vehicles": connection.edge.getLastStepHaltingNumber(row.sumo_edge),
                        "mapping_requires_review": bool(row.requires_review), "source": "simulated_edge_state_NOT_observed"})
                handle.write(json.dumps(state, ensure_ascii=False) + "\n")
                handle.flush()
                next_sample = (int(now // 60) + 1) * 60
    finally:
        if service is not None:
            service.close()
        if controller is not None:
            controller.close()
        if accounting_handle is not None:
            accounting_handle.close()
        if connection is not None:
            connection.close()


def execute(case, network_version="original", seed=20261017, backend="subprocess", run_id=None,
            demand_mode="time15", clearance=900, entry_ridge=0, background_vph=0, supply_vph=0, meter_policy=None):
    if meter_policy is not None and backend != "traci":
        raise ValueError("Meter policy requires --backend traci")
    output, metadata = prepare_run(case, network_version, seed, run_id, demand_mode, clearance, entry_ridge, background_vph, supply_vph, meter_policy)
    started = time.perf_counter()
    try:
        if backend == "traci":
            run_traci(output, metadata)
        else:
            env = dict(os.environ, SUMO_HOME=str(SUMO_HOME))
            result = subprocess.run([sumo_exe(), "-c", str(output / "run.sumocfg"), "--log", str(output / "sumo.log"),
                                     "--error-log", str(output / "warnings.log")], capture_output=True, env=env, timeout=180)
            (output / "process.log").write_bytes(result.stdout + result.stderr)
            result.check_returncode()
        metrics = evaluate(output, case, metadata)
        metadata.update(status="completed", backend=backend, elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        metadata.update(status="failed", backend=backend, error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise
    finally:
        (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_dir": str(output), **metrics}
