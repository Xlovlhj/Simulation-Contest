#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Team review remains required.
"""Physical SUMO batch engine for low-dimensional inverse traffic modelling.

This engine returns simulated travel times, never an affine time correction.
Speed factors describe effective operating constraints, NOT observed speed limits.
Background OD demand and fixed-release measurement vehicles are distinguished.
No external API, source-admission control, artificial stop, or TraCI is used.

API: run_case(parameters: dict, folder: Path | str, protocol: dict | None) -> dict.
All paths and runtime/cache writes must remain in the current project workspace.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
try:
    from . import run_baseline as baseline
    from . import run_scenarios as scenarios
except ImportError:
    import run_baseline as baseline
    import run_scenarios as scenarios

ROOT = baseline.ROOT
DEFAULT_PROTOCOL = {
    "schema_version": 1,
    "route_catalog": "data/processed/baidu/route_catalog_v2.json",
    "base_physics": "config/baseline.json",
    "warmup_s": 600,
    "probe_offsets_s": [0, 60, 120],
    "background_end_s": 1200,
    "simulation_end_s": 3000,
    "edge_period_s": 60,
    "seed": 20261017,
    "maximum_probe_insertion_delay_s": 5,
    "network_cache": "outputs/identification_v2/network_cache",
    "edge_speed_groups": {},
    "demand_groups": {},
    "background_start_offsets_s": {},
    "probe_od_offsets_s": {},
    "binary_timeout_s": 180,
}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def number(value: object, name: str, minimum: float = 0.0, positive=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be a finite number")
    if not math.isfinite(value) or value < minimum or (positive and value <= 0):
        raise ValueError(name + " outside its finite allowed range")
    return float(value)


def normalize_protocol(protocol: dict | None) -> dict:
    result = copy.deepcopy(DEFAULT_PROTOCOL)
    if protocol is not None:
        if not isinstance(protocol, dict):
            raise ValueError("protocol must be a mapping")
        result.update(copy.deepcopy(protocol))
    if result["schema_version"] != 1:
        raise ValueError("Unsupported inverse-engine protocol")
    for key in ("warmup_s", "background_end_s", "simulation_end_s", "edge_period_s",
                "maximum_probe_insertion_delay_s", "binary_timeout_s"):
        number(result[key], key)
    if result["simulation_end_s"] <= result["background_end_s"]:
        raise ValueError("Simulation must continue after background departures end")
    if result["edge_period_s"] <= 0 or result["binary_timeout_s"] <= 0:
        raise ValueError("Output period and timeout must be positive")
    if not isinstance(result["probe_offsets_s"], list) or not result["probe_offsets_s"]:
        raise ValueError("At least one fixed probe release offset is required")
    offsets = [number(x, "probe offset") for x in result["probe_offsets_s"]]
    if offsets != sorted(set(offsets)):
        raise ValueError("Probe offsets must be strictly increasing and unique")
    releases = [result["warmup_s"] + x for x in offsets]
    if any(x >= result["background_end_s"] for x in releases):
        raise ValueError("Background departures must remain active for all probe releases")
    for value in [result[k] for k in ("warmup_s", "background_end_s", "simulation_end_s", "edge_period_s")] + releases:
        if not float(value).is_integer():
            raise ValueError("This engine uses fixed one-second steps; protocol times must be integers")
    if not isinstance(result["seed"], int) or isinstance(result["seed"], bool):
        raise ValueError("seed must be an integer")
    for key in ("edge_speed_groups", "demand_groups", "background_start_offsets_s", "probe_od_offsets_s"):
        if not isinstance(result[key], dict):
            raise ValueError(key + " must be a mapping")
    for key in ("route_catalog", "base_physics", "network_cache"):
        baseline.workspace_path(result[key])
    return result


def load_inputs(protocol: dict) -> tuple[dict, dict, dict, dict]:
    catalog_path = baseline.workspace_path(protocol["route_catalog"])
    physics_path = baseline.workspace_path(protocol["base_physics"])
    catalog = baseline.read_json(catalog_path)
    if catalog.get("catalog_version") == 2:
        mapping_path = baseline.workspace_path(catalog["normalization_mapping_file"])
        mapping = baseline.read_json(mapping_path)
        if mapping["normalized_catalog_sha256"] != baseline.sha256(catalog_path):
            raise ValueError("Normalized catalog differs from the audited mapping")
        if mapping["source_sha256"] != baseline.sha256(baseline.workspace_path(mapping["source_catalog"])):
            raise ValueError("Original geometry source differs from the audited mapping")
        if mapping["builder_sha256"] != baseline.sha256(ROOT / "scripts/normalize_network_v2.py"):
            raise ValueError("Geometry normalization implementation changed")
    network, demands, candidates = scenarios.prepare_catalog(catalog)
    physics = baseline.read_json(physics_path)
    baseline.validate_config(physics)
    return network, demands, candidates, physics


def expand_parameters(parameters: dict, protocol: dict, network: dict, demands: dict,
                      physics: dict) -> dict:
    allowed = {"speed_factor", "group_speed_factors", "default_headway_s", "group_headways_s", "od_headways_s"}
    if not isinstance(parameters, dict):
        raise ValueError("Inverse parameters must be a mapping")
    if set(parameters) - allowed:
        raise ValueError("Unknown inverse parameter: " + str(sorted(set(parameters) - allowed)))
    global_factor = number(parameters.get("speed_factor", 1), "speed_factor", positive=True)
    edge_ids = {e["id"] for e in network["edges"]}
    edge_factors = {edge: global_factor for edge in edge_ids}
    groups = protocol["edge_speed_groups"]
    group_factors = parameters.get("group_speed_factors", {})
    if not isinstance(group_factors, dict) or set(group_factors) - set(groups):
        raise ValueError("Unknown speed-factor group")
    seen = set()
    for group, edges in groups.items():
        if not isinstance(edges, list) or not edges or len(edges) != len(set(edges)):
            raise ValueError("Speed groups must contain distinct known edge ids")
        if set(edges) - edge_ids or seen.intersection(edges):
            raise ValueError("Speed groups overlap or contain unknown edges")
        seen.update(edges)
        factor = number(group_factors.get(group, 1), "group speed factor", positive=True)
        for edge in edges:
            edge_factors[edge] *= factor
    # The unchanged vehicle maxSpeed remains the upper bound of this family.
    # Values >1 would be partly clipped and confound the intended interpretation.
    if any(factor > 1 for factor in edge_factors.values()):
        raise ValueError("Effective speed factors must be <=1 relative to the fixed vehicle maximum")

    def headway(value):
        return None if value is None else number(value, "headway_s", minimum=1)

    default = headway(parameters.get("default_headway_s", 60))
    headways = dict.fromkeys(demands, default)
    group_values = parameters.get("group_headways_s", {})
    if not isinstance(group_values, dict) or set(group_values) - set(protocol["demand_groups"]):
        raise ValueError("Unknown demand group")
    grouped = set()
    for group, ods in protocol["demand_groups"].items():
        if not isinstance(ods, list) or not ods or len(ods) != len(set(ods)):
            raise ValueError("Demand groups must contain distinct known OD ids")
        if set(ods) - set(demands) or grouped.intersection(ods):
            raise ValueError("Demand groups overlap or contain unknown ODs")
        grouped.update(ods)
        if group in group_values:
            for od in ods:
                headways[od] = headway(group_values[group])
    od_values = parameters.get("od_headways_s", {})
    if not isinstance(od_values, dict) or set(od_values) - set(demands):
        raise ValueError("Unknown OD headway")
    headways.update({od: headway(value) for od, value in od_values.items()})
    if set(protocol["background_start_offsets_s"]) - set(demands):
        raise ValueError("Unknown background offset OD")
    offsets = {od: number(protocol["background_start_offsets_s"].get(od, 0), "background offset")
               for od in demands}
    if set(protocol["probe_od_offsets_s"]) - set(demands):
        raise ValueError("Unknown probe offset OD")
    for od, offset in protocol["probe_od_offsets_s"].items():
        number(offset, "probe OD offset")
        if not float(offset).is_integer():
            raise ValueError("Probe OD offset must align with one-second steps")
        if protocol["warmup_s"] + max(protocol["probe_offsets_s"]) + offset >= protocol["background_end_s"]:
            raise ValueError("Offset probe release must precede background departure end")
    base_speed = physics["speed_limit_kmh"] / 3.6
    return {"edge_speed_factors": dict(sorted(edge_factors.items())),
            "edge_effective_speed_m_s": {edge: base_speed * value for edge, value in sorted(edge_factors.items())},
            "od_headways_s": dict(sorted(headways.items())), "background_start_offsets_s": offsets,
            "parameter_interpretation": "Effective constraints and assumed demand; not observed posted speed limits or measured flow."}


def create_assignments(demands: dict, expanded: dict, protocol: dict) -> list[dict]:
    assignments = []
    for od in sorted(demands):
        route = demands[od]["default_route_id"]
        headway = expanded["od_headways_s"][od]
        offset = expanded["background_start_offsets_s"][od]
        if headway is not None:
            count = max(0, math.ceil((protocol["background_end_s"] - offset) / headway))
            for index in range(count):
                release = math.ceil(offset + index * headway - 1e-9)
                if release < protocol["background_end_s"]:
                    assignments.append({"id": f"background.{od}.{index:05d}", "od_id": od,
                                        "role": "background", "route": route, "depart": release,
                                        "release_s": release})
        for index, offset in enumerate(protocol["probe_offsets_s"]):
            release = protocol["warmup_s"] + offset + protocol["probe_od_offsets_s"].get(od, 0)
            assignments.append({"id": f"probe.{od}.{index:03d}", "od_id": od,
                                "role": "probe", "route": route, "depart": release,
                                "release_s": release})
    return sorted(assignments, key=lambda item: (item["depart"], item["id"]))


def write_case_inputs(folder: Path, network: dict, physics: dict, expanded: dict,
                      assignments: list[dict], protocol: dict) -> None:
    runtime_physics = {**physics, "simulation_duration_s": protocol["simulation_end_s"],
                       "step_length_s": 1, "seed": protocol["seed"]}
    baseline.write_inputs(folder, network, runtime_physics, assignments)
    edges = ET.parse(folder / "network.edg.xml")
    for edge in edges.getroot().findall("edge"):
        edge.set("speed", format(expanded["edge_effective_speed_m_s"][edge.get("id")], ".12g"))
    baseline.write_xml(folder / "network.edg.xml", edges.getroot())
    route_tree = ET.parse(folder / "demand.rou.xml")
    roles = {a["id"]: a["role"] for a in assignments}
    for vehicle in route_tree.getroot().findall("vehicle"):
        ET.SubElement(vehicle, "param", key="measurement_role", value=roles[vehicle.get("id")])
    baseline.write_xml(folder / "demand.rou.xml", route_tree.getroot())
    additional = ET.Element("additional")
    ET.SubElement(additional, "edgeData", id="edge_states", file="edge_states.xml",
                  period=str(protocol["edge_period_s"]), withInternal="false",
                  excludeEmpty="true", writeAttributes="speed traveltime sampledSeconds density occupancy entered left arrived departed waitingTime timeLoss")
    baseline.write_xml(folder / "inverse.add.xml", additional)
    config = ET.parse(folder / "baseline.sumocfg")
    ET.SubElement(config.getroot().find("input"), "additional-files", value="inverse.add.xml")
    # Dense FCD is unnecessary for parameter search. Edge aggregates, complete
    # trip outcomes, summary, and collisions retain the relevant evidence.
    output = config.getroot().find("output")
    output.remove(output.find("fcd-output"))
    for key, value in (("vehroute-output", "vehicle_routes.xml"),
                       ("vehroute-output.exit-times", "true"),
                       ("vehroute-output.internal", "true"),
                       ("vehroute-output.write-unfinished", "true")):
        ET.SubElement(output, key, value=value)
    config.getroot().remove(config.getroot().find("fcd_device"))
    baseline.write_xml(folder / "inverse.sumocfg", config.getroot())
    (folder / "baseline.sumocfg").unlink()


def source_hashes(protocol: dict) -> dict:
    paths = [Path(__file__), Path(baseline.__file__), Path(scenarios.__file__),
             baseline.workspace_path(protocol["route_catalog"]), baseline.workspace_path(protocol["base_physics"])]
    catalog = baseline.read_json(baseline.workspace_path(protocol["route_catalog"]))
    if catalog.get("catalog_version") == 2:
        mapping_path = baseline.workspace_path(catalog["normalization_mapping_file"])
        paths.extend([mapping_path, ROOT / "scripts/normalize_network_v2.py"])
        paths.append(baseline.workspace_path(baseline.read_json(mapping_path)["source_catalog"]))
    return {str(path.resolve().relative_to(ROOT)): baseline.sha256(path) for path in paths}


def prepare_network_cache(folder: Path, network: dict, protocol: dict, source: dict,
                          netconvert: Path, env: dict) -> dict:
    inputs = {name: baseline.sha256(folder / name) for name in
              ("network.nod.xml", "network.edg.xml", "network.con.xml")}
    key = digest({"inputs": inputs, "netconvert_sha256": baseline.sha256(netconvert), "source": source})[:24]
    cache = baseline.workspace_path(protocol["network_cache"]) / key
    cache.parent.mkdir(parents=True, exist_ok=True)
    cached = cache / "network.net.xml"
    cache_manifest = cache / "cache_manifest.json"
    hit = cached.is_file() and cache_manifest.is_file()
    if hit:
        record = baseline.read_json(cache_manifest)
        if record["inputs"] != inputs or record["network_sha256"] != baseline.sha256(cached):
            raise ValueError("Cached network integrity failed")
        shutil.copyfile(cached, folder / "network.net.xml")
        (folder / "netconvert.log").write_text("Verified cached network: " + str(cache.relative_to(ROOT)) + "\n")
    else:
        command = [str(netconvert), "--node-files", "network.nod.xml", "--edge-files", "network.edg.xml",
                   "--connection-files", "network.con.xml", "--output-file", "network.net.xml",
                   "--no-turnarounds", "true", "--no-internal-links", "false", "--offset.disable-normalization", "true",
                   "--xml-validation", "never"]
        baseline.invoke(command, folder, env, protocol["binary_timeout_s"], "netconvert.log")
        baseline.audit_network(folder, network)
        cache.mkdir(exist_ok=False)
        shutil.copyfile(folder / "network.net.xml", cached)
        baseline.write_json(cache_manifest, {"inputs": inputs, "source_hashes": source,
                                            "network_sha256": baseline.sha256(cached), "command": command})
    return {"cache_hit": hit, "cache_folder": str(cache.relative_to(ROOT)),
            "network_sha256": baseline.sha256(folder / "network.net.xml"),
            "topology": baseline.audit_network(folder, network)}


def trip_records(trips: list[dict], assignments: list[dict], horizon: float) -> list[dict]:
    expected = {a["id"] for a in assignments}
    by_id = {}
    for trip in trips:
        if trip["id"] not in expected or trip["id"] in by_id:
            raise ValueError("Unexpected or duplicate SUMO trip")
        by_id[trip["id"]] = trip
    result = []
    for item in assignments:
        trip = by_id.get(item["id"], {})
        depart = float(trip.get("depart", -1))
        arrival = float(trip.get("arrival", -1))
        if arrival >= 0 and not (0 <= depart <= arrival <= horizon + 1e-6):
            raise ValueError("Invalid trip times")
        if depart >= 0 and not item["release_s"] <= depart <= horizon:
            raise ValueError("Vehicle entered before its original release or after horizon")
        completed = arrival >= 0
        in_network = ((arrival if completed else horizon) - depart) if depart >= 0 else 0
        insertion = (depart if depart >= 0 else horizon) - item["release_s"]
        result.append({**item, "depart_s": depart if depart >= 0 else None,
                       "arrival_s": arrival if completed else None, "completed": completed,
                       "censored_at_horizon": not completed,
                       "insertion_delay_s": insertion,
                       "in_network_time_s": in_network if completed else None,
                       "observed_in_network_time_s": in_network,
                       "release_to_arrival_s": arrival - item["release_s"] if completed else None,
                       "observed_release_to_end_s": (arrival if completed else horizon) - item["release_s"],
                       "observed_delay_is_lower_bound": not completed,
                       "route_length_m": float(trip["routeLength"]) if "routeLength" in trip else None,
                       "waiting_time_s": float(trip["waitingTime"]) if "waitingTime" in trip else None,
                       "time_loss_s": float(trip["timeLoss"]) if "timeLoss" in trip else None})
    return result


def summarize_records(records: list[dict], maximum_insertion_delay: float) -> dict:
    completed = [r for r in records if r["completed"]]
    count = len(records)
    arrived = len(completed)
    mean = lambda key: sum(r[key] for r in completed) / arrived if arrived else None
    delays = [r["insertion_delay_s"] for r in records]
    fully_completed = bool(count) and arrived == count
    insertion_acceptable = fully_completed and max(delays) <= maximum_insertion_delay
    return {"planned": count, "arrived": arrived, "unfinished": count - arrived,
            "not_departed": sum(r["depart_s"] is None for r in records),
            "completion_rate": arrived / count if count else None,
            "in_network_mean_s": mean("in_network_time_s"),
            "release_to_arrival_mean_s": mean("release_to_arrival_s"),
            "completed_only_means_are_censored": not fully_completed,
            "insertion_delay_mean_s": sum(delays) / count if count else None,
            "insertion_delay_max_s": max(delays) if delays else None,
            "observed_in_network_total_s": sum(r["observed_in_network_time_s"] for r in records),
            "observed_system_total_s": sum(r["observed_release_to_end_s"] for r in records),
            "total_is_lower_bound_if_unfinished": not fully_completed,
            "all_complete": fully_completed,
            "insertion_acceptable": insertion_acceptable,
            "eta_usable": insertion_acceptable,
            "comparable_eta_s": mean("in_network_time_s") if insertion_acceptable else None,
            "source_control_wait_s": 0}


def summarize_edges(path: Path, probe_window_start: float, background_end: float) -> dict:
    totals = {}
    intervals = 0
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "interval":
            continue
        begin, end = float(element.get("begin")), float(element.get("end"))
        if begin >= probe_window_start and end <= background_end:
            intervals += 1
            for edge in element.findall("edge"):
                samples = float(edge.get("sampledSeconds", 0))
                if samples <= 0:
                    continue
                record = totals.setdefault(edge.get("id"), {"sampled_vehicle_seconds": 0., "distance_estimate_m": 0.,
                                                           "entered": 0, "left": 0, "waiting_vehicle_seconds": 0.})
                record["sampled_vehicle_seconds"] += samples
                record["distance_estimate_m"] += samples * float(edge.get("speed", 0))
                record["entered"] += int(edge.get("entered", 0))
                record["left"] += int(edge.get("left", 0))
                record["waiting_vehicle_seconds"] += float(edge.get("waitingTime", 0))
        element.clear()
    for record in totals.values():
        record["space_mean_speed_m_s"] = record["distance_estimate_m"] / record["sampled_vehicle_seconds"]
    return {"aggregation": "distance / sampled vehicle seconds over complete aligned edgeData intervals",
            "requested_window_start_s": probe_window_start, "requested_window_end_s": background_end,
            "included_intervals": intervals, "edges": totals}


def probe_edge_times(path: Path, assignments: list[dict], protocol: dict) -> list[dict]:
    """Retain direct SUMO external-edge times; internal junction times stay separate.

    SUMO writes all traversed edges including junction internal edges. Subtracting
    consecutive exit times therefore does not silently charge junction delays to
    a different road. No negative-time unfinished suffix is treated as observed.
    """
    probes = {a["id"]: a for a in assignments if a["role"] == "probe"}
    edge_groups = {edge: group for group, edges in protocol["edge_speed_groups"].items() for edge in edges}
    results = []
    for _, vehicle in ET.iterparse(path, events=("end",)):
        if vehicle.tag != "vehicle":
            continue
        if vehicle.get("id") not in probes:
            vehicle.clear()
            continue
        item = probes[vehicle.get("id")]
        route = vehicle.find("route")
        if route is None:
            raise ValueError("Probe route output missing route element")
        edges = route.get("edges", "").split()
        exits = [float(x) for x in route.get("exitTimes", "").split()]
        if len(edges) != len(exits):
            raise ValueError("Probe edge exit count differs from route length")
        previous = float(vehicle.get("depart", -1))
        traversals, internal_total, group_totals = [], 0.0, {}
        for edge, exit_time in zip(edges, exits):
            if exit_time < 0:
                break
            if previous < 0 or exit_time < previous:
                raise ValueError("Invalid probe edge exit times")
            duration = exit_time - previous
            if edge.startswith(":"):
                internal_total += duration
            else:
                group = edge_groups.get(edge, "ungrouped")
                group_totals[group] = group_totals.get(group, 0.0) + duration
                traversals.append({"edge_id": edge, "entry_s": previous, "exit_s": exit_time,
                                   "time_s": duration, "speed_group": group})
            previous = exit_time
        results.append({"id": item["id"], "od_id": item["od_id"],
                        "depart_s": float(vehicle.get("depart", -1)),
                        "arrival_s": float(vehicle.get("arrival", -1)),
                        "external_edges": traversals,
                        "external_group_time_s": group_totals,
                        "internal_junction_time_s": internal_total,
                        "exit_time_resolution_s": 1,
                        "note": "External durations exclude internal junction traversal; unfinished suffix is censored."})
        vehicle.clear()
    return results


def run_case(parameters: dict, folder: Path | str, protocol: dict | None = None) -> dict:
    """Run a new auditable physical case; refuses to overwrite an existing folder.

    per_od[od].probe.comparable_eta_s is the direct SUMO in-network mean,
    available only if every probe completes and insertion delays are small.
    All censored and source-waiting vehicles remain in full_records/metrics.
    """
    protocol = normalize_protocol(protocol)
    folder = baseline.workspace_path(folder)
    network, demands, candidates, physics = load_inputs(protocol)
    expanded = expand_parameters(parameters, protocol, network, demands, physics)
    assignments = create_assignments(demands, expanded, protocol)
    source = source_hashes(protocol)
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "status": "running", "started_at": baseline.utc_now(),
                "source_hashes": source, "parameters": parameters, "protocol": protocol,
                "expanded_parameters": expanded, "physics_parameters": physics,
                "case_hash": digest({"parameters": parameters, "protocol": protocol, "source": source}),
                "interpretation": "Physical inverse-model candidate, not validated observed traffic truth.",
                "output_time_correction": None, "source_control": None, "artificial_stops": None}
    baseline.write_json(folder / "run_manifest.json", manifest)
    try:
        baseline.write_json(folder / "parameters.json", parameters)
        baseline.write_json(folder / "protocol.json", protocol)
        baseline.write_json(folder / "expanded_parameters.json", expanded)
        baseline.write_json(folder / "assignments.json", assignments)
        for source_path in (Path(__file__), Path(baseline.__file__), Path(scenarios.__file__)):
            shutil.copyfile(source_path, folder / source_path.name)
        env = baseline.isolated_env(folder)
        sumo, netconvert = baseline.locate_binary("sumo"), baseline.locate_binary("netconvert")
        manifest["sumo_version"] = baseline.invoke([str(sumo), "--version"], folder, env,
                                                   protocol["binary_timeout_s"], "sumo.version.log")
        write_case_inputs(folder, network, physics, expanded, assignments, protocol)
        manifest["network"] = prepare_network_cache(folder, network, protocol, source, netconvert, env)
        command = [str(sumo), "-c", "inverse.sumocfg"]
        manifest["sumo_command"] = command
        baseline.invoke(command, folder, env, protocol["binary_timeout_s"], "sumo.log")
        trips = [dict(e.attrib) for _, e in ET.iterparse(folder / "tripinfo.xml") if e.tag == "tripinfo"]
        records = trip_records(trips, assignments, protocol["simulation_end_s"])
        original_metrics = baseline.load_metrics(folder, assignments, protocol["simulation_end_s"])
        max_delay = protocol["maximum_probe_insertion_delay_s"]
        per_od = {}
        for od in sorted(demands):
            per_od[od] = {role: summarize_records([r for r in records if r["od_id"] == od and r["role"] == role], max_delay)
                          for role in ("probe", "background")}
        probe = summarize_records([r for r in records if r["role"] == "probe"], max_delay)
        background = summarize_records([r for r in records if r["role"] == "background"], max_delay)
        metrics = {"schema_version": 1, "status": "completed" if original_metrics["physics_valid"] else "physics_invalid",
                   "run_dir": str(folder.relative_to(ROOT)), "parameters": parameters,
                   "per_od": per_od, "probe": probe, "background": background,
                   "physics_valid": original_metrics["physics_valid"], "physics_counters": original_metrics["physics_counters"],
                   "count_conservation": original_metrics["count_conservation"],
                   "eta_usable": original_metrics["physics_valid"] and probe["eta_usable"],
                   "all_vehicles_arrived": original_metrics["unfinished_total"] == 0,
                   "planned": original_metrics["planned"], "arrived": original_metrics["arrived"],
                   "unfinished_total": original_metrics["unfinished_total"],
                   "not_departed": original_metrics["not_departed"],
                   "throughput_vehicles_per_simulated_hour": original_metrics["arrived"] * 3600 / protocol["simulation_end_s"],
                   "demand_vph_by_od": {od: 3600 / h if h is not None else 0
                                        for od, h in expanded["od_headways_s"].items()},
                   "probe_release_times_s": [protocol["warmup_s"] + x for x in protocol["probe_offsets_s"]],
                   "probe_od_offsets_s": protocol["probe_od_offsets_s"],
                   "comparison_target": "Platform ETA is an estimate for a new traveller, not observed individual ground truth.",
                   "notes": ["Direct simulated in-network time; no affine output adjustment.",
                             "Insertion delay is reported and never removed from total system cost.",
                             "Unfinished vehicles retain observed lower bounds and invalidate complete-probe ETA.",
                             "Effective speed factors subsume unresolved operations; they are not measured signal timing or posted limits."]}
        edges = summarize_edges(folder / "edge_states.xml", protocol["warmup_s"], protocol["background_end_s"])
        probe_traversals = probe_edge_times(folder / "vehicle_routes.xml", assignments, protocol)
        outcome_by_id = {item["id"]: item for item in records}
        for item in probe_traversals:
            outcome = outcome_by_id[item["id"]]
            if outcome["completed"]:
                total = sum(edge["time_s"] for edge in item["external_edges"]) + item["internal_junction_time_s"]
                if not math.isclose(total, outcome["in_network_time_s"], abs_tol=1e-6):
                    raise ValueError("Probe edge times do not conserve complete in-network time")
        complete_probes = {item["id"] for item in records if item["role"] == "probe" and item["completed"]}
        if not complete_probes.issubset({item["id"] for item in probe_traversals}):
            raise ValueError("Completed probe is missing edge traversal output")
        baseline.write_json(folder / "probe_edge_times.json", probe_traversals)
        baseline.write_json(folder / "vehicle_outcomes.json", records)
        baseline.write_json(folder / "edge_summary.json", edges)
        baseline.write_json(folder / "full_metrics.json", original_metrics)
        baseline.write_json(folder / "metrics.json", metrics)
        if source_hashes(protocol) != source:
            raise ValueError("Source or catalogue changed during simulation")
        manifest["status"] = metrics["status"]
        manifest["finished_at"] = baseline.utc_now()
        manifest["artifacts"] = {str(path.relative_to(folder)): baseline.sha256(path)
                                 for path in sorted(folder.iterdir()) if path.is_file() and path.name != "run_manifest.json"}
        baseline.write_json(folder / "run_manifest.json", manifest)
        return metrics
    except Exception as exc:
        manifest.update(status="failed", finished_at=baseline.utc_now(), error_type=type(exc).__name__, error=str(exc))
        baseline.write_json(folder / "run_manifest.json", manifest)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", required=True, help="Public JSON with physical candidate parameters")
    parser.add_argument("--protocol", help="Public JSON protocol; defaults are engineering controls, not observations")
    parser.add_argument("--output", required=True, help="New case directory inside workspace")
    args = parser.parse_args(argv)
    parameters = baseline.read_json(baseline.workspace_path(args.parameters))
    protocol = baseline.read_json(baseline.workspace_path(args.protocol)) if args.protocol else None
    result = run_case(parameters, args.output, protocol)
    print(json.dumps({"status": result["status"], "run_dir": result["run_dir"], "eta_usable": result["eta_usable"],
                      "probe": result["probe"], "per_od": result["per_od"]}, ensure_ascii=False, indent=2))
    return 0 if result["physics_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
