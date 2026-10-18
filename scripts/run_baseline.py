#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date
# was not provided; the team must review this code and complete AI disclosure.
"""Run an uncalibrated SUMO technical baseline from accepted Baidu paths.

Standard-library only. No credentials or network requests are used. All generated
files, caches and subprocess working directories are inside this workspace.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
EARTH_RADIUS_M = 6371008.8


def workspace_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path must stay inside workspace: {value}")
    if path.name.endswith(".local.json"):
        raise ValueError("Private configuration files are not baseline inputs")
    return path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def validate_config(config: dict) -> None:
    positive = ("simulation_duration_s", "departure_duration_s", "departure_interval_s",
                "step_length_s", "fcd_period_s", "speed_limit_kmh", "vehicle_length_m",
                "accel_m_s2", "decel_m_s2", "reaction_time_s", "binary_timeout_s")
    for key in positive:
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
    if config["departure_duration_s"] > config["simulation_duration_s"]:
        raise ValueError("Departure window cannot exceed simulation duration")
    if config.get("lane_count") != 1:
        raise ValueError("This technical baseline supports one assumed lane only")
    if not isinstance(config.get("seed"), int) or isinstance(config["seed"], bool):
        raise ValueError("seed must be an integer")
    if not 0 <= config.get("driver_imperfection", -1) <= 1:
        raise ValueError("driver_imperfection must be in [0, 1]")
    if not isinstance(config.get("min_gap_m"), (int, float)) or config["min_gap_m"] < 0:
        raise ValueError("min_gap_m must be non-negative")
    if config.get("model_status") != "uncalibrated_technical_baseline":
        raise ValueError("model_status must identify an uncalibrated technical baseline")


def validate_bounds(bounds: dict) -> dict[str, float]:
    result = {key: float(bounds[key]) for key in ("west", "south", "east", "north")}
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError("Bounds must be finite")
    if not (-180 <= result["west"] < result["east"] <= 180 and
            -90 <= result["south"] < result["north"] <= 90):
        raise ValueError("Invalid geographical bounds")
    return result


def in_bounds(point: tuple[float, float], bounds: dict) -> bool:
    lng, lat = point
    return bounds["west"] <= lng <= bounds["east"] and bounds["south"] <= lat <= bounds["north"]


def project_point(point: tuple[float, float], center: tuple[float, float]) -> tuple[float, float]:
    """Local equirectangular engineering projection; BD-09 is kept explicit."""
    lng, lat = point
    lng0, lat0 = center
    return (EARTH_RADIUS_M * math.radians(lng - lng0) * math.cos(math.radians(lat0)),
            EARTH_RADIUS_M * math.radians(lat - lat0))


def prepare_network(dataset: dict, config: dict) -> dict:
    if dataset.get("study_area_id") != config["study_area_id"]:
        raise ValueError("Input study area does not match baseline configuration")
    if dataset.get("coordinate_system") != "bd09ll":
        raise ValueError("Expected explicit bd09ll source coordinates")
    bounds = validate_bounds(dataset.get("model_bounds") or dataset["collection_bounds"])
    center = ((bounds["west"] + bounds["east"]) / 2, (bounds["south"] + bounds["north"]) / 2)
    nodes, edges, routes, connections = {}, {}, [], set()
    rejected = []
    accepted_ids = set()
    for source in dataset.get("routes", []):
        if source.get("accepted_surface") is not True:
            rejected.append({"id": source.get("id"), "reason": "not_accepted_surface"})
            continue
        if source.get("use_for_baseline", True) is not True:
            rejected.append({"id": source.get("id"), "reason": "not_selected_for_baseline",
                             "source_reason": source.get("baseline_exclusion_reason") or source.get("baseline_selection_note")})
            continue
        source_id = str(source["id"])
        if source_id in accepted_ids:
            raise ValueError(f"Duplicate accepted route id: {source_id}")
        accepted_ids.add(source_id)
        points = []
        for raw in source.get("points", []):
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise ValueError(f"Invalid coordinate pair in route {source_id}")
            point = tuple(float(value) for value in raw)
            if not all(math.isfinite(value) for value in point) or not in_bounds(point, bounds):
                raise ValueError(f"Accepted route {source_id} has a point outside model bounds")
            point = (round(point[0], 6), round(point[1], 6))
            if not points or points[-1] != point:
                points.append(point)
        if len(points) < 2:
            raise ValueError(f"Accepted route {source_id} has no nonzero geometry")
        edge_chain = []
        for start, end in zip(points, points[1:]):
            for point in (start, end):
                if point not in nodes:
                    nodes[point] = {"id": f"n{len(nodes):06d}", "point": point, "xy": project_point(point, center)}
            key = (start, end)  # Direction is part of the key; never reverse a path.
            if key not in edges:
                a, b = nodes[start]["xy"], nodes[end]["xy"]
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length <= 0:
                    continue
                edges[key] = {"id": f"e{len(edges):06d}", "from": nodes[start]["id"],
                              "to": nodes[end]["id"], "length_m": length}
            edge_chain.append(edges[key]["id"])
        connections.update(zip(edge_chain, edge_chain[1:]))
        length_by_id = {edge["id"]: edge["length_m"] for edge in edges.values()}
        routes.append({"id": f"r{len(routes):04d}", "source_id": source_id,
                       "origin_id": source.get("origin_id"), "destination_id": source.get("destination_id"),
                       "edge_ids": edge_chain, "projected_length_m": sum(length_by_id[e] for e in edge_chain),
                       "platform_distance_m": source.get("distance_m"), "platform_duration_s": source.get("duration_s"),
                       "source_response": source.get("source_response"), "requested_at": source.get("requested_at")})
    if not routes:
        raise ValueError("No accepted surface routes are available")
    return {"bounds": bounds, "center": center, "nodes": list(nodes.values()), "edges": list(edges.values()),
            "routes": routes, "connections": sorted(connections), "excluded_routes": rejected}


def create_departures(network: dict, config: dict) -> list[dict]:
    result = []
    count = math.ceil(config["departure_duration_s"] / config["departure_interval_s"])
    for index in range(count):
        departure = index * config["departure_interval_s"]
        if departure >= config["departure_duration_s"]:
            break
        for route in network["routes"]:
            result.append({"id": f"{route['id']}_v{index:05d}", "route": route["id"], "depart": departure})
    return result


def write_inputs(folder: Path, network: dict, config: dict, departures: list[dict]) -> None:
    nodes_xml = ET.Element("nodes")
    for node in network["nodes"]:
        ET.SubElement(nodes_xml, "node", id=node["id"], x=f"{node['xy'][0]:.6f}",
                      y=f"{node['xy'][1]:.6f}", type="priority")
    write_xml(folder / "network.nod.xml", nodes_xml)
    edges_xml = ET.Element("edges")
    for edge in network["edges"]:
        ET.SubElement(edges_xml, "edge", {"id": edge["id"], "from": edge["from"], "to": edge["to"],
                      "numLanes": "1", "speed": str(config["speed_limit_kmh"] / 3.6),
                      "length": f"{edge['length_m']:.6f}", "spreadType": "center", "priority": "1"})
    write_xml(folder / "network.edg.xml", edges_xml)
    connections_xml = ET.Element("connections")
    # netconvert infers connections at terminal edges with no explicit successor.
    # Delete every topologically possible but unobserved pair, including at those
    # endpoints. An independent audit below verifies the built network exactly.
    allowed = set(network["connections"])
    outgoing = {}
    for edge in network["edges"]:
        outgoing.setdefault(edge["from"], []).append(edge["id"])
    for edge in network["edges"]:
        for successor in outgoing.get(edge["to"], []):
            if (edge["id"], successor) not in allowed:
                ET.SubElement(connections_xml, "delete", {"from": edge["id"], "to": successor})
    for start, end in network["connections"]:
        ET.SubElement(connections_xml, "connection", {"from": start, "to": end, "fromLane": "0", "toLane": "0"})
    write_xml(folder / "network.con.xml", connections_xml)
    route_xml = ET.Element("routes")
    ET.SubElement(route_xml, "vType", id="assumed_car", vClass="passenger", length=str(config["vehicle_length_m"]),
                  minGap=str(config["min_gap_m"]), maxSpeed=str(config["speed_limit_kmh"] / 3.6),
                  accel=str(config["accel_m_s2"]), decel=str(config["decel_m_s2"]),
                  tau=str(config["reaction_time_s"]), sigma=str(config["driver_imperfection"]), speedFactor="1", speedDev="0")
    for route in network["routes"]:
        ET.SubElement(route_xml, "route", id=route["id"], edges=" ".join(route["edge_ids"]))
    for item in departures:
        ET.SubElement(route_xml, "vehicle", id=item["id"], type="assumed_car", route=item["route"],
                      depart=str(item["depart"]), departLane="0", departSpeed="0")
    write_xml(folder / "demand.rou.xml", route_xml)
    cfg_xml = ET.Element("configuration")
    sections = {
        "input": {"net-file": "network.net.xml", "route-files": "demand.rou.xml"},
        "time": {"begin": "0", "end": str(config["simulation_duration_s"]), "step-length": str(config["step_length_s"])},
        # A collision must remain visible and invalidate the run. SUMO's default
        # collision teleport is independent of time-to-teleport and hides delay.
        "processing": {"time-to-teleport": "-1", "collision.action": "warn",
                       "collision.check-junctions": "true"},
        "report": {"no-step-log": "true", "duration-log.statistics": "true", "xml-validation": "never"},
        "random_number": {"seed": str(config["seed"])},
        "output": {"tripinfo-output": "tripinfo.xml", "tripinfo-output.write-unfinished": "true",
                   "tripinfo-output.write-undeparted": "true", "fcd-output": "fcd.xml", "summary-output": "summary.xml",
                   "collision-output": "collisions.xml"},
        "fcd_device": {"device.fcd.period": str(config["fcd_period_s"])},
    }
    for section, values in sections.items():
        parent = ET.SubElement(cfg_xml, section)
        for key, value in values.items():
            ET.SubElement(parent, key, value=value)
    write_xml(folder / "baseline.sumocfg", cfg_xml)
    write_json(folder / "route_manifest.json", {key: value for key, value in network.items() if key not in ("nodes", "edges")})


def compute_metrics(trips: list[dict], departures: list[dict], end_time: float, last_summary: dict | None = None) -> dict:
    scheduled = {item["id"]: float(item["depart"]) for item in departures}
    by_id = {}
    for trip in trips:
        if trip["id"] not in scheduled or trip["id"] in by_id:
            raise ValueError("Unexpected or duplicate tripinfo vehicle id")
        by_id[trip["id"]] = trip
    departed = arrived = 0
    in_network_time = departure_wait = 0.0
    completed_times, completed_full_times = [], []
    route_totals = {}
    for item in departures:
        route_totals.setdefault(item["route"], {"planned": 0, "departed": 0, "arrived": 0, "unfinished_on_network": 0})
        route_totals[item["route"]]["planned"] += 1
        trip = by_id.get(item["id"])
        depart = float(trip.get("depart", -1)) if trip else -1
        if depart < 0:
            departure_wait += max(0.0, end_time - float(item["depart"]))
            continue
        if depart > end_time:
            raise ValueError("Trip departure is after simulation end")
        departed += 1
        route_totals[item["route"]]["departed"] += 1
        arrival = float(trip.get("arrival", -1))
        departure_wait += max(0.0, depart - float(item["depart"]))
        if arrival >= 0:
            if not depart <= arrival <= end_time + 1e-6:
                raise ValueError("Invalid completed trip times")
            arrived += 1
            route_totals[item["route"]]["arrived"] += 1
            travel_time = arrival - depart
            completed_times.append(travel_time)
            completed_full_times.append(arrival - float(item["depart"]))
        else:
            travel_time = end_time - depart
            route_totals[item["route"]]["unfinished_on_network"] += 1
        in_network_time += travel_time
    unfinished = departed - arrived
    not_departed = len(scheduled) - departed
    summary_ok = None
    if last_summary:
        checks = {"inserted": departed, "arrived": arrived, "running": unfinished}
        summary_ok = all(int(float(last_summary[k])) == expected for k, expected in checks.items() if k in last_summary)
        if not summary_ok:
            raise ValueError("Tripinfo counts disagree with final SUMO summary")
    return {
        "model_status": "uncalibrated_technical_baseline", "observation_status": "simulated_assumed_demand",
        "simulation_end_s": end_time, "planned": len(scheduled), "departed": departed, "arrived": arrived,
        "unfinished_on_network": unfinished, "not_departed": not_departed,
        "unfinished_total": unfinished + not_departed,
        "completion_rate": arrived / len(scheduled) if scheduled else None,
        "total_in_network_vehicle_time_s": in_network_time,
        "total_departure_wait_time_s": departure_wait,
        "total_system_vehicle_time_s": in_network_time + departure_wait,
        "completed_trip_count": len(completed_times),
        "completed_travel_time_mean_s": sum(completed_times) / len(completed_times) if completed_times else None,
        "completed_travel_time_total_s": sum(completed_times),
        "completed_departure_to_arrival_max_s": max(completed_times) if completed_times else None,
        "completed_scheduled_to_arrival_mean_s": sum(completed_full_times) / len(completed_full_times) if completed_full_times else None,
        "count_conservation": len(scheduled) == arrived + unfinished + not_departed,
        "final_summary_consistent": summary_ok, "final_sumo_summary": last_summary or {}, "per_route": route_totals,
        "metric_notes": ["In-network time includes vehicles still driving at the horizon.",
                         "System time also includes pre-insertion waiting, including vehicles not yet inserted.",
                         "Completed-only travel times must not be interpreted as all-demand performance."]}


def load_metrics(folder: Path, departures: list[dict], end_time: float) -> dict:
    trips = [dict(element.attrib) for _, element in ET.iterparse(folder / "tripinfo.xml") if element.tag == "tripinfo"]
    last_summary = None
    for _, element in ET.iterparse(folder / "summary.xml"):
        if element.tag == "step":
            last_summary = dict(element.attrib)
    metrics = compute_metrics(trips, departures, end_time, last_summary)
    collisions = sum(element.tag == "collision" for _, element in ET.iterparse(folder / "collisions.xml"))
    metrics.update(assess_physics(last_summary, collisions))
    return metrics


def assess_physics(last_summary: dict | None, collision_events: int) -> dict:
    """Gate physical validity separately from vehicle-count conservation.

    Teleported vehicles can still arrive and conserve counts. Missing counters
    also cannot establish that the run was free from collisions or removals.
    This gate says nothing about calibration against real traffic.
    """
    reasons, counters = [], {}
    for key in ("collisions", "teleports", "discarded"):
        raw = (last_summary or {}).get(key)
        try:
            value = float(raw)
            if not math.isfinite(value) or value < 0 or not value.is_integer():
                raise ValueError
            counters[key] = int(value)
            if value:
                reasons.append(f"{key}_nonzero")
        except (ValueError, TypeError):
            counters[key] = None
            reasons.append(f"{key}_counter_missing_or_invalid")
    if collision_events:
        reasons.append("collision_events_recorded")
    return {"physics_valid": not reasons, "quality_reasons": reasons,
            "physics_counters": {**counters, "collision_events": collision_events},
            "eligible_for_governance": False,
            "physics_note": "Zero detected collisions/teleports/discards is necessary, not real-world validation."}


def locate_binary(name: str) -> Path:
    candidates = [ROOT / ".venv" / "bin" / name]
    candidates.extend(sorted((ROOT / ".venv" / "lib").glob(f"python*/site-packages/sumo/bin/{name}")))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return workspace_path(candidate)
    raise FileNotFoundError(f"Missing workspace SUMO executable: {name}; expected .venv installation")


def isolated_env(folder: Path) -> dict[str, str]:
    env = os.environ.copy()
    runtime = folder / "runtime"
    runtime.mkdir()
    for key, child in (("TMPDIR", "tmp"), ("TMP", "tmp"), ("TEMP", "tmp"),
                       ("XDG_CACHE_HOME", "cache"), ("MPLCONFIGDIR", "matplotlib")):
        target = runtime / child
        target.mkdir(exist_ok=True)
        env[key] = str(target)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    package_homes = sorted((ROOT / ".venv" / "lib").glob("python*/site-packages/sumo"))
    home = next((path for path in package_homes if (path / "data" / "xsd").is_dir()), None)
    if home is None:
        raise FileNotFoundError("Workspace SUMO package data directory is missing")
    env["SUMO_HOME"] = str(workspace_path(home))
    return env


def invoke(command: list[str], folder: Path, env: dict, timeout: float, log_name: str) -> str:
    process = subprocess.run(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=timeout, check=False)
    (folder / log_name).write_text(process.stdout + process.stderr, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed with code {process.returncode}; see {folder / log_name}")
    return process.stdout.strip()


def audit_network(folder: Path, network: dict) -> dict:
    root = ET.parse(folder / "network.net.xml").getroot()
    actual_edges = {e.attrib["id"] for e in root.findall("edge") if e.attrib.get("function") != "internal"}
    expected_edges = {e["id"] for e in network["edges"]}
    connections = root.findall("connection")
    actual_connections = {(e.attrib["from"], e.attrib["to"]) for e in connections
                          if e.attrib["from"] in actual_edges}
    expected_connections = set(network["connections"])
    if actual_edges != expected_edges or actual_connections != expected_connections:
        raise ValueError("netconvert altered the observed-edge/connection set; refusing to simulate: "
                         f"extra_edges={sorted(actual_edges - expected_edges)}, "
                         f"missing_edges={sorted(expected_edges - actual_edges)}, "
                         f"extra_connections={sorted(actual_connections - expected_connections)}, "
                         f"missing_connections={sorted(expected_connections - actual_connections)}")
    # Internal links model the physical junction but may not add a movement.
    internal_edges = {e.attrib["id"] for e in root.findall("edge") if e.attrib.get("function") == "internal"}
    internal_lanes = {lane.attrib["id"]: edge.attrib["id"] for edge in root.findall("edge")
                      if edge.attrib["id"] in internal_edges for lane in edge.findall("lane")}
    reached = set()
    for connection in connections:
        if connection.attrib["from"] not in actual_edges:
            continue
        target, via = connection.attrib["to"], connection.get("via")
        seen = set()
        while via:
            if via not in internal_lanes or via in seen:
                raise ValueError("Invalid or cyclic internal junction connection")
            seen.add(via)
            candidates = [c for c in connections if c.get("from") == internal_lanes[via] and c.get("to") == target]
            if len(candidates) != 1:
                raise ValueError("Internal junction path does not preserve its observed movement")
            reached.add(id(candidates[0]))
            via = candidates[0].get("via")
    if any(id(c) not in reached for c in connections if c.get("from") in internal_edges):
        raise ValueError("Unaccounted internal junction movement")
    junction_types = {}
    for junction in root.findall("junction"):
        kind = junction.get("type")
        junction_types[kind] = junction_types.get(kind, 0) + 1
    return {"directed_edge_set_preserved": True, "observed_connection_set_preserved": True,
            "internal_movements_preserved": True, "internal_edge_count": len(internal_edges),
            "junction_types": junction_types,
            "node_count": len(network["nodes"]), "edge_count": len(actual_edges), "connection_count": len(actual_connections)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/baseline.json", help="Workspace baseline config")
    parser.add_argument("--input", help="Override accepted survey routes JSON")
    parser.add_argument("--check", action="store_true", help="Validate inputs and locate binaries without writing or running SUMO")
    parser.add_argument("--run-id", help="Optional unique output directory name")
    args = parser.parse_args(argv)
    folder = None
    manifest = None
    try:
        config_path = workspace_path(args.config)
        config = read_json(config_path)
        validate_config(config)
        input_path = workspace_path(args.input or config["input_routes"])
        dataset = read_json(input_path)
        network = prepare_network(dataset, config)
        departures = create_departures(network, config)
        sumo, netconvert = locate_binary("sumo"), locate_binary("netconvert")
        if args.check:
            print(json.dumps({"status": "inputs_valid", "accepted_routes": len(network["routes"]),
                              "nodes": len(network["nodes"]), "directed_edges": len(network["edges"]),
                              "planned_vehicles": len(departures), "sumo": str(sumo), "netconvert": str(netconvert),
                              "model_status": config["model_status"]}, ensure_ascii=False, indent=2))
            return 0
        run_id = args.run_id or datetime.now(timezone.utc).strftime("baseline_%Y%m%dT%H%M%S_%fZ")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", run_id):
            raise ValueError("Invalid run-id")
        folder = workspace_path(ROOT / "outputs" / "baseline" / run_id)
        folder.mkdir(parents=True, exist_ok=False)
        env = isolated_env(folder)
        script_snapshot = folder / "run_baseline.py"
        script_snapshot.write_bytes(Path(__file__).read_bytes())
        script_digest = sha256(script_snapshot)
        if script_digest != sha256(Path(__file__)):
            raise ValueError("Baseline source changed while archiving this run")
        manifest = {"run_id": run_id, "status": "running", "started_at": utc_now(),
                    "study_area_id": dataset["study_area_id"], "coordinate_system": dataset["coordinate_system"],
                    "input_routes": str(input_path.relative_to(ROOT)), "input_sha256": sha256(input_path),
                    "config_path": str(config_path.relative_to(ROOT)), "config_sha256": sha256(config_path),
                    "script_sha256": script_digest, "script_snapshot": script_snapshot.name,
                    "config": config, "network": {},
                    "assumptions": config["assumptions"], "excluded_routes": network["excluded_routes"],
                    "junction_model": {"node_type": "priority", "equal_edge_priority": 1,
                                       "internal_links": True, "status": "assumed_not_observed",
                                       "collision_action": "warn", "junction_collision_checks": True},
                    "selected_route_count": len(network["routes"]), "commands": []}
        write_json(folder / "run_manifest.json", manifest)
        versions = {}
        for name, binary in (("sumo", sumo), ("netconvert", netconvert)):
            versions[name] = invoke([str(binary), "--version"], folder, env, config["binary_timeout_s"], f"{name}.version.log")
        manifest["versions"] = versions
        write_inputs(folder, network, config, departures)
        build_command = [str(netconvert), "--node-files", "network.nod.xml", "--edge-files", "network.edg.xml",
                         "--connection-files", "network.con.xml", "--output-file", "network.net.xml",
                         "--no-turnarounds", "true", "--no-internal-links", "false", "--offset.disable-normalization", "true",
                         "--xml-validation", "never"]
        manifest["commands"].append(build_command)
        invoke(build_command, folder, env, config["binary_timeout_s"], "netconvert.log")
        manifest["network"] = audit_network(folder, network)
        command = [str(sumo), "-c", "baseline.sumocfg"]
        manifest["commands"].append(command)
        invoke(command, folder, env, config["binary_timeout_s"], "sumo.log")
        metrics = load_metrics(folder, departures, config["simulation_duration_s"])
        write_json(folder / "metrics.json", metrics)
        status = "completed" if metrics["physics_valid"] else "physics_invalid"
        manifest.update(status=status, finished_at=utc_now(), physics_valid=metrics["physics_valid"],
                        quality_reasons=metrics["quality_reasons"], eligible_for_governance=False,
                        validation_scope="Executable integration, observed-path constraints, collision/teleport gate and count conservation; not S1 real-world validation")
        manifest["artifacts"] = {file.name: sha256(file) for file in sorted(folder.iterdir())
                                 if file.is_file() and file.name != "run_manifest.json"}
        write_json(folder / "run_manifest.json", manifest)
        print(json.dumps({"status": status, "output": str(folder.relative_to(ROOT)),
                          "planned": metrics["planned"], "departed": metrics["departed"], "arrived": metrics["arrived"],
                          "unfinished_total": metrics["unfinished_total"], "model_status": config["model_status"]}, indent=2))
        return 0 if metrics["physics_valid"] else 2
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, subprocess.SubprocessError, RuntimeError) as error:
        if folder and manifest:
            manifest.update(status="failed", finished_at=utc_now(), error=str(error))
            write_json(folder / "run_manifest.json", manifest)
        print(f"Baseline error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
