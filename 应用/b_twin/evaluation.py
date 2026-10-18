"""Align E1 intervals to physical 15-minute observation windows."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import REFINED

WARMUP = 300
CASES = {"ordinary": ("2026-10-15T11:15:00", 2700), "peak": ("2026-10-15T08:00:00", 3600)}


def aggregate_e1(path, physical_start, duration):
    """Use complete 300-second outputs inside the evaluation period only."""
    rows = []
    t0 = pd.Timestamp(physical_start)
    for element in ET.parse(path).getroot().findall("interval"):
        d = element.attrib
        begin, end = float(d["begin"]), float(d["end"])
        if begin < WARMUP or end > WARMUP + duration:
            continue
        if end <= begin or int((begin - WARMUP) // 900) != int((end - WARMUP - 1e-6) // 900):
            raise ValueError("E1 interval crosses a physical 15-minute boundary")
        count, speed = int(d["nVehContrib"]), float(d["speed"])
        rows.append({"detector_id": d["id"].split("__")[0], "lane_loop": d["id"],
            "window": (t0 + pd.Timedelta(seconds=(begin - WARMUP) // 900 * 900)).isoformat(),
            "begin": begin, "end": end, "vehicles": count,
            "occupancy_pct": float(d.get("occupancy", "nan")),
            "speed_numerator": speed * 3.6 * count if count and speed >= 0 else 0.})
    intervals = pd.DataFrame(rows)
    if intervals.empty:
        raise ValueError("No post-warmup E1 data")
    aggregated = []
    for (did, window), g in intervals.groupby(["detector_id", "window"]):
        periods = g[["begin", "end"]].drop_duplicates()
        seconds = (periods.end - periods.begin).sum()
        if seconds != 900:
            raise ValueError(f"Incomplete simulated window: {did}/{window}/{seconds}s")
        for _, lane in g.groupby("lane_loop"):
            if (lane.end - lane.begin).sum() != 900:
                raise ValueError("Incomplete simulated lane coverage")
        count = int(g.vehicles.sum())
        aggregated.append({"detector_id": did, "window": window, "simulated_count": count,
            "simulated_flow_vph": count * 4,
            "simulated_occupancy_pct": float(np.average(g.occupancy_pct, weights=g.end - g.begin)),
            "simulated_speed_kmh": g.speed_numerator.sum() / count if count else np.nan})
    return intervals, pd.DataFrame(aggregated)


def vehicle_accounting(summary_path, horizon, step, planned=None):
    """Count the last step before the evaluation horizon separately from clearance."""
    records = [dict(e.attrib) for e in ET.parse(summary_path).getroot().findall("step")]
    frame = pd.DataFrame(records).apply(pd.to_numeric)
    evaluation = frame[(frame.time >= WARMUP) & (frame.time < horizon)]
    before = frame[frame.time < horizon]
    if before.empty or evaluation.empty:
        raise ValueError("Missing evaluation-horizon summary")
    at_end, final = before.iloc[-1], frame.iloc[-1]
    def counts(row):
        return {"loaded": int(row.loaded), "inserted": int(row.inserted), "arrived": int(row.arrived),
            "running": int(row.running), "pending": int(row.waiting), "discarded": int(row.get("discarded", 0)),
            "loaded_not_yet_attempted": int(row.loaded - row.inserted - row.waiting - row.get("discarded", 0)),
            "halting": int(row.halting), "last_step_seconds": float(row.time)}
    result = {"evaluation_end": counts(at_end), "after_clearance": counts(final),
        "peak_running_during_evaluation": int(evaluation.running.max()),
        "peak_pending_during_evaluation": int(evaluation.waiting.max()),
        "peak_halting_during_evaluation": int(evaluation.halting.max()),
        "evaluation_running_vehicle_seconds": float(evaluation.running.sum() * step),
        "evaluation_pending_vehicle_seconds": float(evaluation.waiting.sum() * step),
        "evaluation_halting_vehicle_seconds": float(evaluation.halting.sum() * step)}
    if planned is not None:
        # A vehicle scheduled in the last fractional step may be loaded but
        # not attempted yet, especially when clearance=0. Keep it unfinished.
        not_attempted = int(final.loaded - final.inserted - final.waiting - final.get("discarded", 0))
        accounted = int(final.arrived + final.running + final.waiting + final.get("discarded", 0) + not_attempted)
        result.update(planned_vehicles=planned, final_accounted_vehicles=accounted,
            conservation_residual=planned - accounted, all_departures_accounted_for=planned == accounted)
        if planned != accounted:
            raise ValueError(f"Fixed demand conservation mismatch: planned={planned}, accounted={accounted}")
    return result


def trip_accounting(run_dir, horizon, end, save_dir=None):
    """Include all planned synthetic agents, also those inserted during clearance."""
    vehicles_file = run_dir / "vehicles.csv"
    if not vehicles_file.exists():
        return None
    vehicles = pd.read_csv(vehicles_file)
    trips = pd.DataFrame([e.attrib for e in ET.parse(run_dir / "tripinfo.xml").getroot().findall("tripinfo")])
    if trips.empty or not trips.id.is_unique:
        raise ValueError("Missing or duplicate tripinfo agents")
    for col in ["depart", "arrival", "duration", "departDelay", "waitingTime", "timeLoss"]:
        trips[col] = pd.to_numeric(trips[col])
    data = vehicles.merge(trips, left_on="vehicle_id", right_on="id", how="left",
        validate="one_to_one", suffixes=("_intended", "_actual"))
    if data.id.isna().any() or len(trips) != len(vehicles):
        raise ValueError("Tripinfo does not account for every planned vehicle")
    data["arrived_before_horizon"] = data.arrival.ge(0) & data.arrival.lt(horizon)
    data["pending_at_horizon"] = data.depart_actual.lt(0) | data.depart_actual.ge(horizon)
    data["running_at_horizon"] = ~data.pending_at_horizon & ~data.arrived_before_horizon
    data["arrived_after_clearance"] = data.arrival.ge(0)
    data["experienced_time_lower_bound_s"] = np.where(data.arrival.ge(0), data.arrival, end) - data.depart_intended
    data["departure_delay_lower_bound_s"] = np.where(data.depart_actual.ge(0), data.depart_actual, end) - data.depart_intended
    by_source = data.groupby("origin_edge").agg(planned=("id", "size"),
        arrived_before_horizon=("arrived_before_horizon", "sum"), pending_at_horizon=("pending_at_horizon", "sum"),
        running_at_horizon=("running_at_horizon", "sum"), arrived_after_clearance=("arrived_after_clearance", "sum"),
        mean_departure_delay_lower_bound_s=("departure_delay_lower_bound_s", "mean"),
        max_departure_delay_lower_bound_s=("departure_delay_lower_bound_s", "max"),
        mean_experienced_time_lower_bound_s=("experienced_time_lower_bound_s", "mean"))
    by_source.to_csv((save_dir or run_dir) / "source_accounting.csv", encoding="utf-8-sig")
    result = {"planned": len(data), "arrived_before_horizon": int(data.arrived_before_horizon.sum()),
        "pending_at_horizon": int(data.pending_at_horizon.sum()), "running_at_horizon": int(data.running_at_horizon.sum()),
        "arrived_after_clearance": int(data.arrived_after_clearance.sum()),
        "unfinished_after_clearance": int((~data.arrived_after_clearance).sum()),
        "mean_departure_delay_lower_bound_s": float(data.departure_delay_lower_bound_s.mean()),
        "mean_experienced_time_lower_bound_s": float(data.experienced_time_lower_bound_s.mean()),
        "note": "All intended departures included, including warmup; actual insertion/arrival timestamps differ from last-step summary by up to one step. Unfinished trip times are censored lower bounds."}
    if data.arrived_after_clearance.all():
        result["mean_departure_delay_s_all_agents"] = result["mean_departure_delay_lower_bound_s"]
        result["mean_experienced_time_s_all_agents"] = result["mean_experienced_time_lower_bound_s"]
    return result


def primary_metrics(compared):
    g = compared[compared.used_in_primary_metrics]
    speed = g[g.simulated_speed_kmh.notna() & g.speed_kmh_observed.notna()]
    # The 30-km/h threshold is an engineering diagnostic, not an official label.
    observed_slow, simulated_slow = g.speed_kmh_observed.lt(30), g.simulated_speed_kmh.lt(30)
    return {"basis": "Fixed original core station set, eligible windows and unflagged mappings; calibration day only",
        "station_windows": len(g), "stations": int(g.detector_id.nunique()),
        "flow_wmape_pct": float(100 * g.flow_absolute_error.sum() / g.flow_vph_estimated.sum()),
        "speed_mae_kmh": float(speed.speed_absolute_error_kmh.mean()),
        "occupancy_mae_percentage_points": float(abs(g.simulated_occupancy_pct - g.occupancy_pct_observed).mean()),
        "congestion_threshold_kmh_assumed": 30,
        "observed_slow_station_windows": int(observed_slow.sum()),
        "reproduced_slow_station_windows": int((observed_slow & simulated_slow).sum()),
        "missed_slow_station_windows": int((observed_slow & ~simulated_slow).sum()),
        "false_slow_station_windows": int((~observed_slow & simulated_slow).sum()),
        "speed_missing_simulated_windows": int(g.simulated_speed_kmh.isna().sum())}


def evaluate(run_dir, case, metadata=None):
    start, duration = CASES[case]
    intervals, simulated = aggregate_e1(run_dir / "e1.xml", start, duration)
    intervals.to_csv(run_dir / "e1_intervals.csv", index=False, encoding="utf-8-sig")
    simulated.to_csv(run_dir / "simulated_15min.csv", index=False, encoding="utf-8-sig")
    observed = pd.read_csv(REFINED / "traffic_15min_estimates.csv")
    compared = simulated.merge(observed, on=["detector_id", "window"], how="left", validate="one_to_one")
    compared["used_in_metrics"] = compared.eligible.fillna(False).astype(bool)
    mapping = pd.read_csv(REFINED / "detector_mapping.csv")
    compared = compared.merge(mapping[["detector_id", "requires_review"]], on="detector_id", validate="many_to_one")
    core = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))["core_edges"]
    primary_ids = set(mapping[mapping.sumo_edge.astype(str).isin(core)].detector_id)
    compared["reference_core_station"] = compared.detector_id.isin(primary_ids)
    compared["used_in_primary_metrics"] = compared.used_in_metrics & ~compared.requires_review.astype(bool) & compared.reference_core_station
    compared["flow_absolute_error"] = abs(compared.simulated_flow_vph - compared.flow_vph_estimated)
    compared["speed_absolute_error_kmh"] = abs(compared.simulated_speed_kmh - compared.speed_kmh_observed)
    compared.to_csv(run_dir / "comparison_15min.csv", index=False, encoding="utf-8-sig")
    g = compared[compared.used_in_metrics]
    stats = ET.parse(run_dir / "statistics.xml").getroot()
    vehicles = {k: int(v) for k, v in stats.find("vehicles").attrib.items()}
    metadata = metadata or {}
    accounting = vehicle_accounting(run_dir / "summary.xml", WARMUP + duration,
        metadata.get("step_seconds", .5), metadata.get("planned_vehicles"))
    trips = trip_accounting(run_dir, WARMUP + duration,
        metadata.get("simulation_end_seconds", WARMUP + duration))
    metrics = {"case": case, "physical_start": start, "duration_seconds": duration,
        "warmup_excluded_seconds": WARMUP, "comparison_basis": "Matched eligible physical 15-minute windows",
        "observed_role": "Calibration-day counts/speeds; not independent validation",
        "compared_station_windows": len(g), "compared_stations": int(g.detector_id.nunique()),
        "flow_wmape_pct": float(100 * g.flow_absolute_error.sum() / g.flow_vph_estimated.sum()),
        "speed_mae_kmh": float(g.speed_absolute_error_kmh.mean()),
        "primary_metrics": primary_metrics(compared),
        "accounting": accounting,
        "trip_accounting": trips,
        "vehicles": vehicles, "teleports": int(stats.find("teleports").get("total")),
        "collisions": int(stats.find("safety").get("collisions")),
        "traffic_baseline_validated": False}
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics
