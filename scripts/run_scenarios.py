#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date
# was not supplied. The team must review the implementation and interpretation.
"""Run offline engineering scenarios with fixed OD demand, not calibrated S2/S3.

No credentials, network requests, new dependencies or global environment changes.
Default invocation shows help. Explicit --run writes a new reproducible batch.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
try:
    from . import run_baseline as baseline
except ImportError:
    import run_baseline as baseline

ROOT = baseline.ROOT
CASES = (("S2_fixed", False, False), ("S2_guided", False, True),
         ("S3_fixed", True, False), ("S3_independent_guided", True, True))


def object_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def stable_json(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def verify_inputs(hashes: dict) -> None:
    for relative, expected in hashes.items():
        if baseline.sha256(baseline.workspace_path(relative)) != expected:
            raise ValueError("Batch input or source changed: " + relative)


def finite_number(value, name: str, minimum=0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return float(value)


def prepare_catalog(catalog: dict) -> tuple[dict, dict, dict]:
    """Adapt audited topology, while keeping six OD demands separate from routes."""
    if catalog.get("schema_version") != 1 or catalog.get("coordinate_system") != "bd09ll":
        raise ValueError("Unsupported route catalog or coordinate system")
    demands = {d["id"]: d for d in catalog["demand_ods"]}
    if len(demands) != 6 or len(catalog["demand_ods"]) != 6:
        raise ValueError("This engineering protocol requires the six original demand ODs")
    nodes = [{**n, "xy": n["xy_m"]} for n in catalog["network"]["nodes"]]
    edges = catalog["network"]["edges"]
    edge_by_id = {e["id"]: e for e in edges}
    if len(edge_by_id) != len(edges):
        raise ValueError("Duplicate catalog edge")
    connections = sorted((c["from"], c["to"]) for c in catalog["network"]["connections"]
                         if c.get("eligible_for_policy_path") is True and not c.get("explicit_uturn"))
    allowed = set(connections)
    candidates, routes = {}, []
    for candidate in catalog["route_candidates"]:
        identity, od = candidate["id"], candidate["demand_od_id"]
        if identity in candidates or od not in demands or candidate.get("adds_demand") is not False:
            raise ValueError("Invalid candidate identity or demand relationship")
        chain = candidate["edge_ids"]
        if not chain or any(e not in edge_by_id for e in chain) or len(chain) != len(set(chain)):
            raise ValueError("Candidate must be a nonempty edge-simple known path")
        if any(pair not in allowed for pair in zip(chain, chain[1:])):
            raise ValueError("Candidate includes an unverified or excluded connection")
        node_chain = [edge_by_id[chain[0]]["from"]] + [edge_by_id[e]["to"] for e in chain]
        if len(node_chain) != len(set(node_chain)):
            raise ValueError("Candidate contains a repeated node")
        if any(edge_by_id[a]["to"] != edge_by_id[b]["from"] for a, b in zip(chain, chain[1:])):
            raise ValueError("Candidate geometry is disconnected")
        length = sum(edge_by_id[e]["length_m"] for e in chain)
        candidates[identity] = {**candidate, "model_length_m": length}
        routes.append({"id": identity, "source_id": identity, "od_id": od,
                       "edge_ids": chain, "projected_length_m": length,
                       "origin_id": demands[od]["origin_id"], "destination_id": demands[od]["destination_id"]})
    for od, demand in demands.items():
        matches = [c["id"] for c in candidates.values()
                   if c["demand_od_id"] == od and c["edge_ids"] == demand["baseline_edge_ids"]]
        if len(matches) != 1:
            raise ValueError("Each OD must have one unambiguous original default candidate")
        demands[od] = {**demand, "default_route_id": matches[0]}
    network = {"bounds": catalog["model_bounds"], "center": catalog["projection_center"],
               "nodes": nodes, "edges": edges, "connections": connections,
               "routes": routes, "excluded_routes": []}
    return network, demands, candidates


def avoiding_candidates(od: str, demands: dict, candidates: dict, event: dict) -> list[dict]:
    default = demands[od]["baseline_edge_ids"]
    forbidden = set(event["edge_ids"])
    eligible = [c for c in candidates.values() if c["demand_od_id"] == od
                and (c["edge_ids"][0], c["edge_ids"][-1]) == (default[0], default[-1])
                and not forbidden.intersection(c["edge_ids"])]
    return sorted(eligible, key=lambda c: (c["model_length_m"], c["id"]))


def validate_protocol(config: dict, physics: dict, network: dict, demands: dict, candidates: dict) -> None:
    if config.get("purpose") != "uncalibrated_engineering_scenarios":
        raise ValueError("Protocol must explicitly identify uncalibrated engineering scenarios")
    if config.get("policy") != "minimum_current_known_route_cost_before_departure" or config.get("coordination_B_status") != "not_implemented":
        raise ValueError("Only the stated independent guidance policy is implemented; B is not implemented")
    if config.get("time_basis") != "simulation_seconds_only_no_real_world_clock_mapping":
        raise ValueError("This runner currently supports simulation seconds only")
    event, task = config["event"], config["hospital_task"]
    if event.get("source") != "assumed_engineering_event_not_an_observed_incident":
        raise ValueError("Slow event must be explicitly marked as an assumption")
    target = event["target_od_id"]
    if target not in demands or task["od_id"] != target or demands[target]["destination_id"] != "RJ_YJ":
        raise ValueError("Task and event must reference the selected hospital-road OD")
    for key in ("published_at_s", "begin_s", "end_s"):
        finite_number(event[key], key)
    if not event["begin_s"] < event["end_s"] <= physics["simulation_duration_s"]:
        raise ValueError("Event interval is outside the common simulation horizon")
    speed = finite_number(event["speed_m_s"], "speed_m_s", minimum=0.001)
    if speed >= physics["speed_limit_kmh"] / 3.6:
        raise ValueError("Slow-event speed must be below the unchanged base limit")
    release = finite_number(task["release_s"], "task release")
    if release >= physics["simulation_duration_s"]:
        raise ValueError("Task must be released before the simulation horizon")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", task["id"]):
        raise ValueError("Invalid task id")
    step = physics["step_length_s"]
    for value in [event[k] for k in ("published_at_s", "begin_s", "end_s")] + [release]:
        if not math.isclose(value / step, round(value / step), abs_tol=1e-8):
            raise ValueError("Event/task times must align with simulation steps")
    sequence = event["edge_ids"]
    default = demands[target]["baseline_edge_ids"]
    if not sequence or len(set(sequence)) != len(sequence) or not any(
            default[i:i + len(sequence)] == sequence for i in range(len(default))):
        raise ValueError("Event edges must form a contiguous segment of the default hospital path")
    edge_by_id = {e["id"]: e for e in network["edges"]}
    if any(event["road_name"] not in edge_by_id[e].get("road_names", []) for e in sequence):
        raise ValueError("Event segment road name lacks catalog evidence")
    if not avoiding_candidates(target, demands, candidates, event):
        raise ValueError("No same-endpoint verified candidate avoids the entire slow segment")


def fixed_demand(demands: dict, physics: dict, task: dict, include_task: bool) -> list[dict]:
    """This function never receives the candidate list: more paths cannot add cars."""
    result = []
    for index in range(math.ceil(physics["departure_duration_s"] / physics["departure_interval_s"])):
        release = index * physics["departure_interval_s"]
        if release >= physics["departure_duration_s"]:
            break
        for od in sorted(demands):
            result.append({"id": f"ordinary.{od}.{index:05d}", "od_id": od,
                           "release_s": release, "role": "ordinary", "vehicle_type": "assumed_car"})
    if include_task:
        result.append({"id": task["id"], "od_id": task["od_id"], "release_s": task["release_s"],
                       "role": "hospital_task", "vehicle_type": "assumed_car"})
    return sorted(result, key=lambda row: (row["release_s"], row["id"]))


def assign_routes(demand: list[dict], demands: dict, candidates: dict, event: dict, guided: bool,
                  edges: list[dict], base_speed_m_s: float) -> list[dict]:
    edge_lengths = {e["id"]: e["length_m"] for e in edges}
    event_edges = set(event["edge_ids"])
    result = []
    for item in demand:
        release, od = item["release_s"], item["od_id"]
        route_id = demands[od]["default_route_id"]
        aware_and_active = event["published_at_s"] <= release and event["begin_s"] <= release < event["end_s"]
        reason = "fixed_default"
        estimates = {}
        if guided and od == event["target_od_id"] and aware_and_active:
            default = demands[od]["baseline_edge_ids"]
            for c in candidates.values():
                if c["demand_od_id"] != od or (c["edge_ids"][0], c["edge_ids"][-1]) != (default[0], default[-1]):
                    continue
                estimates[c["id"]] = sum(edge_lengths[e] / (event["speed_m_s"] if e in event_edges else base_speed_m_s)
                                          for e in c["edge_ids"])
            best_cost = estimates[route_id]
            for identity in sorted(estimates):
                if estimates[identity] < best_cost - 1e-9:
                    route_id, best_cost = identity, estimates[identity]
            reason = "minimum_current_known_cost" if route_id != demands[od]["default_route_id"] else "default_retained_by_current_cost"
        result.append({**item, "route": route_id, "depart": release, "decision_s": release, "reason": reason,
                       "candidate_estimated_cost_s": estimates,
                       "cost_basis": "catalog_external_length/current_known_speed; no future end or queue/junction forecast"})
    return result


def write_scenario_inputs(folder: Path, network: dict, physics: dict, assignments: list[dict], event: dict) -> None:
    baseline.write_inputs(folder, network, physics, assignments)
    additional = ET.Element("additional")
    sign = ET.SubElement(additional, "variableSpeedSign", id=event["id"],
                         lanes=" ".join(e + "_0" for e in event["edge_ids"]))
    speed = str(physics["speed_limit_kmh"] / 3.6)
    if event["begin_s"] > 0:
        ET.SubElement(sign, "step", time="0", speed=speed)
    ET.SubElement(sign, "step", time=str(event["begin_s"]), speed=str(event["speed_m_s"]))
    ET.SubElement(sign, "step", time=str(event["end_s"]), speed=speed)
    ET.SubElement(additional, "edgeData", id="edge_states", file="edge_states.xml", period="30", withInternal="true")
    baseline.write_xml(folder / "scenario.add.xml", additional)
    tree = ET.parse(folder / "baseline.sumocfg")
    ET.SubElement(tree.getroot().find("input"), "additional-files", value="scenario.add.xml")
    baseline.write_xml(folder / "baseline.sumocfg", tree.getroot())


def task_metrics(trips: list[dict], assignments: list[dict], horizon: float) -> list[dict]:
    by_id = {row["id"]: row for row in trips}
    result = []
    for task in assignments:
        if task["role"] != "hospital_task":
            continue
        trip = by_id.get(task["id"], {})
        depart, arrive = float(trip.get("depart", -1)), float(trip.get("arrival", -1))
        result.append({"id": task["id"], "release_s": task["release_s"], "route_id": task["route"],
                       "depart_s": depart if depart >= 0 else None, "arrival_s": arrive if arrive >= 0 else None,
                       "completed": arrive >= 0,
                       "release_to_arrival_s": arrive - task["release_s"] if arrive >= 0 else None,
                       "observed_release_to_end_s": (arrive if arrive >= 0 else horizon) - task["release_s"],
                       "insertion_wait_s": (depart if depart >= 0 else horizon) - task["release_s"],
                       "in_network_time_s": (arrive if arrive >= 0 else horizon) - depart if depart >= 0 else 0,
                       "censored_at_horizon": arrive < 0})
    return result


def run_case(folder: Path, name: str, network: dict, demands: dict, candidates: dict,
             config: dict, physics: dict, include_task: bool, guided: bool, input_hashes: dict) -> dict:
    folder.mkdir(exist_ok=False)
    demand = fixed_demand(demands, physics, config["hospital_task"], include_task)
    assignments = assign_routes(demand, demands, candidates, config["event"], guided,
                                network["edges"], physics["speed_limit_kmh"] / 3.6)
    manifest = {"run_id": name, "status": "running", "started_at": baseline.utc_now(),
                "purpose": config["purpose"], "model_status": physics["model_status"],
                "time_basis": config["time_basis"], "coordination_B_status": "not_implemented",
                "eligible_for_governance": False, "real_world_calibrated": False,
                "study_area_id": physics["study_area_id"], "coordinate_system": "bd09ll",
                "config": physics, "protocol": config, "assumptions": config["assumptions"],
                "inputs_sha256": input_hashes, "demand_sha256": object_digest(demand),
                "background_demand_sha256": object_digest([d for d in demand if d["role"] == "ordinary"]),
                "event_sha256": object_digest(config["event"]), "commands": []}
    baseline.write_json(folder / "run_manifest.json", manifest)
    try:
        for source in (Path(__file__), Path(baseline.__file__)):
            snapshot = folder / source.name
            snapshot.write_bytes(source.read_bytes())
            if baseline.sha256(snapshot) != baseline.sha256(source):
                raise ValueError("Source changed while archiving")
        manifest["script_sha256"] = baseline.sha256(folder / Path(__file__).name)
        manifest["baseline_script_sha256"] = baseline.sha256(folder / Path(baseline.__file__).name)
        baseline.write_json(folder / "demand.json", demand)
        baseline.write_json(folder / "assignments.json", assignments)
        baseline.write_json(folder / "event.json", config["event"])
        env = baseline.isolated_env(folder)
        sumo, netconvert = baseline.locate_binary("sumo"), baseline.locate_binary("netconvert")
        manifest["versions"] = {n: baseline.invoke([str(binary), "--version"], folder, env,
                                                  physics["binary_timeout_s"], n + ".version.log")
                                for n, binary in (("sumo", sumo), ("netconvert", netconvert))}
        write_scenario_inputs(folder, network, physics, assignments, config["event"])
        build = [str(netconvert), "--node-files", "network.nod.xml", "--edge-files", "network.edg.xml",
                 "--connection-files", "network.con.xml", "--output-file", "network.net.xml",
                 "--no-turnarounds", "true", "--no-internal-links", "false", "--offset.disable-normalization", "true",
                 "--xml-validation", "never"]
        manifest["commands"].append(build)
        baseline.invoke(build, folder, env, physics["binary_timeout_s"], "netconvert.log")
        manifest["network"] = baseline.audit_network(folder, network)
        command = [str(sumo), "-c", "baseline.sumocfg"]
        manifest["commands"].append(command)
        baseline.invoke(command, folder, env, physics["binary_timeout_s"], "sumo.log")
        metrics = baseline.load_metrics(folder, assignments, physics["simulation_duration_s"])
        trips = [dict(e.attrib) for _, e in ET.iterparse(folder / "tripinfo.xml") if e.tag == "tripinfo"]
        ordinary = [a for a in assignments if a["role"] == "ordinary"]
        ids = {a["id"] for a in ordinary}
        metrics["ordinary"] = baseline.compute_metrics([t for t in trips if t["id"] in ids], ordinary,
                                                       physics["simulation_duration_s"])
        metrics["hospital_tasks"] = task_metrics(trips, assignments, physics["simulation_duration_s"])
        metrics["changed_assignments"] = sum(a["route"] != demands[a["od_id"]]["default_route_id"] for a in assignments)
        metrics["coordination_B_status"] = "not_implemented"
        baseline.write_json(folder / "metrics.json", metrics)
        manifest.update(status="completed" if metrics["physics_valid"] else "physics_invalid",
                        physics_valid=metrics["physics_valid"], quality_reasons=metrics["quality_reasons"],
                        finished_at=baseline.utc_now())
        manifest["artifacts"] = {p.name: baseline.sha256(p) for p in sorted(folder.iterdir())
                                 if p.is_file() and p.name != "run_manifest.json"}
        baseline.write_json(folder / "run_manifest.json", manifest)
        return {"name": name, "folder": str(folder.relative_to(ROOT)), "manifest": manifest, "metrics": metrics}
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, subprocess.SubprocessError, RuntimeError) as error:
        manifest.update(status="failed", error=str(error), finished_at=baseline.utc_now())
        baseline.write_json(folder / "run_manifest.json", manifest)
        raise


def paired_comparison(fixed: dict, guided: dict) -> dict:
    a, b = fixed["manifest"], guided["manifest"]
    if a["demand_sha256"] != b["demand_sha256"] or a["event_sha256"] != b["event_sha256"] or a["config"] != b["config"]:
        raise ValueError("Paired demand, event or vehicle/simulation settings differ")
    ma, mb = fixed["metrics"], guided["metrics"]
    valid = ma["physics_valid"] and mb["physics_valid"]
    task_a, task_b = ma["hospital_tasks"], mb["hospital_tasks"]
    delta_task = None
    if valid and len(task_a) == len(task_b) == 1 and task_a[0]["completed"] and task_b[0]["completed"]:
        delta_task = task_b[0]["release_to_arrival_s"] - task_a[0]["release_to_arrival_s"]
    return {"fixed": fixed["name"], "guided": guided["name"], "same_demand": True, "same_event": True,
            "engineering_comparison_valid": valid, "eligible_for_governance": False,
            "ordinary_system_time_delta_s": (mb["ordinary"]["total_system_vehicle_time_s"] -
                                               ma["ordinary"]["total_system_vehicle_time_s"]) if valid else None,
            "ordinary_unfinished_fixed": ma["ordinary"]["unfinished_total"],
            "ordinary_unfinished_guided": mb["ordinary"]["unfinished_total"],
            "task_release_to_arrival_delta_s": delta_task,
            "note": "Guided minus fixed; positive means worse. Engineering assumptions only. B is not implemented."}


def load_inputs(config_path: Path) -> tuple:
    config, protocol_digest = stable_json(config_path)
    base_path = baseline.workspace_path(config["base_config"])
    catalog_path = baseline.workspace_path(config["route_catalog"])
    physics, base_digest = stable_json(base_path)
    catalog, catalog_digest = stable_json(catalog_path)
    if not set(config["simulation"]) <= {"simulation_duration_s", "departure_duration_s", "departure_interval_s"}:
        raise ValueError("Only common duration and demand interval overrides are supported")
    physics = copy.deepcopy(physics)
    physics.update(config["simulation"])
    physics["assumptions"] = config["assumptions"]
    baseline.validate_config(physics)
    if catalog["study_area_id"] != physics["study_area_id"]:
        raise ValueError("Study area mismatch")
    hashes = {str(p.relative_to(ROOT)): digest for p, digest in
              ((config_path, protocol_digest), (base_path, base_digest), (catalog_path, catalog_digest))}
    hashes.update({str(p.relative_to(ROOT)): baseline.sha256(p) for p in (Path(__file__), Path(baseline.__file__))})
    for relative, expected in catalog["source_sha256"].items():
        actual = baseline.sha256(baseline.workspace_path(relative))
        if actual != expected:
            raise ValueError("Catalog source digest mismatch: " + relative)
        hashes[relative] = actual
    network, demands, candidates = prepare_catalog(catalog)
    validate_protocol(config, physics, network, demands, candidates)
    speeds = config["event_speed_scenarios"]
    if not 1 <= len(speeds) <= 3 or len({s["id"] for s in speeds}) != len(speeds):
        raise ValueError("Use one to three distinct predeclared speed scenarios")
    for speed in speeds:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", speed["id"]):
            raise ValueError("Invalid speed scenario id")
        variant = copy.deepcopy(config)
        variant["event"]["speed_m_s"] = speed["speed_m_s"]
        validate_protocol(variant, physics, network, demands, candidates)
    baseline.locate_binary("sumo")
    baseline.locate_binary("netconvert")
    verify_inputs(hashes)
    return config, physics, network, demands, candidates, hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/scenarios.json")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Read-only offline input and policy checks")
    action.add_argument("--run", action="store_true", help="Run all four groups for every predeclared event speed")
    parser.add_argument("--run-id", help="Optional unique batch directory name")
    args = parser.parse_args(argv)
    if not args.check and not args.run:
        parser.print_help()
        return 0
    batch, manifest = None, None
    try:
        config_path = baseline.workspace_path(args.config)
        config, physics, network, demands, candidates, hashes = load_inputs(config_path)
        if args.check:
            print(json.dumps({"status": "inputs_valid", "demand_od_count": len(demands),
                              "candidate_count": len(candidates), "background_vehicles": len(fixed_demand(demands, physics, config["hospital_task"], False)),
                              "event_length_m": sum(e["length_m"] for e in network["edges"] if e["id"] in config["event"]["edge_ids"]),
                              "cases": [s["id"] + "_" + case[0] for s in config["event_speed_scenarios"] for case in CASES],
                              "coordination_B_status": "not_implemented"}, indent=2))
            return 0
        identity = args.run_id or datetime.now(timezone.utc).strftime("SCENARIOS_%Y%m%dT%H%M%S_%fZ")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", identity):
            raise ValueError("Invalid run-id")
        batch = baseline.workspace_path(ROOT / "outputs/scenarios" / identity)
        batch.mkdir(parents=True, exist_ok=False)
        baseline.write_json(batch / "scenario_config.json", config)
        manifest = {"batch_id": identity, "status": "running", "purpose": config["purpose"], "inputs_sha256": hashes,
                    "started_at": baseline.utc_now(), "eligible_for_governance": False, "coordination_B_status": "not_implemented", "runs": []}
        baseline.write_json(batch / "manifest.json", manifest)
        snapshots = batch / "input_snapshots"
        snapshots.mkdir()
        for source in (config_path, baseline.workspace_path(config["base_config"]), baseline.workspace_path(config["route_catalog"])):
            snapshot = snapshots / source.name
            snapshot.write_bytes(source.read_bytes())
            if baseline.sha256(snapshot) != hashes[str(source.relative_to(ROOT))]:
                raise ValueError("Input changed while archiving batch")
        verify_inputs(hashes)
        runs, comparisons = [], []
        for speed in config["event_speed_scenarios"]:
            variant = copy.deepcopy(config)
            variant["event"]["speed_m_s"] = speed["speed_m_s"]
            group = []
            for case_name, include_task, guided in CASES:
                verify_inputs(hashes)
                name = speed["id"] + "_" + case_name
                result = run_case(batch / name, name, network, demands, candidates, variant, physics, include_task, guided, hashes)
                runs.append(result)
                group.append(result)
                manifest["runs"].append({"case": name, "folder": result["folder"], "status": result["manifest"]["status"],
                                         "demand_sha256": result["manifest"]["demand_sha256"]})
                baseline.write_json(batch / "manifest.json", manifest)
                print(f"{name}: {result['manifest']['status']}, planned={result['metrics']['planned']}, arrived={result['metrics']['arrived']}", flush=True)
            comparisons.extend((paired_comparison(group[0], group[1]), paired_comparison(group[2], group[3])))
        verify_inputs(hashes)
        baseline.write_json(batch / "comparisons.json", comparisons)
        rows = []
        for run in runs:
            m, r = run["metrics"], run["manifest"]
            task = m["hospital_tasks"][0] if m["hospital_tasks"] else {}
            rows.append({"case": run["name"], "physics_valid": m["physics_valid"],
                         "planned": m["planned"], "arrived": m["arrived"], "unfinished_total": m["unfinished_total"],
                         "ordinary_system_time_s": m["ordinary"]["total_system_vehicle_time_s"],
                         "ordinary_departure_wait_s": m["ordinary"]["total_departure_wait_time_s"],
                         "ordinary_unfinished": m["ordinary"]["unfinished_total"],
                         "task_release_to_arrival_s": task.get("release_to_arrival_s"), "task_completed": task.get("completed"),
                         "changed_assignments": m["changed_assignments"], "demand_sha256": r["demand_sha256"],
                         **m["physics_counters"]})
        baseline.write_json(batch / "summary.json", rows)
        with (batch / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        valid = all(row["physics_valid"] for row in rows)
        manifest.update(status="completed" if valid else "completed_with_invalid_scenarios", finished_at=baseline.utc_now(),
                        all_scenarios_physics_valid=valid, inputs_unchanged_at_completion=True,
                        comparison_sha256=baseline.sha256(batch / "comparisons.json"))
        manifest["artifacts"] = {p.name: baseline.sha256(p) for p in sorted(batch.iterdir())
                                 if p.is_file() and p.name != "manifest.json"}
        baseline.write_json(batch / "manifest.json", manifest)
        print("Engineering results only: " + str(batch.relative_to(ROOT)))
        return 0 if valid else 2
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, subprocess.SubprocessError, RuntimeError) as error:
        if batch and manifest:
            manifest.update(status="failed", error=str(error), finished_at=baseline.utc_now())
            baseline.write_json(batch / "manifest.json", manifest)
        print(f"Scenario error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
