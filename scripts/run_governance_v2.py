#!/usr/bin/env python3
"""Paired governance simulations on the directly fitted SUMO V2 mechanism.

Reads frozen inverse fits and training-derived full-step perturbations. Preserves
all vehicles and waiting, applies causal A/B control, and retains invalid cases.
No fitting, output-time correction, external API, or global monkeypatching.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
try:
    from . import sumo_inverse_engine as engine
    from . import run_final_experiments as original
except ImportError:
    import sumo_inverse_engine as engine
    import run_final_experiments as original

baseline = engine.baseline
prior = engine.scenarios
ROOT = engine.ROOT
OUT = ROOT / "outputs/governance_v2"
FIT = ROOT / "outputs/identification_v2/calibration/fit.json"
CALIBRATION = ROOT / "config/inverse_estimation_v2.json"
BASIS = ROOT / "research/parameter_audit/scenario_basis_v2.json"
CASES = (("S2", "fixed"), ("S2", "A"), ("S3", "fixed"), ("S3", "A"), ("S3", "B"))
CONTROL = {"maximum_source_hold_s": 45, "maximum_task_control_window_s": 180,
           "source": "explicit bounded admission-control design, not a field permission or measured delay"}
TIMING = {"warmup_s": 1200, "event_begin_s": 1800, "event_published_s": 1800,
          "task_release_s": 1920, "background_end_s": 3000, "simulation_end_s": 6000,
          "state_sample_period_s": 30,
          "interpretation": "Counterfactual simulation clock; no claim of a recorded accident timeline"}


def write(path: Path, obj: object) -> None:
    baseline.write_json(path, obj)


def canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def object_hash(obj: object) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def resolve_event_edges(old_edges: list[str], catalog: dict) -> list[str]:
    """Map the unchanged observational V1 geometry through V2 canonical chains."""
    known = {e["id"] for e in catalog["network"]["edges"]}
    aliases = catalog.get("old_edge_to_canonical_chain", {})
    result = []
    for old in old_edges:
        chain = aliases.get(old, [old] if old in known else None)
        if not isinstance(chain, list) or not chain or set(chain) - known:
            raise ValueError("Missing canonical chain for observational event edge " + old)
        for edge in chain:
            if not result or result[-1] != edge:
                result.append(edge)
    edges = {e["id"]: e for e in catalog["network"]["edges"]}
    if len(result) != len(set(result)) or any(edges[a]["to"] != edges[b]["from"] for a, b in zip(result, result[1:])):
        raise ValueError("Mapped complete event step is not a simple contiguous path")
    return result


def load_context(fit_path: Path = FIT, calibration_path: Path = CALIBRATION,
                 basis_path: Path = BASIS) -> dict:
    fit, calibration, basis = [baseline.read_json(p) for p in (fit_path, calibration_path, basis_path)]
    if fit["config_sha256"] != baseline.sha256(calibration_path):
        raise ValueError("Calibration configuration changed after fitting")
    fit_manifest_path = fit_path.parent / "fit_manifest.json"
    fit_manifest = baseline.read_json(fit_manifest_path)
    if fit_manifest["config_sha256"] != fit["config_sha256"]:
        raise ValueError("Fit manifest configuration mismatch")
    for name, expected in fit_manifest["artifacts"].items():
        path = (fit_path.parent / name).resolve()
        if not path.is_relative_to(fit_path.parent) or baseline.sha256(path) != expected:
            raise ValueError("Frozen fitted artifact changed: " + name)
    prior.verify_inputs(fit["source_sha256"])
    for source in basis["source_files"]:
        if baseline.sha256(baseline.workspace_path(source["path"])) != source["sha256"]:
            raise ValueError("Scenario-basis source hash mismatch: " + source["path"])
    if not basis["training_only"] or basis["holdout_used_for_range_or_model_selection"]:
        raise ValueError("Scenario strength must derive from training only")
    protocol = engine.normalize_protocol(calibration["engine_protocol"])
    protocol.update(warmup_s=TIMING["warmup_s"], background_end_s=TIMING["background_end_s"],
                    simulation_end_s=TIMING["simulation_end_s"],
                    network_cache="outputs/governance_v2/network_cache")
    protocol = engine.normalize_protocol(protocol)
    catalog = baseline.read_json(baseline.workspace_path(protocol["route_catalog"]))
    mapping_path = None
    if catalog.get("normalization_mapping_file"):
        mapping_path = baseline.workspace_path(catalog["normalization_mapping_file"])
        mapping = baseline.read_json(mapping_path)
        if mapping["normalized_catalog_sha256"] != baseline.sha256(baseline.workspace_path(protocol["route_catalog"])):
            raise ValueError("Canonical edge mapping does not match the fitted catalogue")
        catalog["old_edge_to_canonical_chain"] = mapping["old_edge_to_canonical_chain"]
    network, ods, candidates, physics = engine.load_inputs(protocol)
    event_edges = resolve_event_edges(basis["event_geometry"]["edge_ids"], catalog)
    selected = [p for p in fit["profiles"] if p["best"]["objective"] is not None]
    if not selected:
        raise ValueError("No physically valid inverse profile is available for governance")
    for profile in selected:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", profile["id"]):
            raise ValueError("Unsafe profile name")
        run = baseline.workspace_path(profile["best"]["run_dir"])
        manifest = baseline.read_json(run / "run_manifest.json")
        if manifest["parameters"] != profile["best"]["parameters"]:
            raise ValueError("Selected profile differs from its fitted run")
        if manifest["source_hashes"] != engine.source_hashes(calibration["engine_protocol"]):
            raise ValueError("Selected fit mechanism changed before governance")
        for name, expected in manifest["artifacts"].items():
            if baseline.sha256(run / name) != expected:
                raise ValueError("Selected inverse run changed: " + name)
    sources = [fit_path, fit_manifest_path, calibration_path, basis_path, Path(__file__), Path(original.__file__)]
    if mapping_path:
        sources.append(mapping_path)
    hashes = {str(p.relative_to(ROOT)): baseline.sha256(p) for p in sources}
    hashes.update(engine.source_hashes(protocol))
    context = {"fit": fit, "calibration": calibration, "basis": basis, "protocol": protocol,
               "catalog": catalog, "network": network, "ods": ods, "candidates": candidates,
               "physics": physics, "profiles": selected, "event_edges": event_edges,
               "input_hashes": hashes, "config": {"coordination": CONTROL,
               "hospital_task": {"id": "hospital.task", "od_id": basis["event_geometry"]["route_pair_id"],
                                 "release_s": TIMING["task_release_s"]}}}
    return context


def make_demand(context: dict, expanded: dict, include_task: bool) -> list[dict]:
    """Same fitted conditional OD rates and offsets; no measurement probes added."""
    result = []
    for od in sorted(context["ods"]):
        headway = expanded["od_headways_s"][od]
        if headway is None:
            continue
        offset = expanded["background_start_offsets_s"][od]
        for index in range(max(0, math.ceil((TIMING["background_end_s"]-offset)/headway))):
            release = math.ceil(offset+index*headway-1e-9)
            if release < TIMING["background_end_s"]:
                result.append({"id": f"ordinary.{od}.{index:05d}", "od_id": od,
                               "release_s": release, "role": "ordinary", "vehicle_type": "assumed_car"})
    if include_task:
        task = context["config"]["hospital_task"]
        result.append({"id": task["id"], "od_id": task["od_id"], "release_s": task["release_s"],
                       "role": "hospital_task", "vehicle_type": "assumed_car"})
    return sorted(result, key=lambda row: (row["release_s"], row["id"]))


def make_event(context: dict, preset: dict, enabled: bool) -> dict:
    multiplier = float(preset["speed_multiplier"])
    if not 0 < multiplier < 1:
        raise ValueError("Perturbation multiplier must be in (0,1)")
    return {"id": preset["id"], "enabled": enabled, "edge_ids": context["event_edges"],
            "observational_v1_edge_ids": context["basis"]["event_geometry"]["edge_ids"],
            "target_od_id": context["basis"]["event_geometry"]["route_pair_id"],
            "begin_s": TIMING["event_begin_s"], "published_at_s": TIMING["event_published_s"],
            "end_s": TIMING["event_begin_s"] + preset["duration_s"],
            "speed_multiplier": multiplier, "strength_source": "training quantile ratio of same full-step effective travel speed",
            "mechanism": "Per-external-edge operating-speed cap perturbation; internal junction speeds retain fitted network values",
            "duration_source": "declared experimental design in scenario_basis_v2, not measured incident duration"}


def route_cost(edges: list[str], lengths: dict, speeds: dict, event: dict, now: float) -> float:
    active = event["enabled"] and event["published_at_s"] <= now and event["begin_s"] <= now < event["end_s"]
    event_set = set(event["edge_ids"]) if active else set()
    return sum(lengths[e] / (speeds[e] * (event["speed_multiplier"] if e in event_set else 1)) for e in edges)


def choose_route(row: dict, now: float, context: dict, expanded: dict, event: dict, policy: str) -> dict:
    if now < row["release_s"]:
        raise ValueError("Route assigned before demand release")
    od = row["od_id"]
    default = context["ods"][od]["default_route_id"]
    selected, estimates, reason = default, {}, "fixed_default"
    active = event["enabled"] and event["published_at_s"] <= now and event["begin_s"] <= now < event["end_s"]
    # Preserves V1 causal intervention scope: target OD only, during known event.
    # Before publication all policies use the identical default baseline.
    if policy != "fixed" and active and od == event["target_od_id"]:
        default_edges = context["candidates"][default]["edge_ids"]
        lengths = {e["id"]: e["length_m"] for e in context["network"]["edges"]}
        for candidate in context["candidates"].values():
            edges = candidate["edge_ids"]
            if candidate["demand_od_id"] == od and (edges[0], edges[-1]) == (default_edges[0], default_edges[-1]):
                estimates[candidate["id"]] = route_cost(edges, lengths, expanded["edge_effective_speed_m_s"], event, now)
        if default not in estimates:
            raise ValueError("Default route missing from feasible alternatives")
        cost = estimates[default]
        for candidate in sorted(estimates):
            if estimates[candidate] < cost-1e-9:
                selected, cost = candidate, estimates[candidate]
        reason = "minimum_current_effective_cost" if selected != default else "default_retained_by_current_effective_cost"
    return {**row, "route": selected, "depart": row["release_s"], "decision_s": now,
            "reason": reason, "candidate_estimated_cost_s": estimates,
            "cost_basis": "full route length / fitted per-edge effective speed under currently active event; no queue or future-recovery prediction"}


def build_inputs(folder: Path, context: dict, expanded: dict, event: dict) -> tuple[dict, dict]:
    # Reuse the frozen inverse engine's exact network construction and repairs.
    engine.write_case_inputs(folder, context["network"], context["physics"], expanded, [], context["protocol"])
    additions = ET.parse(folder / "inverse.add.xml")
    if event["enabled"]:
        for edge in event["edge_ids"]:
            normal = expanded["edge_effective_speed_m_s"][edge]
            sign = ET.SubElement(additions.getroot(), "variableSpeedSign", id="event."+edge, lanes=edge+"_0")
            for when, speed in ((0, normal), (event["begin_s"], normal*event["speed_multiplier"]), (event["end_s"], normal)):
                ET.SubElement(sign, "step", time=str(when), speed=format(speed, ".12g"))
    baseline.write_xml(folder / "inverse.add.xml", additions.getroot())
    config = ET.parse(folder / "inverse.sumocfg")
    ET.SubElement(config.getroot().find("output"), "fcd-output", value="fcd.xml")
    section = ET.SubElement(config.getroot(), "fcd_device")
    ET.SubElement(section, "device.fcd.period", value=str(TIMING["state_sample_period_s"]))
    baseline.write_xml(folder / "governance.sumocfg", config.getroot())
    env = baseline.isolated_env(folder)
    audit = engine.prepare_network_cache(folder, context["network"], context["protocol"],
                                         engine.source_hashes(context["protocol"]), baseline.locate_binary("netconvert"), env)
    return env, audit


def simulate(folder: Path, context: dict, expanded: dict, event: dict,
             policy: str, demand: list[dict], env: dict) -> dict:
    traci = original.traci_module()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = [str(baseline.locate_binary("sumo")), "-c", "governance.sumocfg", "--remote-port", str(port)]
    start = time.monotonic()
    assigned, admissions, submitted, completed, actions, queue_trace = {}, {}, set(), set(), [], []
    next_request = 0
    conn = process = None
    with (folder / "sumo.log").open("w") as stream:
        try:
            process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT)
            conn = traci.connect(port=port, host="127.0.0.1", numRetries=20, proc=process, waitBetweenRetries=.1)
            while conn.simulation.getTime() < TIMING["simulation_end_s"]:
                if time.monotonic()-start > context["protocol"]["binary_timeout_s"]:
                    raise TimeoutError("Governance SUMO exceeded declared wall-time budget")
                now = conn.simulation.getTime()
                while next_request < len(demand) and demand[next_request]["release_s"] <= now:
                    row = demand[next_request]
                    assigned[row["id"]] = choose_route(row, now, context, expanded, event, policy)
                    next_request += 1
                active_ids = set(conn.vehicle.getIDList())
                task_state = None
                task_id = context["config"]["hospital_task"]["id"]
                if task_id in assigned and task_id not in completed:
                    task = assigned[task_id]
                    edges = context["candidates"][task["route"]]["edge_ids"]
                    index = max(0, conn.vehicle.getRouteIndex(task_id)) if task_id in active_ids else 0
                    task_state = {"active": True, "release_s": task["release_s"], "remaining_edges": edges[index:],
                                  "route_index": index, "visibility": "in_network" if task_id in active_ids else "released_waiting_insertion"}
                waiting = []
                for identity, row in sorted(assigned.items(), key=lambda item: (item[1]["release_s"], item[0])):
                    if identity in submitted:
                        continue
                    edges = context["candidates"][row["route"]]["edge_ids"]
                    hold = policy == "B" and original.should_hold(row, now, task_state, CONTROL, edges)
                    if hold:
                        waiting.append(identity)
                        if not row.get("hold_started"):
                            row["hold_started"] = True
                            actions.append({"time_s": now, "action": "hold_at_source", "vehicle_id": identity,
                                            "task_state": copy.deepcopy(task_state), "original_release_s": row["release_s"]})
                        continue
                    conn.vehicle.add(identity, row["route"], typeID="assumed_car", depart="now", departLane="0", departSpeed="0")
                    submitted.add(identity)
                    admissions[identity] = now
                    row["admitted_s"] = now
                    row["source_control_wait_s"] = now-row["release_s"]
                    if row.get("hold_started"):
                        actions.append({"time_s": now, "action": "release_from_source", "vehicle_id": identity,
                                        "source_control_wait_s": row["source_control_wait_s"]})
                if now % TIMING["state_sample_period_s"] == 0:
                    queue_trace.append({"time_s": now, "source_waiting": len(waiting),
                                        "task_active": task_state is not None,
                                        "task_route_index": task_state["route_index"] if task_state else None})
                conn.simulationStep()
                completed.update(conn.simulation.getArrivedIDList())
            conn.close()
            conn = None
            process.wait(timeout=15)
            if process.returncode:
                raise RuntimeError("SUMO nonzero exit; original log retained")
        finally:
            if conn is not None:
                try:
                    conn.close(wait=False)
                except Exception:
                    pass
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    for row in demand:
        if row["id"] not in assigned:
            assigned[row["id"]] = {**row, "route": context["ods"][row["od_id"]]["default_route_id"],
                                    "depart": row["release_s"], "decision_s": None, "admitted_s": None}
    ordered = [assigned[row["id"]] for row in demand]
    write(folder / "assignments.json", ordered)
    write(folder / "controller_log.json", actions)
    write(folder / "source_queue_trace.json", queue_trace)
    replay = ET.parse(folder / "demand.rou.xml").getroot()
    for identity, admitted in sorted(admissions.items(), key=lambda item: (item[1], item[0])):
        ET.SubElement(replay, "vehicle", id=identity, type="assumed_car", route=assigned[identity]["route"],
                      depart=str(admitted), departLane="0", departSpeed="0")
    baseline.write_xml(folder / "realized_admissions.rou.xml", replay)
    return {"assignments": ordered, "admissions": admissions, "commands": [command],
            "controller_action_count": len(actions), "submitted_count": len(submitted)}


def trajectory_prefixes(path: Path) -> dict:
    hashes = {"before_event": hashlib.sha256(), "before_task": hashlib.sha256()}
    counts = {k: 0 for k in hashes}
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "timestep":
            continue
        seconds = float(element.get("time"))
        content = canonical({"time": seconds, "vehicles": sorted([dict(v.attrib) for v in element.findall("vehicle")], key=lambda v: v["id"])})
        for key, stop in (("before_event", TIMING["event_begin_s"]), ("before_task", TIMING["task_release_s"])):
            if seconds < stop:
                hashes[key].update(content)
                counts[key] += 1
        element.clear()
    return {key: {"sha256": value.hexdigest(), "frames": counts[key]} for key, value in hashes.items()}


def collect_metrics(folder: Path, context: dict, result: dict) -> dict:
    physics = {**context["physics"], "simulation_duration_s": TIMING["simulation_end_s"]}
    metrics = original.collect_metrics(folder, context, physics, result)
    metrics["model_status"] = "conditional_inverse_estimated_effective_operation"
    metrics["scope"] = "Paired counterfactual on fitted effective operating constraints; real traffic counts and signal timings remain unidentified"
    trips = [dict(e.attrib) for _, e in ET.iterparse(folder / "tripinfo.xml") if e.tag == "tripinfo"]
    cohort = [a for a in result["assignments"] if a["role"] == "ordinary" and a["release_s"] >= TIMING["event_begin_s"]]
    ids = {a["id"] for a in cohort}
    metrics["ordinary_post_event_release_cohort"] = baseline.compute_metrics([t for t in trips if t["id"] in ids], cohort, TIMING["simulation_end_s"])
    for part in [metrics["ordinary"], metrics["ordinary_post_event_release_cohort"], *metrics["per_od"].values()]:
        part["model_status"] = "conditional_inverse_estimated_effective_operation"
        part["observation_status"] = "simulated_conditional_demand_profile_not_measured_counts"
    metrics["whole_population_complete"] = metrics["unfinished_total"] == 0
    metrics["system_time_is_lower_bound_if_unfinished"] = not metrics["whole_population_complete"]
    metrics["eligible_for_conditional_pair_comparison"] = metrics["physics_valid"] and metrics["whole_population_complete"]
    metrics["trajectory_prefixes"] = trajectory_prefixes(folder / "fcd.xml")
    outcomes = engine.trip_records(trips, result["assignments"], TIMING["simulation_end_s"])
    for row in outcomes:
        admitted = result["admissions"].get(row["id"])
        row["source_control_wait_s"] = (admitted if admitted is not None else TIMING["simulation_end_s"])-row["release_s"]
        row["sumo_insertion_wait_s"] = row["insertion_delay_s"]-row["source_control_wait_s"]
    write(folder / "vehicle_outcomes.json", outcomes)
    return metrics


def row_from_metrics(folder: Path, manifest: dict, metrics: dict) -> dict:
    task = metrics["hospital_tasks"][0] if metrics["hospital_tasks"] else {}
    return {"case": manifest["case"], "profile": manifest["profile_id"], "preset": manifest["preset_id"],
            "scenario_family": manifest["scenario_family"], "policy": manifest["policy"],
            "folder": str(folder.relative_to(ROOT)), "profile_passes_training_screen": manifest["profile_passes_training_screen"],
            "event_speed_multiplier": manifest["event"]["speed_multiplier"] if manifest["event"]["enabled"] else None,
            "planned": metrics["planned"], "arrived": metrics["arrived"], "unfinished_total": metrics["unfinished_total"],
            "physics_valid": metrics["physics_valid"], "comparison_eligible": metrics["eligible_for_conditional_pair_comparison"],
            "eligible_for_fitted_scenario_conclusion": metrics["eligible_for_conditional_pair_comparison"] and manifest["profile_passes_training_screen"],
            **metrics["physics_counters"], "ordinary_system_time_s": metrics["ordinary"]["total_system_vehicle_time_s"],
            "ordinary_post_event_system_time_s": metrics["ordinary_post_event_release_cohort"]["total_system_vehicle_time_s"],
            "ordinary_in_network_time_s": metrics["ordinary"]["total_in_network_vehicle_time_s"],
            "ordinary_source_control_wait_s": metrics["ordinary_source_control_wait_s"],
            "ordinary_sumo_insertion_wait_s": metrics["ordinary_sumo_insertion_wait_s"],
            "maximum_source_control_wait_s": metrics["maximum_source_control_wait_s"],
            "source_held_vehicle_count": metrics["source_held_vehicle_count"],
            "changed_route_count": metrics["changed_route_count"], "task_completed": task.get("completed"),
            "task_release_to_arrival_s": task.get("release_to_arrival_s"),
            "task_insertion_wait_s": task.get("insertion_wait_s"),
            "demand_sha256": manifest["demand_sha256"], "event_sha256": manifest["event_sha256"],
            "physics_sha256": manifest["physics_sha256"], "trajectory_prefixes": metrics["trajectory_prefixes"],
            "run_manifest_sha256": baseline.sha256(folder / "run_manifest.json")}


def run_case(context: dict, profile: dict, preset: dict, family: str, policy: str, folder: Path) -> dict:
    expanded = engine.expand_parameters(profile["best"]["parameters"], context["protocol"],
                                         context["network"], context["ods"], context["physics"])
    event = make_event(context, preset, family != "S1")
    demand = make_demand(context, expanded, family == "S3")
    evidence = {"parameters": profile["best"]["parameters"], "engine_protocol": context["protocol"],
                "timing": TIMING, "coordination": CONTROL, "input_hashes": context["input_hashes"]}
    manifest = {"schema_version": 2, "status": "running", "case": folder.name,
                "profile_id": profile["id"], "preset_id": preset["id"] if family != "S1" else "none",
                "profile_passes_training_screen": profile["passes_consistency_screen"],
                "scenario_family": family, "policy": policy, "event": event,
                "demand_sha256": object_hash(demand), "event_sha256": object_hash(event),
                "physics_sha256": object_hash({"physics": context["physics"], "expanded": expanded, "protocol": context["protocol"]}),
                "input_hashes": context["input_hashes"], "started_at": baseline.utc_now()}
    if folder.exists():
        previous = baseline.read_json(folder / "run_manifest.json")
        for key in ("input_hashes", "demand_sha256", "event_sha256", "physics_sha256", "policy"):
            if previous[key] != manifest[key]:
                raise ValueError("Saved governance case differs: " + key)
        if previous["status"] not in ("completed", "physics_invalid", "incomplete"):
            raise ValueError("Retained failed/in-progress directory requires separate output: " + str(folder.relative_to(ROOT)))
        for name, expected in previous["artifacts"].items():
            if baseline.sha256(folder / name) != expected:
                raise ValueError("Governance artifact hash mismatch: " + name)
        return row_from_metrics(folder, previous, baseline.read_json(folder / "metrics.json"))
    folder.mkdir(parents=True, exist_ok=False)
    write(folder / "run_manifest.json", manifest)
    try:
        write(folder / "protocol.json", evidence)
        write(folder / "demand.json", demand)
        write(folder / "event.json", event)
        write(folder / "expanded_parameters.json", expanded)
        for source in (Path(__file__), Path(engine.__file__), Path(original.__file__), Path(prior.__file__), Path(baseline.__file__)):
            shutil.copyfile(source, folder / source.name)
        env, audit = build_inputs(folder, context, expanded, event)
        manifest["network"] = audit
        manifest["sumo_version"] = baseline.invoke([str(baseline.locate_binary("sumo")), "--version"], folder, env,
                                                   context["protocol"]["binary_timeout_s"], "sumo.version.log")
        result = simulate(folder, context, expanded, event, policy, demand, env)
        metrics = collect_metrics(folder, context, result)
        write(folder / "metrics.json", metrics)
        prior.verify_inputs(context["input_hashes"])
        manifest.update(status="physics_invalid" if not metrics["physics_valid"] else "completed" if metrics["whole_population_complete"] else "incomplete",
                        finished_at=baseline.utc_now(), commands=result["commands"], inputs_unchanged=True)
        manifest["artifacts"] = {p.name: baseline.sha256(p) for p in sorted(folder.iterdir()) if p.is_file() and p.name != "run_manifest.json"}
        write(folder / "run_manifest.json", manifest)
        return row_from_metrics(folder, manifest, metrics)
    except Exception as error:
        manifest.update(status="failed", finished_at=baseline.utc_now(), error_type=type(error).__name__, error=str(error))
        write(folder / "run_manifest.json", manifest)
        raise


def comparisons(rows: list[dict]) -> list[dict]:
    indexed = {(r["profile"], r["preset"], r["scenario_family"], r["policy"]): r for r in rows}
    results = []
    for row in rows:
        if row["policy"] == "fixed":
            continue
        for reference_policy in (["fixed", "A"] if row["policy"] == "B" else ["fixed"]):
            reference = indexed.get((row["profile"], row["preset"], row["scenario_family"], reference_policy))
            if reference is None:
                continue
            for key in ("demand_sha256", "event_sha256", "physics_sha256"):
                if row[key] != reference[key]:
                    raise ValueError("Paired governance differs in " + key)
            prefix = "before_task" if row["policy"] == "B" and reference_policy == "A" else "before_event"
            if row["trajectory_prefixes"][prefix] != reference["trajectory_prefixes"][prefix]:
                raise ValueError("Policies do not share identical pre-intervention trajectories")
            valid = row["comparison_eligible"] and reference["comparison_eligible"]
            screened = row["profile_passes_training_screen"] and reference["profile_passes_training_screen"]
            results.append({"profile": row["profile"], "preset": row["preset"], "scenario_family": row["scenario_family"],
                            "policy": row["policy"], "reference_policy": reference_policy, "comparison_valid": valid,
                            "profile_passes_training_screen": screened,
                            "eligible_for_fitted_scenario_conclusion": valid and screened,
                            "interpretation": "fitted conditional scenario" if valid and screened else "diagnostic only: physical/complete or training consistency screen failed",
                            "identical_prefix": prefix, "ordinary_system_time_delta_s": row["ordinary_system_time_s"]-reference["ordinary_system_time_s"] if valid else None,
                            "ordinary_post_event_system_time_delta_s": row["ordinary_post_event_system_time_s"]-reference["ordinary_post_event_system_time_s"] if valid else None,
                            "task_time_delta_s": row["task_release_to_arrival_s"]-reference["task_release_to_arrival_s"] if valid and row["task_completed"] and reference["task_completed"] else None})
    return results


def individual_effect(reference: list[dict], controlled: list[dict]) -> dict:
    """Matched completed ordinary vehicles; no survivor-only fairness claim."""
    before = {r["id"]: r for r in reference if r["role"] == "ordinary"}
    after = {r["id"]: r for r in controlled if r["role"] == "ordinary"}
    if set(before) != set(after):
        raise ValueError("Individual comparison changed ordinary demand IDs")
    if not all(r["completed"] for r in list(before.values())+list(after.values())):
        return {"comparison_valid": False, "reason": "Unfinished vehicles: no completed-only benefit claim"}
    differences = []
    for identity, row in before.items():
        other = after[identity]
        if row["release_s"] != other["release_s"] or row["od_id"] != other["od_id"]:
            raise ValueError("Individual comparison changed original release or OD")
        differences.append(other["release_to_arrival_s"]-row["release_to_arrival_s"])
    return {"comparison_valid": True, "ordinary_count": len(differences),
            "benefited": sum(x < -1e-9 for x in differences), "harmed": sum(x > 1e-9 for x in differences),
            "unchanged": sum(abs(x) <= 1e-9 for x in differences),
            "total_system_time_delta_s": sum(differences), "maximum_extra_time_s": max([0]+differences),
            "maximum_time_saved_s": -min([0]+differences),
            "note": "The 45-second bound limits source control only, not individual whole-trip loss."}


def audit_individual_effects(rows: list[dict]) -> list[dict]:
    indexed = {(r["profile"], r["preset"], r["scenario_family"], r["policy"]): r for r in rows}
    result = []
    for row in rows:
        if row["policy"] != "B":
            continue
        reference = indexed.get((row["profile"], row["preset"], "S3", "A"))
        if reference is None:
            continue
        before_path = baseline.workspace_path(reference["folder"]) / "vehicle_outcomes.json"
        after_path = baseline.workspace_path(row["folder"]) / "vehicle_outcomes.json"
        effect = individual_effect(baseline.read_json(before_path), baseline.read_json(after_path))
        effect["comparison_valid"] = effect["comparison_valid"] and row["comparison_eligible"] and reference["comparison_eligible"]
        effect["profile_passes_training_screen"] = row["profile_passes_training_screen"] and reference["profile_passes_training_screen"]
        effect["eligible_for_fitted_scenario_conclusion"] = effect["comparison_valid"] and effect["profile_passes_training_screen"]
        if effect["comparison_valid"] and not math.isclose(effect["total_system_time_delta_s"], row["ordinary_system_time_s"]-reference["ordinary_system_time_s"], abs_tol=1e-6):
            raise ValueError("Individual and aggregate system-time effects disagree")
        result.append({"profile": row["profile"], "preset": row["preset"], "comparison": "B_minus_A", **effect,
                       "source_sha256": {str(p.relative_to(ROOT)): baseline.sha256(p) for p in (before_path, after_path)}})
    return result


def write_report(out: Path, summary: dict) -> None:
    lines = ["# V2：有来源的拥堵约束与拟合后治理对照", "",
             "本报告使用直接 SUMO 运行时间，不在输出端乘加 ETA 拟合公式。基准运行约束来自训练路线时间的条件反演；交通量仍是多个固定需求剖面，不能视为唯一识别出的真实 OD 流量。", "",
             "事件覆盖同几何的完整瑞金二路步骤：百度276米、模型278.665139米、19条边。中度/强度倍率分别来自训练有效速度Q25/Q50与Q10/Q50，乘在各边已拟合有效速度上。它们不是实测限速，也不证明存在对应事故。持续600秒是两个采样间隔的实验设计，300/900秒为可选设计尺度，本批次没有执行该时长敏感性。", "",
             "扰动通过各外部道路边的可变速度约束实现；内部路口边保持拟合建网时的速度。模型因此模拟的是选定道路段的运行约束变化，不是把整条路线及所有路口耗时同比缩放。", "",
             "前1200秒作为预热，1800秒发布并开始事件，1920秒发布任务，背景车辆释放至3000秒，观察到6000秒。发布事件前所有策略使用同样默认路线；A根据当时已知事件与各边有效速度比较完整路径，不预知恢复；B只暂缓任务发布后、尚未入网且与任务剩余路径重叠的普通请求。每车45秒、控制窗口180秒是算法约束，源端协调权限是假设。", "",
             "背景OD错峰与反演一致。治理不额外加入反演时的18辆测量探针；S1在本批次重新运行，不能把反演probe ETA直接当作治理基准。完整事件段有约209.93米为医院方向共同必经路段，备选只能绕开上游部分，不保证A获得收益。", "",
             "固定/A/B逐对校验车辆需求、事件与物理输入哈希，以及事件前FCD轨迹完全一致；A/B另校验任务发布前轨迹。全部源端控制等待及SUMO入网等待均从原始需求发布时间计入。若碰撞、异常传送、丢弃或未完成，保存原始结果并禁止宣称对照收益。", "",
             "## 全部运行", "",
             "| 条件/情景/策略 | 普通总系统时间(车·秒) | 事件后释放普通车总时间 | 普通源端等待 | 任务总时间(秒) | 未完成 | 物理有效 | 训练筛选通过 |",
             "|---|---:|---:|---:|---:|---:|---|---|"]
    for row in summary["runs"]:
        task = row["task_release_to_arrival_s"]
        lines.append(f"| {row['case']} | {row['ordinary_system_time_s']:.0f} | {row['ordinary_post_event_system_time_s']:.0f} | {row['ordinary_source_control_wait_s']:.0f} | {task if task is not None else '无任务/未完成'} | {row['unfinished_total']} | {row['physics_valid']} | {row['profile_passes_training_screen']} |")
    lines += ["", "## 配对效果与诊断", "", "差值为策略减参照；负值是减少，正值是增加。comparison_valid仅表示物理有效且完整；只有同时通过训练一致性筛选的eligible_for_fitted_scenario_conclusion=true可支持拟合条件下的情景结论。未通过训练筛选的需求剖面完整保留，数值仅作模型失配诊断。", "",
              "| 需求/强度/情景 | 对比 | 普通总时间差(车·秒) | 事件后释放群体时间差 | 任务时间差(秒) | 物理完整 | 可用于拟合情景结论 |",
              "|---|---|---:|---:|---:|---|---|"]
    for pair in summary["comparisons"]:
        lines.append(f"| {pair['profile']}/{pair['preset']}/{pair['scenario_family']} | {pair['policy']}−{pair['reference_policy']} | {pair['ordinary_system_time_delta_s']} | {pair['ordinary_post_event_system_time_delta_s']} | {pair['task_time_delta_s']} | {pair['comparison_valid']} | {pair['eligible_for_fitted_scenario_conclusion']} |")
    valid = [p for p in summary["comparisons"] if p["comparison_valid"]]
    fitted = [p for p in valid if p["eligible_for_fitted_scenario_conclusion"]]
    b_a = [p for p in fitted if p["policy"] == "B" and p["reference_policy"] == "A"]
    faster = sum(p["task_time_delta_s"] is not None and p["task_time_delta_s"] < 0 for p in b_a)
    ordinary_harm = sum(p["ordinary_system_time_delta_s"] > 0 for p in b_a)
    lines += ["", "## 通过训练筛选的条件结论", "", f"本批次有 {len(valid)} 组物理完整的配对比较，其中 {len(fitted)} 组同时通过训练一致性筛选。仅在这 {len(fitted)} 组内统计：B对A共 {len(b_a)} 组，保障任务缩短 {faster} 组，普通交通总耗时增加 {ordinary_harm} 组。无改进或代价上升的结果与改善结果一并报告；不得将条件模拟解释为现场治理承诺。", "",
              "## 未通过训练筛选的诊断范围", "", f"其余 {len(valid)-len(fitted)} 组物理完整比较未通过训练一致性筛选，其收益或代价不能用于已拟合交通状态的治理结论。它们保留用于展示在更高/不同假设需求下的模型失配与敏感性。未完成或物理无效的运行另禁止收益比较。", "",
              "## 个体代价", "", "以下B−A比较保留每辆普通车原始释放时间；全体总耗时下降不代表每辆车受益。45秒仅限制主动源端等待，不是整个行程损失上限。", "",
              "| 需求/强度 | 受益 | 受损 | 不变 | 最大增加(秒) | 物理完整 | 可用于拟合情景结论 |", "|---|---:|---:|---:|---:|---|---|"]
    for row in summary.get("individual_effects", []):
        lines.append(f"| {row['profile']}/{row['preset']} | {row.get('benefited')} | {row.get('harmed')} | {row.get('unchanged')} | {row.get('maximum_extra_time_s')} | {row['comparison_valid']} | {row['eligible_for_fitted_scenario_conclusion']} |")
    lines += ["", "复现：`python3 -B scripts/run_governance_v2.py --run`。已有完整目录逐文件校验后复用；新计算或不同协议请在outputs/governance_v2下显式指定独立子目录，保留现有证据。"]
    (out / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def save_summary(out: Path, context: dict, rows: list[dict], complete: bool) -> dict:
    summary = {"schema_version": 2, "status": "completed" if complete else "partial", "input_hashes": context["input_hashes"],
               "timing": TIMING, "coordination": CONTROL, "runs": rows, "comparisons": comparisons(rows),
               "real_world_traffic_counts_identified": False, "output_time_correction": None,
               "conditional_on_fitted_effective_constraints": True,
               "eligibility_definitions": {"comparison_valid": "Both runs have valid physics and complete demand outcomes",
                                           "eligible_for_fitted_scenario_conclusion": "comparison_valid AND both runs use a profile passing the predeclared training consistency screen"},
               "all_comparisons_physically_complete": all(r["comparison_eligible"] for r in rows),
               "note": "Every conditional demand profile is reported; invalid and unfinished runs remain visible. No field causal claim."}
    if complete:
        summary["individual_effects"] = audit_individual_effects(rows)
    write(out / "summary.json", summary)
    if rows:
        with (out / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, dict) else v for k, v in row.items()} for row in rows)
    if complete:
        write_report(out, summary)
        write(out / "individual_effects.json", summary["individual_effects"])
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit", default=str(FIT.relative_to(ROOT)))
    parser.add_argument("--calibration", default=str(CALIBRATION.relative_to(ROOT)))
    parser.add_argument("--basis", default=str(BASIS.relative_to(ROOT)))
    parser.add_argument("--output", default=str(OUT.relative_to(ROOT)))
    parser.add_argument("--profile", action="append", help="Explicitly narrow profile IDs; all valid fits are the default")
    parser.add_argument("--preset", action="append", help="Explicitly narrow event IDs; both quantiles are the default")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--only-case", choices=["S1_fixed", "S2_fixed", "S2_A", "S3_fixed", "S3_A", "S3_B"])
    args = parser.parse_args(argv)
    if not (args.check or args.run):
        parser.print_help()
        return 0
    context = load_context(*(baseline.workspace_path(p) for p in (args.fit, args.calibration, args.basis)))
    profiles = [p for p in context["profiles"] if not args.profile or p["id"] in args.profile]
    presets = [p for p in context["basis"]["presets"] if not args.preset or p["id"] in args.preset]
    if not profiles or not presets or (args.profile and set(args.profile)-{p["id"] for p in profiles}) or (args.preset and set(args.preset)-{p["id"] for p in presets}):
        raise ValueError("Requested profile/preset unavailable")
    plan = []
    for profile in profiles:
        plan.append((profile, presets[0], "S1", "fixed"))
        for preset in presets:
            plan.extend((profile, preset, family, policy) for family, policy in CASES)
    if args.only_case:
        plan = [row for row in plan if row[2]+"_"+row[3] == args.only_case]
    if args.check:
        print(json.dumps({"status": "inputs_valid", "planned_runs": len(plan), "event_edges": len(context["event_edges"]),
                          "profiles": [p["id"] for p in profiles], "timing": TIMING}, ensure_ascii=False))
        return 0
    out = baseline.workspace_path(args.output)
    if not out.is_relative_to(OUT):
        raise ValueError("Governance outputs must remain inside outputs/governance_v2")
    out.mkdir(parents=True, exist_ok=True)
    protocol = {"input_hashes": context["input_hashes"], "timing": TIMING, "coordination": CONTROL,
                "planned_runs": [{"profile": p["id"], "preset": s["id"] if f != "S1" else "none", "family": f, "policy": a} for p, s, f, a in plan]}
    if (out / "protocol.json").exists() and baseline.read_json(out / "protocol.json") != protocol:
        raise ValueError("Preserve differing frozen governance protocol; use a new output subdirectory")
    write(out / "protocol.json", protocol)
    rows = []
    for profile, preset, family, policy in plan:
        prior.verify_inputs(context["input_hashes"])
        case = profile["id"]+"_"+(preset["id"] if family != "S1" else "none")+"_"+family+"_"+policy
        row = run_case(context, profile, preset, family, policy, out / "runs" / case)
        rows.append(row)
        save_summary(out, context, rows, False)
        print(json.dumps({"case": case, "arrived": row["arrived"], "planned": row["planned"], "valid": row["comparison_eligible"]}), flush=True)
    result = save_summary(out, context, rows, True)
    write(out / "manifest.json", {"input_hashes": context["input_hashes"], "run_count": len(rows),
                                  "artifacts": {name: baseline.sha256(out / name) for name in ("protocol.json", "summary.json", "summary.csv", "report.md", "individual_effects.json")}})
    return 0 if result["all_comparisons_physically_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
