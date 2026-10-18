#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Team review and AI disclosure required.
"""Reproducible S1/S2/S3 with causal route guidance and bounded source control.

Only workspace files and a local SUMO/TraCI process are used. No external network,
credentials, installations or system configuration changes. See --check/--run.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timedelta
import hashlib
import html
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
try:
    from . import run_baseline as baseline
    from . import run_scenarios as prior
except ImportError:
    import run_baseline as baseline
    import run_scenarios as prior

ROOT = baseline.ROOT
OUT = ROOT / "outputs/final_experiments"
CASES = (("S2_fixed", "S2", "fixed"), ("S2_A", "S2", "A"),
         ("S3_fixed", "S3", "fixed"), ("S3_A", "S3", "A"), ("S3_B", "S3", "B"))


def dump(path: Path, value: object) -> None:
    baseline.write_json(path, value)


def wall_time(config: dict, seconds: float) -> str:
    return (datetime.fromisoformat(config["experiment_anchor"]) + timedelta(seconds=seconds)).isoformat()


def load_protocol(path: Path) -> dict:
    config, digest = prior.stable_json(path)
    if config.get("protocol_version") != 1:
        raise ValueError("Unsupported final protocol")
    anchor = datetime.fromisoformat(config["experiment_anchor"])
    if anchor.utcoffset() is None or config.get("anchor_status") != "declared_counterfactual_experiment_clock_not_reconstructed_observation":
        raise ValueError("Experiment clock must be zoned and explicitly counterfactual")
    base_path, catalog_path = [baseline.workspace_path(config[key]) for key in ("base_config", "route_catalog")]
    physics, base_hash = prior.stable_json(base_path)
    catalog, catalog_hash = prior.stable_json(catalog_path)
    network, ods, candidates = prior.prepare_catalog(catalog)
    if physics["study_area_id"] != catalog["study_area_id"]:
        raise ValueError("Study area mismatch")
    for key in ("simulation_duration_s", "departure_duration_s", "step_length_s", "fcd_period_s", "seed"):
        physics[key] = config[key]
    physics["assumptions"] = config["assumptions"]
    baseline.validate_config(physics)
    event, task = config["event"], config["hospital_task"]
    sequence = event["edge_ids"]
    default = ods[event["target_od_id"]]["baseline_edge_ids"]
    if not sequence or not any(default[i:i + len(sequence)] == sequence for i in range(len(default))):
        raise ValueError("Slow event must be a contiguous observed default-route segment")
    if event["source"] != "assumed_event_not_observed_incident" or task["od_id"] != event["target_od_id"]:
        raise ValueError("Unexpected event/task relationship")
    edge_map = {e["id"]: e for e in network["edges"]}
    if any(event["road_name"] not in edge_map[e]["road_names"] for e in sequence):
        raise ValueError("Slow event road name lacks evidence")
    if not prior.avoiding_candidates(task["od_id"], ods, candidates, event):
        raise ValueError("Verified same-endpoint detour is required")
    for key in ("maximum_source_hold_s", "maximum_task_control_window_s"):
        prior.finite_number(config["coordination"][key], key, 1)
    if config["coordination"]["maximum_source_hold_s"] > config["coordination"]["maximum_task_control_window_s"]:
        raise ValueError("Per-vehicle hold must not exceed task control window")
    if task["release_s"] + config["coordination"]["maximum_source_hold_s"] >= physics["simulation_duration_s"]:
        raise ValueError("Task/source-control window leaves no observation time")
    if len({p["id"] for p in config["profiles"]}) != len(config["profiles"]):
        raise ValueError("Duplicate profile")
    for profile in config["profiles"]:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", profile["id"]):
            raise ValueError("Invalid profile id")
        for key in ("departure_interval_s", "event_speed_m_s", "event_duration_s"):
            prior.finite_number(profile[key], key, 0.001)
        if profile["event_speed_m_s"] >= physics["speed_limit_kmh"] / 3.6:
            raise ValueError("Event must reduce speed")
        if event["begin_s"] + profile["event_duration_s"] > physics["simulation_duration_s"]:
            raise ValueError("Event exceeds horizon")
        for value in (profile["departure_interval_s"], event["begin_s"], event["published_at_s"],
                      profile["event_duration_s"], task["release_s"]):
            if not math.isclose(value / physics["step_length_s"], round(value / physics["step_length_s"]), abs_tol=1e-9):
                raise ValueError("Times must align with simulation step")
    hashes = {str(path.relative_to(ROOT)): digest, str(base_path.relative_to(ROOT)): base_hash,
              str(catalog_path.relative_to(ROOT)): catalog_hash}
    hashes.update(catalog["source_sha256"])
    for source in (Path(__file__), Path(baseline.__file__), Path(prior.__file__)):
        hashes[str(source.relative_to(ROOT))] = baseline.sha256(source)
    prior.verify_inputs(hashes)
    return {"config": config, "physics": physics, "catalog": catalog, "network": network,
            "ods": ods, "candidates": candidates, "input_hashes": hashes, "config_path": path}


def profile_event(config: dict, profile: dict) -> dict:
    return {**config["event"], "end_s": config["event"]["begin_s"] + profile["event_duration_s"],
            "speed_m_s": profile["event_speed_m_s"]}


def choose_route(row: dict, now: float, context: dict, event: dict, policy: str, enabled: bool) -> dict:
    """One decision at actual request release; never uses future task/state."""
    if now < row["release_s"]:
        raise ValueError("A request cannot be assigned before release")
    return prior.assign_routes([row], context["ods"], context["candidates"], event,
                               enabled and policy != "fixed", context["network"]["edges"],
                               context["physics"]["speed_limit_kmh"] / 3.6)[0]


def should_hold(row: dict, now: float, task_state: dict | None, control: dict, route_edges: list[str]) -> bool:
    """Causal admission gate. Already submitted vehicles never enter this function."""
    if row["role"] != "ordinary" or task_state is None or not task_state.get("active"):
        return False
    release = task_state["release_s"]
    if now < release or row["release_s"] < release:
        return False
    if now - release >= control["maximum_task_control_window_s"]:
        return False
    if now - row["release_s"] >= control["maximum_source_hold_s"]:
        return False
    return bool(set(route_edges).intersection(task_state["remaining_edges"]))


def traci_module():
    folders = sorted((ROOT / ".venv/lib").glob("python*/site-packages/sumo/tools"))
    if not folders:
        raise FileNotFoundError("Bundled SUMO Python tools are unavailable")
    tools_dir = str(folders[0])
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import traci
    return traci


def build_inputs(folder: Path, context: dict, physics: dict, event: dict, enabled: bool) -> tuple:
    network = context["network"]
    baseline.write_inputs(folder, network, physics, [])
    additional = ET.Element("additional")
    if enabled:
        sign = ET.SubElement(additional, "variableSpeedSign", id=event["id"],
                             lanes=" ".join(e + "_0" for e in event["edge_ids"]))
        normal = str(physics["speed_limit_kmh"] / 3.6)
        ET.SubElement(sign, "step", time="0", speed=normal)
        ET.SubElement(sign, "step", time=str(event["begin_s"]), speed=str(event["speed_m_s"]))
        ET.SubElement(sign, "step", time=str(event["end_s"]), speed=normal)
    ET.SubElement(additional, "edgeData", id="edge_states", file="edge_states.xml", period="30", withInternal="true")
    baseline.write_xml(folder / "scenario.add.xml", additional)
    cfg = ET.parse(folder / "baseline.sumocfg").getroot()
    ET.SubElement(cfg.find("input"), "additional-files", value="scenario.add.xml")
    baseline.write_xml(folder / "baseline.sumocfg", cfg)
    env = baseline.isolated_env(folder)
    converter = baseline.locate_binary("netconvert")
    command = [str(converter), "--node-files", "network.nod.xml", "--edge-files", "network.edg.xml",
               "--connection-files", "network.con.xml", "--output-file", "network.net.xml",
               "--no-turnarounds", "true", "--no-internal-links", "false",
               "--offset.disable-normalization", "true", "--xml-validation", "never"]
    baseline.invoke(command, folder, env, physics["binary_timeout_s"], "netconvert.log")
    audit = baseline.audit_network(folder, network)
    return env, command, audit


def simulate(folder: Path, context: dict, physics: dict, event: dict, enabled: bool,
             policy: str, demand: list[dict], env: dict) -> dict:
    traci = traci_module()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [str(baseline.locate_binary("sumo")), "-c", "baseline.sumocfg", "--remote-port", str(port)]
    started = time.monotonic()
    assigned, admissions, submitted, completed, controller_log = {}, {}, set(), set(), []
    source_wait_trace, trajectory = [], []
    next_request = 0
    conn = process = None
    source = folder / "sumo.log"
    with source.open("w", encoding="utf-8") as stream:
        try:
            process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT)
            conn = traci.connect(port=port, host="127.0.0.1", numRetries=20, proc=process, waitBetweenRetries=0.1)
            while conn.simulation.getTime() < physics["simulation_duration_s"]:
                if time.monotonic() - started > physics["binary_timeout_s"]:
                    raise TimeoutError("Local SUMO experiment exceeded its declared wall-time budget")
                now = conn.simulation.getTime()
                while next_request < len(demand) and demand[next_request]["release_s"] <= now:
                    row = demand[next_request]
                    assigned[row["id"]] = choose_route(row, now, context, event, policy, enabled)
                    next_request += 1
                active_ids = set(conn.vehicle.getIDList())
                task_state = None
                task_id = context["config"]["hospital_task"]["id"]
                if task_id in assigned and task_id not in completed:
                    task = assigned[task_id]
                    edges = context["candidates"][task["route"]]["edge_ids"]
                    # TraCI routeIndex remains an external-edge index on internal
                    # junction links. Keeping the current edge is conservative.
                    index = max(0, conn.vehicle.getRouteIndex(task_id)) if task_id in active_ids else 0
                    task_state = {"active": True, "release_s": task["release_s"],
                                  "remaining_edges": edges[index:], "route_index": index,
                                  "visibility": "in_network" if task_id in active_ids else "released_waiting_insertion"}
                waiting = []
                for identity, row in sorted(assigned.items(), key=lambda item: (item[1]["release_s"], item[0])):
                    if identity in submitted:
                        continue
                    route_edges = context["candidates"][row["route"]]["edge_ids"]
                    hold = policy == "B" and should_hold(row, now, task_state, context["config"]["coordination"], route_edges)
                    if hold:
                        waiting.append(identity)
                        if not row.get("hold_started"):
                            row["hold_started"] = True
                            controller_log.append({"time_s": now, "action": "hold_at_source", "vehicle_id": identity,
                                                   "task_state": task_state, "original_release_s": row["release_s"]})
                        continue
                    conn.vehicle.add(identity, row["route"], typeID="assumed_car", depart="now", departLane="0", departSpeed="0")
                    submitted.add(identity)
                    admissions[identity] = now
                    row["admitted_s"] = now
                    row["source_control_wait_s"] = now - row["release_s"]
                    if row.get("hold_started"):
                        controller_log.append({"time_s": now, "action": "release_from_source", "vehicle_id": identity,
                                               "source_control_wait_s": row["source_control_wait_s"]})
                if now % physics["fcd_period_s"] == 0:
                    source_wait_trace.append({"time_s": now, "source_waiting": len(waiting),
                                              "task_active": task_state is not None,
                                              "task_route_index": task_state["route_index"] if task_state else None})
                conn.simulationStep()
                completed.update(conn.simulation.getArrivedIDList())
            conn.close()
            conn = None
            process.wait(timeout=15)
            if process.returncode:
                raise RuntimeError("SUMO exited with a nonzero status; see sumo.log")
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
    dump(folder / "assignments.json", ordered)
    dump(folder / "controller_log.json", controller_log)
    dump(folder / "source_queue_trace.json", source_wait_trace)
    # The realized admission schedule is independently replayable without TraCI.
    replay = ET.parse(folder / "demand.rou.xml").getroot()
    for identity, admitted in sorted(admissions.items(), key=lambda pair: (pair[1], pair[0])):
        ET.SubElement(replay, "vehicle", id=identity, type="assumed_car", route=assigned[identity]["route"],
                      depart=str(admitted), departLane="0", departSpeed="0")
    baseline.write_xml(folder / "realized_admissions.rou.xml", replay)
    return {"assignments": ordered, "admissions": admissions, "commands": [command],
            "controller_action_count": len(controller_log), "submitted_count": len(submitted)}


def collect_metrics(folder: Path, context: dict, physics: dict, result: dict) -> dict:
    assignments = result["assignments"]
    horizon = physics["simulation_duration_s"]
    metrics = baseline.load_metrics(folder, assignments, horizon)
    trips = [dict(e.attrib) for _, e in ET.iterparse(folder / "tripinfo.xml") if e.tag == "tripinfo"]
    by_id = {t["id"]: t for t in trips}
    ordinary = [a for a in assignments if a["role"] == "ordinary"]
    ids = {a["id"] for a in ordinary}
    metrics["ordinary"] = baseline.compute_metrics([t for t in trips if t["id"] in ids], ordinary, horizon)
    metrics["hospital_tasks"] = prior.task_metrics(trips, assignments, horizon)
    metrics["per_od"] = {}
    for od in context["ods"]:
        selected = [a for a in ordinary if a["od_id"] == od]
        selected_ids = {a["id"] for a in selected}
        metrics["per_od"][od] = baseline.compute_metrics([t for t in trips if t["id"] in selected_ids], selected, horizon)
    source_wait = sum(result["admissions"].get(a["id"], horizon) - a["release_s"] for a in ordinary)
    insertion_wait = 0.0
    for a in ordinary:
        admitted = result["admissions"].get(a["id"])
        depart = float(by_id.get(a["id"], {}).get("depart", -1))
        if admitted is not None:
            insertion_wait += (depart if depart >= 0 else horizon) - admitted
    if not math.isclose(source_wait + insertion_wait, metrics["ordinary"]["total_departure_wait_time_s"], abs_tol=1e-6):
        raise ValueError("Source control and SUMO insertion waiting do not reconcile")
    metrics["ordinary_source_control_wait_s"] = source_wait
    metrics["ordinary_sumo_insertion_wait_s"] = insertion_wait
    metrics["maximum_source_control_wait_s"] = max((a.get("source_control_wait_s", 0) for a in ordinary), default=0)
    metrics["source_held_vehicle_count"] = sum(a.get("source_control_wait_s", 0) > 0 for a in ordinary)
    metrics["not_submitted_at_horizon"] = len(assignments) - result["submitted_count"]
    metrics["changed_route_count"] = sum(a["route"] != context["ods"][a["od_id"]]["default_route_id"] for a in assignments)
    metrics["all_waiting_accounted_from_original_release"] = True
    if metrics["maximum_source_control_wait_s"] > context["config"]["coordination"]["maximum_source_hold_s"] + 1e-6:
        metrics["physics_valid"] = False
        metrics["quality_reasons"].append("source_control_bound_exceeded")
    metrics["eligible_for_governance"] = False
    metrics["scope"] = "Assumption-based simulation comparison; no claimed field causal effect or microscopic calibration"
    return metrics


def compact_trajectory(folder: Path, config: dict) -> None:
    frames = []
    for _, step in ET.iterparse(folder / "fcd.xml", events=("end",)):
        if step.tag != "timestep":
            continue
        seconds = float(step.get("time"))
        vehicles = [{"id": v.get("id"), "x": float(v.get("x")), "y": float(v.get("y")),
                     "speed": float(v.get("speed")), "lane": v.get("lane")}
                    for v in step.findall("vehicle")]
        frames.append({"time_s": seconds, "declared_experiment_time": wall_time(config, seconds), "vehicles": vehicles})
        step.clear()
    dump(folder / "trajectory.json", {"clock_status": config["anchor_status"], "frames": frames})


def run_case(context: dict, profile: dict, case: str, family: str, policy: str, folder: Path) -> dict:
    config = context["config"]
    physics = copy.deepcopy(context["physics"])
    physics["departure_interval_s"] = profile["departure_interval_s"]
    event = profile_event(config, profile)
    enabled = family != "S1"
    demand = prior.fixed_demand(context["ods"], physics, config["hospital_task"], family == "S3")
    folder.mkdir(parents=True, exist_ok=False)
    dump(folder / "demand.json", demand)
    dump(folder / "event.json", {**event, "enabled": enabled})
    manifest = {"run_id": folder.name, "case": case, "scenario_family": family, "policy": policy,
                "profile": profile, "status": "running", "started_at": baseline.utc_now(),
                "study_area_id": physics["study_area_id"], "coordinate_system": "bd09ll", "config": physics,
                "experiment_anchor": config["experiment_anchor"], "anchor_status": config["anchor_status"],
                "input_sha256": context["input_hashes"][config["route_catalog"]], "inputs_sha256": context["input_hashes"],
                "demand_sha256": prior.object_digest(demand), "event_sha256": prior.object_digest({**event, "enabled": enabled}),
                "physics_sha256": prior.object_digest(physics), "assumptions": config["assumptions"],
                "eligible_for_governance": False, "real_world_calibrated": False, "commands": []}
    dump(folder / "run_manifest.json", manifest)
    try:
        for source in (Path(__file__), Path(baseline.__file__), Path(prior.__file__)):
            (folder / source.name).write_bytes(source.read_bytes())
        (folder / "protocol.json").write_bytes(context["config_path"].read_bytes())
        manifest["script_sha256"] = baseline.sha256(folder / Path(__file__).name)
        env, build_command, audit = build_inputs(folder, context, physics, event, enabled)
        manifest["commands"].append(build_command)
        manifest["network"] = audit
        manifest["versions"] = {name: baseline.invoke([str(baseline.locate_binary(name)), "--version"], folder, env,
                                                      physics["binary_timeout_s"], name + ".version.log")
                                for name in ("sumo", "netconvert")}
        result = simulate(folder, context, physics, event, enabled, policy, demand, env)
        manifest["commands"].extend(result["commands"])
        metrics = collect_metrics(folder, context, physics, result)
        dump(folder / "metrics.json", metrics)
        compact_trajectory(folder, config)
        prior.verify_inputs(context["input_hashes"])
        manifest.update(status="completed" if metrics["physics_valid"] else "physics_invalid",
                        physics_valid=metrics["physics_valid"], quality_reasons=metrics["quality_reasons"],
                        finished_at=baseline.utc_now(), inputs_unchanged=True)
        manifest["artifacts"] = {p.name: baseline.sha256(p) for p in sorted(folder.iterdir())
                                 if p.is_file() and p.name != "run_manifest.json"}
        dump(folder / "run_manifest.json", manifest)
        return make_row(folder, manifest, metrics)
    except Exception as error:
        manifest.update(status="failed", finished_at=baseline.utc_now(), error=type(error).__name__ + ": " + str(error))
        dump(folder / "run_manifest.json", manifest)
        raise


def make_row(folder: Path, manifest: dict, metrics: dict) -> dict:
    task = metrics["hospital_tasks"][0] if metrics["hospital_tasks"] else {}
    return {"case": manifest["case"], "profile": manifest["profile"]["id"],
            "folder": str(folder.relative_to(ROOT)), "scenario_family": manifest["scenario_family"],
            "policy": manifest["policy"], "event_speed_m_s": manifest["profile"]["event_speed_m_s"] if manifest["scenario_family"] != "S1" else None,
            "event_duration_s": manifest["profile"]["event_duration_s"] if manifest["scenario_family"] != "S1" else 0,
            "departure_interval_s": manifest["profile"]["departure_interval_s"],
            "planned": metrics["planned"], "arrived": metrics["arrived"], "unfinished_total": metrics["unfinished_total"],
            "ordinary_planned": metrics["ordinary"]["planned"], "ordinary_arrived": metrics["ordinary"]["arrived"],
            "ordinary_unfinished": metrics["ordinary"]["unfinished_total"],
            "ordinary_system_time_s": metrics["ordinary"]["total_system_vehicle_time_s"],
            "ordinary_in_network_time_s": metrics["ordinary"]["total_in_network_vehicle_time_s"],
            "ordinary_source_control_wait_s": metrics["ordinary_source_control_wait_s"],
            "ordinary_sumo_insertion_wait_s": metrics["ordinary_sumo_insertion_wait_s"],
            "maximum_source_control_wait_s": metrics["maximum_source_control_wait_s"],
            "source_held_vehicle_count": metrics["source_held_vehicle_count"],
            "task_release_to_arrival_s": task.get("release_to_arrival_s"), "task_completed": task.get("completed"),
            "task_insertion_wait_s": task.get("insertion_wait_s"),
            "changed_route_count": metrics["changed_route_count"],
            "physics_valid": metrics["physics_valid"], **metrics["physics_counters"],
            "demand_sha256": manifest["demand_sha256"], "event_sha256": manifest["event_sha256"],
            "physics_sha256": manifest["physics_sha256"], "run_manifest_sha256": baseline.sha256(folder / "run_manifest.json")}


def comparisons(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if row["policy"] == "fixed":
            continue
        base = next(r for r in rows if r["profile"] == row["profile"] and r["scenario_family"] == row["scenario_family"] and r["policy"] == "fixed")
        for key in ("demand_sha256", "event_sha256", "physics_sha256"):
            if base[key] != row[key]:
                raise ValueError("Paired simulation inputs differ: " + key)
        valid = row["physics_valid"] and base["physics_valid"]
        result.append({"profile": row["profile"], "scenario_family": row["scenario_family"], "policy": row["policy"],
                       "reference_policy": "fixed", "comparison_valid": valid,
                       "ordinary_system_time_delta_s": row["ordinary_system_time_s"] - base["ordinary_system_time_s"] if valid else None,
                       "task_time_delta_s": (row["task_release_to_arrival_s"] - base["task_release_to_arrival_s"])
                       if valid and row["task_completed"] and base["task_completed"] else None})
        if row["policy"] == "B":
            a = next(r for r in rows if r["profile"] == row["profile"] and r["scenario_family"] == "S3" and r["policy"] == "A")
            result.append({"profile": row["profile"], "scenario_family": "S3", "policy": "B", "reference_policy": "A",
                           "comparison_valid": row["physics_valid"] and a["physics_valid"],
                           "ordinary_system_time_delta_s": row["ordinary_system_time_s"] - a["ordinary_system_time_s"] if row["physics_valid"] and a["physics_valid"] else None,
                           "task_time_delta_s": row["task_release_to_arrival_s"] - a["task_release_to_arrival_s"]
                           if row["physics_valid"] and a["physics_valid"] and row["task_completed"] and a["task_completed"] else None})
    return result


def svg_chart(rows: list[dict], path: Path, metric: str, title: str) -> None:
    selected = [r for r in rows if r["scenario_family"] == "S3"]
    maximum = max([float(r[metric] or 0) for r in selected] + [1])
    height = 100 + 34 * len(selected)
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" viewBox="0 0 1100 {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="20" y="30" font-family="sans-serif" font-size="21">{html.escape(title)}</text>',
              '<text x="20" y="55" font-family="sans-serif" font-size="13">Assumed scenarios; all profiles shown; lower is better.</text>']
    colors = {"fixed": "#64748b", "A": "#2563eb", "B": "#0f766e"}
    for i, row in enumerate(selected):
        y = 77 + 34 * i
        value = float(row[metric] or 0)
        chunks.extend([f'<text x="20" y="{y + 17}" font-family="sans-serif" font-size="14">{row["profile"]} / {row["policy"]}</text>',
                       f'<rect x="260" y="{y}" width="{680 * value / maximum:.2f}" height="23" fill="{colors[row["policy"]]}"/>',
                       f'<text x="{270 + 680 * value / maximum:.2f}" y="{y + 17}" font-family="sans-serif" font-size="14">{value:g}</text>'])
    chunks.append('</svg>')
    path.write_text("\n".join(chunks), encoding="utf-8")


def save_summary(context: dict, rows: list[dict], complete: bool) -> dict:
    paired = comparisons(rows)
    summary = {"schema_version": 1, "purpose": context["config"]["purpose"],
               "status": "completed" if complete else "partial_s1_ready", "protocol_sha256": context["input_hashes"][str(context["config_path"].relative_to(ROOT))],
               "inputs_sha256": context["input_hashes"], "experiment_anchor": context["config"]["experiment_anchor"],
               "anchor_status": context["config"]["anchor_status"], "all_physics_valid": all(r["physics_valid"] for r in rows),
               "field_calibrated": False, "runs": rows, "comparisons": paired,
               "updated_at": baseline.utc_now(), "note": "All comparisons are under declared demand/control assumptions; completed-only means never replace unfinished counts."}
    dump(OUT / "summary.json", summary)
    with (OUT / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if complete:
        svg_chart(rows, OUT / "task_times.svg", "task_release_to_arrival_s", "Hospital task: original release to arrival (s)")
        svg_chart(rows, OUT / "ordinary_times.svg", "ordinary_system_time_s", "Ordinary traffic: total system vehicle time (s)")
        write_report(context, summary)
    return summary


def write_report(context: dict, summary: dict) -> None:
    config = context["config"]
    lines = ["# 统一 S1/S2/S3 假设条件仿真实验", "", "本报告描述可复现的仿真实验，不把假设事故、需求和源端控制写成现场事实。", "",
             "固定六个 OD、同一 307 边/310 允许转向网络、同一车辆性能与路口规则。每组保留原始释放时刻，计入源端控制与 SUMO 入网等待；未完成车辆保留。", "",
             "A 在请求发布时按当前已知限速比较完整候选，默认参与、平局保留，不预知事件恢复。B 在任务发布后读取任务当前剩余走廊，对有重叠的尚未入网普通请求作有界源端控制，每车最多45秒，任务控制窗口最多180秒；不提前清场、不删除车辆、不授予救护车额外性能。", "",
             f"声明实验锚点为 {config['experiment_anchor']}，t秒映射为锚点+t秒。这是反事实实验时钟，不是已重建的实测交通过程；事件在600秒发布，任务在720秒发布。", "",
             "主情景和单因素敏感性预先在配置声明，全部报告；跟驰sigma=0，单一固定种子不冒充独立重复。ETA等效观测层另行验证，不代表这些微观参数已由现场数据识别。", "",
             "| 组别 | 普通车辆总系统时间(车·秒) | 普通源端控制等待(车·秒) | 任务发布至到达(秒) | 未完成 | 物理门禁 |",
             "|---|---:|---:|---:|---:|---|"]
    for row in summary["runs"]:
        task = row["task_release_to_arrival_s"]
        lines.append(f"| {row['case']} | {row['ordinary_system_time_s']:.0f} | {row['ordinary_source_control_wait_s']:.0f} | {task if task is not None else '不适用/未完成'} | {row['unfinished_total']} | {'通过' if row['physics_valid'] else '无效'} |")
    lines += ["", "差值定义为策略减对照；负值代表时间下降，正值代表增加。不把假设条件下的改善外推成真实道路治理收益。", "",
              "| 剖面 | 场景 | 策略→参照 | 普通总时间差(车·秒) | 任务时间差(秒) |",
              "|---|---|---|---:|---:|"]
    for row in summary["comparisons"]:
        lines.append(f"| {row['profile']} | {row['scenario_family']} | {row['policy']}−{row['reference_policy']} | {row['ordinary_system_time_delta_s']} | {row['task_time_delta_s']} |")
    lines += ["", "源端控制权限、45秒上限、180秒窗口及事件强度均属研究假设。短时控制可能增加普通车辆成本，甚至不能改善任务时间，结果完整保留。物理门禁只排除已检测的碰撞/传送/丢弃，不等于现场真实性验证。", "",
              "复现：`python3 scripts/run_final_experiments.py --run`。每组保存源代码、协议、固定需求、因果决策/控制记录、实际放行计划、SUMO版本、输入与输出摘要、原始XML和精简轨迹。"]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/final_experiments.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--only-s1", action="store_true", help="Freeze the two no-event demand baselines first")
    args = parser.parse_args(argv)
    if not (args.check or args.run):
        parser.print_help()
        return 0
    try:
        context = load_protocol(baseline.workspace_path(args.config))
        if args.check:
            traci_module()
            print(json.dumps({"status": "inputs_valid", "profiles": len(context["config"]["profiles"]),
                              "demand_ods": len(context["ods"]), "edges": len(context["network"]["edges"]),
                              "connections": len(context["network"]["connections"]), "traci": "bundled_local"}))
            return 0
        OUT.mkdir(parents=True, exist_ok=True)
        protocol_path = OUT / "frozen_protocol.json"
        if protocol_path.exists() and baseline.sha256(protocol_path) != context["input_hashes"][str(context["config_path"].relative_to(ROOT))]:
            raise ValueError("Frozen protocol differs; preserve this output and use a separately reviewed protocol")
        if not protocol_path.exists():
            protocol_path.write_bytes(context["config_path"].read_bytes())
            (OUT / "frozen_route_catalog.json").write_bytes(baseline.workspace_path(context["config"]["route_catalog"]).read_bytes())
        rows = []
        if (OUT / "summary.json").exists():
            previous = baseline.read_json(OUT / "summary.json")
            if previous["inputs_sha256"] != context["input_hashes"]:
                raise ValueError("Input/code changed after frozen runs; explicit audit required before reuse")
            for row in previous["runs"]:
                folder = baseline.workspace_path(row["folder"])
                manifest = baseline.read_json(folder / "run_manifest.json")
                if baseline.sha256(folder / "run_manifest.json") != row["run_manifest_sha256"]:
                    raise ValueError("Frozen run manifest changed")
                for name, digest in manifest["artifacts"].items():
                    if baseline.sha256(folder / name) != digest:
                        raise ValueError("Frozen artifact changed")
            rows = previous["runs"]
        plan = []
        seen_intervals = set()
        for profile in context["config"]["profiles"]:
            interval = profile["departure_interval_s"]
            if interval not in seen_intervals:
                seen_intervals.add(interval)
                plan.append((profile, f"S1_d{interval}", "S1", "fixed"))
        if not args.only_s1:
            for profile in context["config"]["profiles"]:
                plan.extend((profile, profile["id"] + "_" + case, family, policy) for case, family, policy in CASES)
        for profile, case, family, policy in plan:
            if any(row["case"] == case for row in rows):
                continue
            prior.verify_inputs(context["input_hashes"])
            folder = OUT / "runs" / case
            row = run_case(context, profile, case, family, policy, folder)
            rows.append(row)
            save_summary(context, rows, False)
            print(f"{case}: planned={row['planned']} arrived={row['arrived']} physics_valid={row['physics_valid']}", flush=True)
        prior.verify_inputs(context["input_hashes"])
        summary = save_summary(context, rows, not args.only_s1)
        artifact_paths = [OUT / "summary.json", OUT / "summary.csv", protocol_path, OUT / "frozen_route_catalog.json"]
        if not args.only_s1:
            artifact_paths += [OUT / "report.md", OUT / "task_times.svg", OUT / "ordinary_times.svg"]
        dump(OUT / "manifest.json", {"status": summary["status"], "inputs_sha256": context["input_hashes"],
                                    "artifacts": {p.name: baseline.sha256(p) for p in artifact_paths},
                                    "run_count": len(rows), "all_physics_valid": summary["all_physics_valid"],
                                    "inputs_unchanged": True, "updated_at": baseline.utc_now()})
        return 0 if summary["all_physics_valid"] else 2
    except Exception as error:
        print("Final experiment error: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
