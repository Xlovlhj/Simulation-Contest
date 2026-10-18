#!/usr/bin/env python3
"""Training-only, profiled SUMO inverse estimation. No ETA output correction.

AI-assisted implementation, 2026-10-18; substantive team review is required.
Run freeze, fit, then evaluate. Evaluation never updates fitted parameters.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
try:
    from . import sumo_inverse_engine as engine
except ImportError:
    import sumo_inverse_engine as engine

ROOT = engine.ROOT
OUT = ROOT / "outputs/identification_v2/calibration"
CONFIG = ROOT / "config/inverse_estimation_v2.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze():
    if CONFIG.exists():
        raise ValueError("Protocol already frozen; preserve it and use a new version for changes")
    observations = ROOT / "data/processed/identification_v2"
    summary = read(observations / "training_summary.json")
    catalogue = read(ROOT / "data/processed/baidu/route_catalog_v2.json")
    groups = {"ruijin": [], "jianguo_west": [], "other_corridors": []}
    for edge in catalogue["network"]["edges"]:
        group = ("ruijin" if "瑞金二路" in edge["road_names"] else
                 "jianguo_west" if "建国西路" in edge["road_names"] else "other_corridors")
        groups[group].append(edge["id"])
    ods = sorted(x["id"] for x in catalogue["demand_ods"])
    protocol = engine.normalize_protocol({
        "route_catalog": "data/processed/baidu/route_catalog_v2.json",
        "warmup_s": 1200, "probe_offsets_s": [0, 120, 240],
        "probe_od_offsets_s": {od: 7 * i for i, od in enumerate(ods)},
        "background_start_offsets_s": {od: 3 * i for i, od in enumerate(ods)},
        "background_end_s": 2400, "simulation_end_s": 4800,
        "edge_speed_groups": groups,
        "maximum_probe_insertion_delay_s": 20,
    })
    sources = ["data/processed/identification_v2/training_summary.json",
               "data/processed/identification_v2/manifest.json",
               "data/processed/identification_v2/routes.csv",
               "data/processed/baidu/route_catalog_v2.json", "config/baseline.json",
               "scripts/sumo_inverse_engine.py", "scripts/calibrate_inverse_v2.py",
               "scripts/normalize_network_v2.py",
               "research/parameter_audit/downloads/ruijin_hospital_eia_public.pdf"]
    config = {
        "version": 2, "frozen_at": engine.baseline.utc_now(),
        "purpose": "Conditional inverse estimation of effective operating impedance; no affine output correction",
        "engine_protocol": protocol,
        "target_median_eta_s": {od: x["eta_s"]["median"] for od, x in summary["route_eta"].items()},
        "target_training_iqr_s": {od: x["eta_s"]["p75"] - x["eta_s"]["p25"] for od, x in summary["route_eta"].items()},
        "target_phase": "training", "training_route_records": 48,
        "objective": "Mean squared relative error to four training-route medians, on direct in-network probe time",
        "speed_factor_bounds": [0.08, 1.0],
        "bound_interpretation": "Broad engineering search bounds, 2.4–30 km/h effective constraint; not a measured speed-limit interval",
        "demand_profiles": [
            {"id": "headway_60", "headway_s": 60},
            {"id": "headway_30", "headway_s": 30},
            {"id": "historical_scale_18", "headway_s": 18},
            {"id": "headway_15", "headway_s": 15},
        ],
        "demand_interpretation": "Conditional synthetic equal-OD demand grid (60/120/200/240 veh/h per OD), NOT measured or uniquely estimated real flow",
        "historical_scale_reference": {
            "source": "research/parameter_audit/downloads/ruijin_hospital_eia_public.pdf",
            "pdf_page": 110, "printed_page": 106, "table": 53,
            "context_monitoring_dates": ["2025-09-09", "2025-09-10"],
            "ruijin_day_counts_per_20min": {"large": 7, "medium": 32, "small": 168},
            "ruijin_day_hourly_equivalent": 621,
            "construction": "Three modelled OD paths share central Ruijin corridor. 200 veh/h per OD yields 600 veh/h, used only as a historical order-of-magnitude reference.",
            "caveat": "Historical road count is not a 2026 weekend OD observation. Exact count section/direction and omitted traffic limit matching; equal division is an allocation assumption. Vehicle classes are not interchangeable with passenger-car equivalents.",
        },
        "optimizer": {"name": "deterministic bounded coordinate pattern search",
                      "initial_common_factors": [0.18, 0.25, 0.35, 0.5, 0.7, 1.0],
                      "steps": [0.08, 0.04, 0.02, 0.01, 0.005], "maximum_sweeps_per_step": 3},
        "acceptance": {"maximum_relative_error_to_each_training_median": 0.15,
                       "all_vehicles_complete": True, "zero_collisions_teleports_discarded": True,
                       "maximum_probe_insertion_delay_s": 20,
                       "insertion_budget_basis": "Floor of 5% of shortest training-route median (417s), fixed at 20s before search; initial 5s smoke threshold revised after observing 9s queue delay. Insertion time stays separately reported and included in system cost.",
                       "meaning": "Engineering consistency screen, not field-validation confidence level"},
        "holdout_policy": "Evaluation after saving all fits; no refitting. Retrospective temporal holdout: v1 outcomes were already inspected.",
        "step_policy": "Step ETA is highly proportional to whole-route ETA: diagnostic only, no duplicate objective weight",
        "unknowns_not_identified": ["actual OD counts", "signal phases and green splits", "real queue lengths", "lane counts"],
        "methods": ["https://sumo.dlr.de/docs/Tutorials/Calibration/San_Pablo_Dam.html",
                    "https://github.com/aonurakman/demandify",
                    "https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter5.htm"],
        "method_note": "Adapts the simulation-in-the-loop principle; does not execute demandify or claim to reproduce its genetic algorithm",
        "source_sha256": {path: sha(ROOT / path) for path in sources},
    }
    write(CONFIG, config)
    print("Frozen", CONFIG.relative_to(ROOT), flush=True)


def objective(metrics, targets):
    """Reject invalid or censored candidates instead of rewarding survivor bias."""
    if not metrics["physics_valid"] or not metrics["all_vehicles_arrived"] or not metrics["eta_usable"]:
        return None
    predictions = {od: metrics["per_od"][od]["probe"]["comparable_eta_s"] for od in targets}
    if any(x is None or not math.isfinite(x) for x in predictions.values()):
        return None
    return sum(((predictions[od] - target) / target) ** 2 for od, target in targets.items()) / len(targets)


def fit():
    config = read(CONFIG)
    for path, expected in config["source_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Frozen source changed: " + path)
    if (OUT / "fit.json").exists():
        raise ValueError("Fit already saved; preserve it")
    OUT.mkdir(parents=True, exist_ok=True)
    groups = sorted(config["engine_protocol"]["edge_speed_groups"])
    targets = config["target_median_eta_s"]
    lower, upper = config["speed_factor_bounds"]
    trace, profiles, cache = [], [], {}

    def candidate(headway, vector):
        vector = tuple(round(min(upper, max(lower, x)), 6) for x in vector)
        key = (headway, vector)
        if key in cache:
            return cache[key]
        params = {"default_headway_s": headway, "group_speed_factors": dict(zip(groups, vector))}
        folder = OUT / "runs" / ("case_" + engine.digest(params)[:16])
        if folder.exists():
            manifest = read(folder / "run_manifest.json")
            if manifest["parameters"] != params or manifest["protocol"] != config["engine_protocol"]:
                raise ValueError("Candidate cache protocol mismatch")
            if manifest["source_hashes"] != engine.source_hashes(config["engine_protocol"]):
                raise ValueError("Candidate cache source mismatch")
            for name, expected in manifest["artifacts"].items():
                if sha(folder / name) != expected:
                    raise ValueError("Candidate artifact changed: " + name)
            metrics = read(folder / "metrics.json")
        else:
            metrics = engine.run_case(params, folder, config["engine_protocol"])
        loss = objective(metrics, targets)
        row = {"case_id": folder.name, "parameters": params, "objective": loss,
               "physics_valid": metrics["physics_valid"], "eta_usable": metrics["eta_usable"],
               "all_vehicles_arrived": metrics["all_vehicles_arrived"],
               "probe_maximum_insertion_delay_s": metrics["probe"]["insertion_delay_max_s"],
               "physics_counters": metrics["physics_counters"],
               "predictions_s": {od: metrics["per_od"][od]["probe"]["in_network_mean_s"] for od in targets},
               "run_dir": str(folder.relative_to(ROOT))}
        trace.append(row)
        with (OUT / "search_trace.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        cache[key] = (math.inf if loss is None else loss, vector, row)
        return cache[key]

    for profile in config["demand_profiles"]:
        h = profile["headway_s"]
        starts = [candidate(h, [x] * len(groups)) for x in config["optimizer"]["initial_common_factors"]]
        best = min(starts, key=lambda x: x[0])
        if math.isfinite(best[0]):
            for step in config["optimizer"]["steps"]:
                for _ in range(config["optimizer"]["maximum_sweeps_per_step"]):
                    before = best[0]
                    for coordinate in range(len(groups)):
                        neighbors = [best]
                        for sign in (-1, 1):
                            proposal = list(best[1]); proposal[coordinate] += sign * step
                            neighbors.append(candidate(h, proposal))
                        best = min(neighbors, key=lambda x: x[0])
                    if best[0] >= before - 1e-12:
                        break
        row = best[2]
        errors = {od: (row["predictions_s"][od] - targets[od]) / targets[od]
                  if row["predictions_s"][od] is not None else None for od in targets}
        accepted = math.isfinite(best[0]) and all(x is not None for x in errors.values()) and max(abs(x) for x in errors.values()) <= config["acceptance"]["maximum_relative_error_to_each_training_median"]
        profiles.append({**profile, "best": row, "relative_errors": errors,
                         "passes_consistency_screen": accepted})
        write(OUT / "partial_fit.json", {"profiles": profiles, "candidates": len(trace)})
        print(json.dumps({"profile": profile["id"], "objective": row["objective"],
                          "accepted": accepted, "predictions_s": row["predictions_s"],
                          "candidates": len(trace)}, ensure_ascii=False), flush=True)
    accepted = [p for p in profiles if p["passes_consistency_screen"]]
    valid = [p for p in profiles if p["best"]["objective"] is not None]
    selected = min(valid, key=lambda p: p["best"]["objective"]) if valid else None
    result = {"version": 2, "status": "fit_completed" if valid else "no_valid_candidate",
              "config_sha256": sha(CONFIG), "completed_at": engine.baseline.utc_now(),
              "profiles": profiles, "candidates": len(trace),
              "invalid_candidates": sum(x["objective"] is None for x in trace),
              "accepted_profile_ids": [x["id"] for x in accepted],
              "selected_for_time_prediction": selected["id"] if selected else None,
              "selection_note": "Minimum training error only; a selected flow profile is NOT a unique measured-flow estimate",
              "point_estimate_of_actual_flow": None,
              "output_time_correction": None,
              "non_identifiability": "Several candidate flow rates can be observationally equivalent; ranges are sensitivity envelopes, not confidence intervals",
              "source_sha256": config["source_sha256"]}
    write(OUT / "fit.json", result)
    write(OUT / "fit_manifest.json", {"artifacts": {name: sha(OUT / name) for name in
          ("fit.json", "partial_fit.json", "search_trace.jsonl")}, "config_sha256": sha(CONFIG)})
    print("Fit saved", str((OUT / "fit.json").relative_to(ROOT)), flush=True)


def evaluate_predictions(rows, predictions):
    residuals = [predictions[r["route_pair_id"]] - float(r["route_duration_s"]) for r in rows]
    return {"n": len(residuals), "mae_s": statistics.mean(abs(x) for x in residuals),
            "rmse_s": math.sqrt(statistics.mean(x * x for x in residuals)),
            "bias_s": statistics.mean(residuals),
            "per_od_mae_s": {od: statistics.mean(abs(predictions[od] - float(r["route_duration_s"]))
                                                for r in rows if r["route_pair_id"] == od)
                             for od in predictions}}


def evaluate():
    config, fitted = read(CONFIG), read(OUT / "fit.json")
    if sha(CONFIG) != fitted["config_sha256"]:
        raise ValueError("Frozen protocol changed after fitting")
    for path, expected in config["source_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Frozen evaluation source changed: " + path)
    for name, expected in read(OUT / "fit_manifest.json")["artifacts"].items():
        if sha(OUT / name) != expected:
            raise ValueError("Fitted artifact changed: " + name)
    with (ROOT / "data/processed/identification_v2/routes.csv").open() as handle:
        all_rows = list(csv.DictReader(handle))
    # Eligibility flags are explicit and kept in the structured observations.
    rows = [r for r in all_rows if r["fit_eligible"].lower() == "true" or r["evaluation_eligible"].lower() == "true"]
    phases = sorted({r["phase"] for r in rows})
    output = {"fit_sha256": sha(OUT / "fit.json"),
              "observations_sha256": sha(ROOT / "data/processed/identification_v2/routes.csv"),
              "policy": config["holdout_policy"], "profiles": {}, "training_median_reference": {}}
    for p in fitted["profiles"]:
        if p["best"]["objective"] is None:
            continue
        predictions = p["best"]["predictions_s"]
        output["profiles"][p["id"]] = {phase: evaluate_predictions([r for r in rows if r["phase"] == phase], predictions) for phase in phases}
    output["training_median_reference"] = {phase: evaluate_predictions([r for r in rows if r["phase"] == phase], config["target_median_eta_s"]) for phase in phases}
    write(OUT / "evaluation.json", output)
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "fit", "evaluate"])
    args = parser.parse_args()
    {"freeze": freeze, "fit": fit, "evaluate": evaluate}[args.action]()


if __name__ == "__main__":
    main()
