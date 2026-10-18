"""Causal observation-to-demand updates; no retrospective vehicle edits.

AI-assisted: OpenAI Codex; record actual tool use and human review before submission.
Archive member timestamps are conservative replay availability proxies, not
measurements of when this machine received historical observations.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP, DATA, INPUTS, REFINED, sumo_exe
from .live import TZ, LIVE, collect_once, parse_snapshot
from .demand import fixed_vehicles, invert_quarter
from .evaluation import CASES, WARMUP, evaluate
from .runner import prepare_run, write_xml, digest
import sumolib

PROTOCOL = {
    "version": "causal_demand_v1", "update_seconds": 300,
    "lookback_seconds": 900, "minimum_coverage": .8,
    "stale_seconds": 600, "maximum_step_fraction": .25,
    "minimum_change_reference_vph": 10., "entry_ridge": .5,
    "clearance_seconds": 900, "seed": 20261022,
    "network": "through", "background_vph": 0, "supply_vph": 0,
    "prior": "frozen_day15_whole_case_route_mean_NOT_causal_or_independent",
}


def stamp(value):
    value = pd.Timestamp(value)
    return value.tz_localize(TZ) if value.tzinfo is None else value.tz_convert(TZ)


def expected_lanes():
    data = pd.read_csv(DATA / "processed/traffic_lane_30s.csv.gz",
                       usecols=["detector_id", "lane_id"])
    return data.groupby("detector_id").lane_id.unique().to_dict()


def core_mapping():
    mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    core = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))["core_edges"]
    return mapping[mapping.sumo_edge.isin(core)].copy()


def archive_observations(case):
    """Retain the final archived copy only after its recorded capture time.

    The old extractor kept the last duplicate, including corrections. Delaying
    that retained value until its own capture avoids exposing a later revision
    early; the discarded revision history cannot be reconstructed here.
    """
    data = pd.read_csv(DATA / "processed/traffic_lane_30s.csv.gz")
    start, duration = CASES[case]
    left = (pd.Timestamp(start) - pd.Timedelta(hours=1)).isoformat()
    right = (pd.Timestamp(start) + pd.Timedelta(seconds=duration)).isoformat()
    lanes = expected_lanes()
    data = data[data.period_from.ge(left) & data.period_to.le(right)]
    rows = []
    for (did, begin, end), group in data.groupby(["detector_id", "period_from", "period_to"]):
        valid = (set(group.lane_id) == set(lanes[did]) and not group.lane_id.duplicated().any()
            and group.valid_flag.eq("Y").all() and group.interval_seconds.eq(30).all()
            and group.vehicle_count.ge(0).all() and group.speed_kmh.ge(0).all()
            and group.occupancy_pct.between(0, 100).all()
            and np.isfinite(group[["vehicle_count", "speed_kmh", "occupancy_pct"]]).all().all())
        available = stamp(group.archive_capture_time.max())
        stop = stamp(end)
        rows.append({"detector_id": did, "observation_start": stamp(begin).isoformat(),
            "observation_end": stop.isoformat(), "available_at": available.isoformat(),
            "quality": "valid" if valid and available >= stop else "invalid_or_incomplete",
            "count": float(group.vehicle_count.sum()) if valid else None,
            "availability_source": "official_archive_member_capture_proxy_NOT_actual_local_receipt"})
    return pd.DataFrame(rows)


def counts_at(observations, decision, mapping, protocol=PROTOCOL):
    """Select only already-available, complete lane periods; missing is not zero."""
    now = stamp(decision)
    known = observations.copy()
    available = pd.to_datetime(known.available_at, utc=True)
    ends = pd.to_datetime(known.observation_end, utc=True)
    known = known[(available <= now) & (ends <= now) & known.quality.eq("valid")].copy()
    # Corrections, if provided by a source, become visible only at their receipt.
    known = known.sort_values("available_at").drop_duplicates(
        ["detector_id", "observation_start", "observation_end"], keep="last")
    anchor = stamp(known.observation_end.max()) if len(known) else now
    left = anchor - pd.Timedelta(seconds=protocol["lookback_seconds"])
    known = known[(pd.to_datetime(known.observation_start, utc=True) >= left)
                  & (pd.to_datetime(known.observation_end, utc=True) <= anchor)]
    rows = []
    for site in mapping.itertuples():
        group = known[known.detector_id.eq(site.detector_id)]
        seconds = len(group) * 30
        coverage = seconds / protocol["lookback_seconds"]
        age = (now - stamp(group.observation_end.max())).total_seconds() if len(group) else None
        count = float(group['count'].sum()) if len(group) else None
        eligible = coverage >= protocol["minimum_coverage"] and age is not None and age <= protocol["stale_seconds"]
        rate = count * 3600 / seconds if seconds else None
        sd = np.sqrt(count) * 3600 / seconds if seconds else None
        rows.append({"detector_id": site.detector_id, "window": now.isoformat(),
            "eligible": bool(eligible), "flow_vph_estimated": rate,
            "conditional_flow_ci95_low": max(0, rate - 1.96 * sd) if rate is not None else None,
            "conditional_flow_ci95_high": rate + 1.96 * sd if rate is not None else None,
            "coverage": coverage, "valid_periods": len(group), "count": count,
            "last_observation_end": group.observation_end.max() if len(group) else None,
            "latest_available_at": group.available_at.max() if len(group) else None,
            "age_seconds": age, "rolling_window_end": anchor.isoformat(),
            "availability_source": group.availability_source.iloc[-1] if len(group) else "no_usable_observation",
            "exclusion": "mapping_requires_review" if site.requires_review else
                "missing_or_low_coverage" if coverage < protocol["minimum_coverage"] else
                "stale" if age > protocol["stale_seconds"] else "eligible"})
    return pd.DataFrame(rows)


def update_rates(routes, counts, mapping, previous, protocol=PROTOCOL):
    prior = routes.estimated_vph.to_numpy(dtype=float)
    entry_ridge = protocol["entry_ridge"] if (~routes.source_mainline.astype(bool)).any() else 0.
    fitted, selected, info = invert_quarter(routes, counts, mapping, counts.iloc[0].window,
        prior, previous, entry_ridge)
    if not info["constraints"]:
        # No evidence: preserve the last admitted plan, not a fictitious zero.
        return previous.copy(), selected, dict(info, evidence="hold_last_plan_NO_fresh_count_evidence", clipped_routes=0)
    reference = np.maximum.reduce([previous, prior, np.full(len(prior), protocol["minimum_change_reference_vph"])])
    delta = protocol["maximum_step_fraction"] * reference
    admitted = np.clip(fitted, np.maximum(0, previous - delta), previous + delta)
    info.update(evidence="causal_available_counts_regularized_future_demand", clipped_routes=int(np.count_nonzero(abs(admitted - fitted) > 1e-6)))
    return admitted, selected, info


def live_plan():
    """One real acquisition and future-plan preview; sparse feed never fakes coverage."""
    record = collect_once()
    if not record.get("success"):
        return {"success": False, "collection": record, "action": "retain_existing_plan"}
    import gzip
    expected = expected_lanes()
    observations = []
    # Every payload is parsed with its real historical receipt, not today's time.
    for line in (LIVE / "collection.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if not item.get("success"):
            continue
        received = datetime.fromisoformat(item["received_at"])
        if (stamp(record["received_at"]) - stamp(received)).total_seconds() > 3600:
            continue
        payload = gzip.decompress((LIVE / item["file"]).read_bytes())
        assert hashlib.sha256(payload).hexdigest() == item["sha256_uncompressed"]
        for row in parse_snapshot(payload, expected, received):
            observations.append({"detector_id": row["detector_id"],
                "observation_start": row["observation_start"], "observation_end": row["observation_end"],
                "available_at": row["received_at"], "quality": row["quality"],
                "count": row["observed_count_30s"], "availability_source": "actual_local_http_receipt"})
    data = pd.DataFrame(observations)
    mapping = core_mapping()
    decision = stamp(record["received_at"])
    counts = counts_at(data, decision, mapping)
    # Daytime whole-case prior is explicitly a prototype fallback at night.
    routes = pd.read_csv(INPUTS / "ordinary_inferred_routes.csv")
    rates, selected, info = update_rates(routes, counts, mapping, routes.estimated_vph.to_numpy())
    output = LIVE / ("plan_" + decision.strftime("%Y%m%d_%H%M%S_%f"))
    output.mkdir(exist_ok=False)
    counts.to_csv(output / "observations.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"route_id": routes.route_id, "estimated_vph": rates}).to_csv(output / "future_rates.csv", index=False)
    result = {"success": True, "decision_at": decision.isoformat(), "collection": record,
        "usable_constraints": info["constraints"], "action": info["evidence"],
        "mode": "live_acquisition_and_plan_preview_NOT_running_live_SUMO",
        "nighttime_or_other_period_prior_verified": False,
        "note": "Preview uses a day-15 ordinary mean fallback; this is NOT a verified current demand or live traffic twin.",
        "source_snapshot_rows": len(data), "plan_directory": output.relative_to(APP).as_posix()}
    (output / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def execute_assimilation(case, policy, run_id, extra_delay=0, dropout=False):
    """Run one event-time replay with actual TraCI future-vehicle insertion.

    Prior-vs-updated inputs are DIFFERENT demand hypotheses, not a causal
    comparison of road governance benefits. No observed speed is forced.
    """
    if policy not in {"prior", "rolling"} or not np.isfinite(extra_delay) or extra_delay < 0:
        raise ValueError("Invalid assimilation policy or delay")
    output, metadata = prepare_run(case, "through", PROTOCOL["seed"], run_id,
        "fixedmean", PROTOCOL["clearance_seconds"])
    # Historical preparation artifacts are explicitly retained as templates.
    for filename in ["vehicles.csv", "demand_segments.csv", "demand_constraints.csv", "demand_manifest.json", "demand.rou.xml"]:
        (output / filename).rename(output / ("template_" + filename))
    observations = archive_observations(case)
    observations["available_at"] = (pd.to_datetime(observations.available_at, utc=True)
        + pd.Timedelta(seconds=extra_delay)).dt.tz_convert(TZ).map(lambda t:t.isoformat())
    start, duration = CASES[case]
    physical_origin = stamp(start) - pd.Timedelta(seconds=WARMUP)
    dropout_at = stamp(start) + pd.Timedelta(seconds=1200)
    if dropout:
        observations = observations[pd.to_datetime(observations.available_at, utc=True) < dropout_at].copy()
    observations.to_csv(output / "observation_stream.csv", index=False, encoding="utf-8-sig")
    routes = pd.read_csv(INPUTS / f"{case}_inferred_routes.csv")
    fleet = pd.read_csv(INPUTS / "vehicle_class_priors.csv")
    fleet = fleet[fleet.case.eq(case)]
    mapping = core_mapping()
    net = sumolib.net.readNet(str(output / "network.net.xml"))
    original = sumolib.net.readNet(str(REFINED / "corridor.net.xml"))
    template = ET.parse(INPUTS / f"{case}_diagnostic.rou.xml").getroot()
    demand = ET.Element("routes")
    for node in template.findall("vType"):
        demand.append(node)
    definition = json.loads((output / "network_definition.json").read_text(encoding="utf-8"))
    for route in routes.itertuples():
        edges = route.edges.split()
        edges += definition["route_continuations"].get(edges[-1], [])
        ET.SubElement(demand, "route", id=route.route_id, edges=" ".join(edges))
    write_xml(demand, output / "demand.rou.xml")
    cfg = ET.parse(output / "run.sumocfg")
    ET.SubElement(ET.SubElement(cfg.getroot(), "processing"), "time-to-teleport", value="-1")
    write_xml(cfg.getroot(), output / "run.sumocfg")
    metadata.update(assimilation_policy=policy, assimilation_protocol=PROTOCOL,
        extra_receipt_delay_seconds=extra_delay, observation_dropout_after=dropout_at.isoformat() if dropout else None,
        mode="historical_causal_observation_replay_NOT_wallclock_live",
        demand_mode="causal_rolling_5min" if policy=="rolling" else "frozen_case_mean_prior",
        planned_vehicles=None, teleport_timeout_seconds=-1,
        demand_sha256=digest(output / "demand.rou.xml"),
        observation_stream_sha256=digest(output / "observation_stream.csv"),
        limitations=["Archive capture is replay availability proxy, not this machine's historical receipt.",
            "Retained corrected rows are withheld until their own capture; discarded earlier revisions unavailable.",
            "Whole-case day15 prior already uses same-day evaluation information; this is NOT independent causal forecasting.",
            "Only incremental updates obey availability; inverse route counts remain nonunique and ignore storage lag.",
            "Static and rolling demand differ: do NOT claim their cost difference is a governance benefit.",
            "No live road-control actuation, measured initial queue, or validated high-peak baseline."])
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    monitored = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    monitored = monitored[monitored.sumo_edge.map(net.hasEdge)]
    previous = routes.estimated_vph.to_numpy(dtype=float)
    vehicles, segments, decisions, created_types = [], [], [], set()
    connection = None
    started = time.perf_counter()
    try:
        import traci
        traci.start([sumo_exe(), "-c", str(output / "run.sumocfg"), "--log", str(output / "sumo.log"),
                     "--error-log", str(output / "warnings.log")], label=run_id)
        connection = traci.getConnection(run_id)
        next_sample, next_update, slot = 0., 0., 0
        last_decision = None
        horizon = WARMUP + duration
        with (output / "states_60s.jsonl").open("w", encoding="utf-8") as states, \
             (output / "update_decisions.jsonl").open("w", encoding="utf-8") as logs:
            while connection.simulation.getTime() < metadata["simulation_end_seconds"]:
                now = connection.simulation.getTime()
                if now >= next_update and now < horizon:
                    decision = physical_origin + pd.Timedelta(seconds=now)
                    counts = counts_at(observations, decision, mapping)
                    inferred, selected, info = update_rates(routes, counts, mapping, previous)
                    rates = inferred if policy == "rolling" else routes.estimated_vph.to_numpy(dtype=float)
                    future_end = min(now + PROTOCOL["update_seconds"], horizon)
                    frame = pd.DataFrame([{"route_id": route.route_id, "direction": route.direction,
                        "source_window": decision.isoformat(), "begin": now, "end": future_end,
                        "phase": "warmup" if now < WARMUP else "evaluation", "estimated_vph": float(rate),
                        "evidence": info["evidence"] if policy == "rolling" else "frozen_prior"}
                        for route,rate in zip(routes.itertuples(), rates)])
                    agents, _ = fixed_vehicles(routes, frame, fleet, original, PROTOCOL["seed"] + slot, case)
                    agents["vehicle_id"] = f"update{slot:03d}_" + agents.vehicle_id
                    agents["planned_at"] = decision.isoformat()
                    # An add schedules a future departure; queued or active agents
                    # from older decisions are never removed or re-planned.
                    for row in agents.itertuples():
                        tid = f"stream_{row.base_type}_{int(round(row.speed_factor_assumed*1000))}"
                        if tid not in created_types:
                            connection.vehicletype.copy(row.base_type, tid)
                            connection.vehicletype.setSpeedFactor(tid, row.speed_factor_assumed)
                            connection.vehicletype.setSpeedDeviation(tid, 0.)
                            created_types.add(tid)
                        connection.vehicle.add(row.vehicle_id, row.route_id, typeID=tid,
                            depart=str(row.depart), departLane=row.depart_lane, departSpeed=row.depart_speed)
                    selected.to_csv(output / f"constraints_{slot:03d}.csv", index=False, encoding="utf-8-sig")
                    used = selected[selected.constraint_used]
                    last_decision = {"slot": slot, "simulation_seconds": now, "decision_at": decision.isoformat(),
                        "effective_begin_seconds": now, "effective_end_seconds": future_end,
                        "policy": policy, "constraints": info["constraints"], "clipped_routes": info["clipped_routes"],
                        "action": info["evidence"] if policy=="rolling" else "frozen_prior",
                        "latest_used_available_at": used.latest_available_at.max() if len(used) else None,
                        "latest_used_observation_end": used.last_observation_end.max() if len(used) else None,
                        "oldest_used_age_seconds": float(used.age_seconds.max()) if len(used) else None,
                        "future_vehicles_added": len(agents), "admitted_boundary_vph": float(rates.sum()),
                        "count_inverse_rank": info["rank"], "route_count": len(routes),
                        "used_rows_sha256": digest(output / f"constraints_{slot:03d}.csv")}
                    logs.write(json.dumps(last_decision, ensure_ascii=False) + "\n")
                    logs.flush()
                    decisions.append(last_decision)
                    vehicles.append(agents)
                    segments.append(frame)
                    previous = rates.copy()
                    next_update, slot = future_end, slot + 1
                connection.simulationStep()
                now = connection.simulation.getTime()
                if now >= next_sample:
                    state = {"simulation_seconds": now, "physical_time": (physical_origin + pd.Timedelta(seconds=now)).isoformat(),
                        "mode": metadata["mode"], "phase": "warmup" if now<WARMUP else "evaluation" if now<horizon else "clearance",
                        "running": connection.vehicle.getIDCount(), "pending": len(connection.simulation.getPendingVehicles()),
                        "assimilation": last_decision, "edges": []}
                    for row in monitored.itertuples():
                        edge = net.getEdge(row.sumo_edge)
                        lon,lat = net.convertXY2LonLat(*edge.getShape()[len(edge.getShape())//2])
                        state["edges"].append({"detector_id":row.detector_id, "edge_id":row.sumo_edge,
                            "longitude":lon, "latitude":lat,
                            "speed_kmh":3.6*connection.edge.getLastStepMeanSpeed(row.sumo_edge),
                            "vehicles":connection.edge.getLastStepVehicleNumber(row.sumo_edge),
                            "halting_vehicles":connection.edge.getLastStepHaltingNumber(row.sumo_edge),
                            "mapping_requires_review":bool(row.requires_review), "source":"simulated_edge_state_NOT_observed"})
                    states.write(json.dumps(state, ensure_ascii=False)+"\n")
                    states.flush()
                    next_sample=(int(now//60)+1)*60
        connection.close()
        connection=None
        all_agents=pd.concat(vehicles, ignore_index=True).sort_values(["depart","vehicle_id"])
        all_agents.to_csv(output/"vehicles.csv",index=False,encoding="utf-8-sig")
        pd.concat(segments,ignore_index=True).to_csv(output/"demand_segments.csv",index=False,encoding="utf-8-sig")
        realized=ET.fromstring(ET.tostring(demand))
        base_types={node.get('id'):node.attrib for node in template.findall('vType')}
        added=set()
        for agent in all_agents.itertuples():
            tid=f"stream_{agent.base_type}_{int(round(agent.speed_factor_assumed*1000))}"
            if tid not in added:
                ET.SubElement(realized,'vType',**dict(base_types[agent.base_type],id=tid,
                    speedFactor=str(agent.speed_factor_assumed),speedDev='0'))
                added.add(tid)
        # SUMO requires all type/route definitions before the departure list.
        ordered=ET.Element('routes')
        for tag in ['vType','route']:
            for node in realized.findall(tag):
                ordered.append(node)
        for agent in all_agents.itertuples():
            ET.SubElement(ordered,'vehicle',id=agent.vehicle_id,route=agent.route_id,
                type=f"stream_{agent.base_type}_{int(round(agent.speed_factor_assumed*1000))}",
                depart=str(agent.depart),departLane=agent.depart_lane,departSpeed=agent.depart_speed)
        write_xml(ordered,output/'realized_demand.rou.xml')
        replay=ET.parse(output/'run.sumocfg').getroot()
        replay.find('input/route-files').set('value','realized_demand.rou.xml')
        replay.find('input/additional-files').set('value','replay_detectors.add.xml')
        replay_detectors=ET.parse(output/'detectors.add.xml').getroot()
        for node in replay_detectors:
            if node.get('file'):
                node.set('file','replay_'+node.get('file'))
        write_xml(replay_detectors,output/'replay_detectors.add.xml')
        # A replay must not overwrite the frozen experiment's evidence.
        for node in replay.find('output'):
            if node.get('value','').endswith('.xml'):
                node.set('value','replay_'+node.get('value'))
        write_xml(replay,output/'replay.sumocfg')
        metadata.update(planned_vehicles=len(all_agents), vehicle_manifest_sha256=digest(output/"vehicles.csv"),
            realized_demand_sha256=digest(output/'realized_demand.rou.xml'),
            update_count=len(decisions), backend="traci", elapsed_seconds=time.perf_counter()-started)
        metrics=evaluate(output,case,metadata)
        metadata["status"]="completed"
    except Exception as exc:
        metadata.update(status="failed", error=str(exc), elapsed_seconds=time.perf_counter()-started)
        raise
    finally:
        if connection is not None:
            connection.close()
        (output/"run.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"run_dir":str(output),"updates":len(decisions),**metrics}
