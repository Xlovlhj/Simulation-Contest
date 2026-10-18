"""Infer nonunique boundary-to-boundary demand and export a diagnostic model.

Only calibration-day counts constrain the inverse fit. Two internal stations
and the next day are withheld. This is an estimation/diagnosis, not true OD.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from download_sources import ROOT

OUT = ROOT / "refined"
BASE = OUT
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path[:0] = [str(ROOT / ".deps"), str(SUMO_HOME / "tools")]
import sumolib

HOLDOUT = {"AID01115", "AID01210"}
CASES = {"ordinary": ("11:15:00", "12:00:00"), "peak": ("08:00:00", "09:00:00")}
CLASSES = [
    ("MOTOR CYCLE", "motorcycle", 2.5), ("PRIVATE CAR", "passenger", 5.),
    ("TAXI", "passenger", 5.), ("PRIVATE LIGHT BUS", "bus", 6.5),
    ("PUBLIC LIGHT BUS", "bus", 6.5), ("LIGHT GOODS VEHICLE", "delivery", 6.5),
    ("MEDIUM/HEAVY GOODS VEHICLE", "truck", 10.), ("NON-FRANCHISED BUS", "bus", 12.),
    ("FRANCHAISED BUS (S.D.)", "bus", 12.), ("FRANCHIASED BUS (D.D.)", "bus", 12.)]


def write_xml(root, path):
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def measured_windows(table, mapping):
    rows = []
    for case, (begin, end) in CASES.items():
        for day in ["2026-10-15", "2026-10-16"]:
            start, stop = f"{day}T{begin}", f"{day}T{end}"
            for did in mapping.detector_id:
                d = table[(table.detector_id == did) & (table.window >= start)
                          & (table.window < stop) & table.eligible]
                if d.empty:
                    continue
                weights = d.complete_intervals.to_numpy()
                q = np.average(d.flow_vph_estimated, weights=weights)
                veh = d.observed_vehicles.sum()
                ci_sd = (d.conditional_flow_ci95_high - d.conditional_flow_ci95_low) / 3.92
                rows.append({"case": case, "day": day, "detector_id": did,
                    "begin": start, "end": stop, "eligible_quarters": len(d),
                    "flow_vph_estimated": q,
                    "sample_sd_proxy": float(np.sqrt(np.sum((ci_sd * weights) ** 2)) / weights.sum()),
                    "observed_speed_kmh": (d.speed_kmh_observed * d.observed_vehicles).sum() / veh if veh else np.nan,
                    "full_window_complete_slot_coverage": weights.sum() / ((pd.Timestamp(stop) - pd.Timestamp(start)).total_seconds() / 30),
                    "role": "calibration_count_constraint" if day.endswith("15") and did not in HOLDOUT else
                        "spatial_count_holdout" if day.endswith("15") else "next_day_unfitted_reference",
                    "provenance": "Eligible observed samples expanded in time; NOT complete observed window count"})
    return pd.DataFrame(rows).merge(mapping[["detector_id", "sumo_edge"]], on="detector_id")


def enumerate_routes(net, definition, mapped_edges):
    records, paths = [], set()
    for direction in ["north", "south"]:
        path = definition[direction + "_mainline"]
        core = set(definition["core_edges"])
        starts = [(0, None)]
        ends = [(len(path) - 1, None)]
        for j, eid in enumerate(path):
            starts.extend((j, e.getID()) for e in net.getEdge(eid).getIncoming()
                          if e.getID() not in core and e.allows("passenger"))
            ends.extend((j, e.getID()) for e in net.getEdge(eid).getOutgoing()
                        if e.getID() not in core and e.allows("passenger"))
        for j, source in starts:
            for k, sink in ends:
                if k < j:
                    continue
                route = tuple(([source] if source else []) + path[j:k + 1] + ([sink] if sink else []))
                if len(set(route)) != len(route) or route in paths or not (set(route) & mapped_edges):
                    continue
                if sum(net.getEdge(e).getLength() for e in route) < 150:
                    continue
                paths.add(route)
                records.append({"route_id": f"r{len(records)}", "direction": direction, "edges": route,
                    "source_mainline": source is None, "sink_mainline": sink is None,
                    "length_m": sum(net.getEdge(e).getLength() for e in route)})
    return records


def main():
    global OUT, HOLDOUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-counts", action="store_true", help="Use all day-15 count stations; keep day 16 unfitted")
    args = parser.parse_args()
    if args.all_counts:
        OUT = BASE / "all_counts"
        OUT.mkdir(exist_ok=True)
        HOLDOUT = set()
        shutil.copyfile(BASE / "corridor.net.xml", OUT / "corridor.net.xml")
    net = sumolib.net.readNet(str(BASE / "corridor.net.xml"))
    definition = json.loads((BASE / "corridor_definition.json").read_text())
    mapping = pd.read_csv(BASE / "detector_mapping.csv", dtype={"sumo_edge": str})
    mapping = mapping[mapping.sumo_edge.isin(definition["core_edges"]) & ~mapping.requires_review].copy()
    table = pd.read_csv(BASE / "traffic_15min_estimates.csv")
    measured = measured_windows(table, mapping)
    measured.to_csv(OUT / "corridor_measurements.csv", index=False, encoding="utf-8-sig")
    routes = enumerate_routes(net, definition, set(mapping.sumo_edge))
    sets = [set(r["edges"]) for r in routes]
    route_table = pd.DataFrame([{**r, "edges": " ".join(r["edges"])} for r in routes])
    route_table.to_csv(OUT / "corridor_candidate_routes.csv", index=False, encoding="utf-8-sig")

    # Same-road northbound hourly classification; opposite direction is missing.
    # Southbound uses the northbound mix as an explicit transferable prior.
    classes = pd.read_csv(ROOT / "processed/vehicle_class_proportions.csv")
    class_rows = []
    for case, (begin, end) in CASES.items():
        hour = begin[:2]
        d = classes[(classes.detector_id == "TCS01102") & classes.exclusive_category
                    & (classes.valid_flag == "Y") & (classes.period_from == f"2026-10-15T{hour}:00:00")]
        proportions = d.set_index("class_name").proportion_pct.reindex([c[0] for c in CLASSES])
        if proportions.isna().any() or not 98 <= proportions.sum() <= 102:
            raise ValueError("Missing usable hourly vehicle-class prior")
        proportions /= proportions.sum()
        for direction in ["north", "south"]:
            for i, (name, vclass, length) in enumerate(CLASSES):
                class_rows.append({"case": case, "direction": direction, "class_name": name,
                    "vtype_id": f"type{i}", "sumo_vclass": vclass, "proportion": proportions[name],
                    "length_m_assumed": length, "source_detector": "TCS01102", "source_hour": f"2026-10-15T{hour}:00:00",
                    "provenance": "same_road_same_direction_measured_prior" if direction == "north" else
                        "opposite_direction_transfer_assumption_NOT_measured_southbound",
                    "note": "Class proportions are observed; representative lengths and driving parameters are assumptions"})
    fleet = pd.DataFrame(class_rows)
    fleet.to_csv(OUT / "vehicle_class_priors.csv", index=False, encoding="utf-8-sig")

    fits, ensemble_rows, comparisons = [], [], []
    for case, (begin, end) in CASES.items():
        train = measured[(measured.case == case) & (measured.role == "calibration_count_constraint")].copy()
        a = np.array([[float(e in p) for p in sets] for e in train.sumo_edge])
        b = train.flow_vph_estimated.to_numpy()
        weights = 500 / np.maximum(100, train.sample_sd_proxy.to_numpy())
        # Spread one directional inflow over plausible boundary routes.
        priors = {}
        for preference in ["through", "balanced"]:
            x0 = np.array([(1 if r["source_mainline"] else .25) *
                           (1 if r["sink_mainline"] else (.08 if preference == "through" else .35))
                           for r in routes], dtype=float)
            for direction in ["north", "south"]:
                first = definition[direction + "_mainline"][0]
                measured_first = train[train.sumo_edge == first].flow_vph_estimated
                scale = float(measured_first.iloc[0])
                idx = [i for i, r in enumerate(routes) if r["direction"] == direction]
                denominator = sum(x0[i] for i in idx if first in sets[i])
                x0[idx] *= scale / denominator
            priors[preference] = x0
        solutions = []
        baseline = None
        for preference, x0 in priors.items():
            for ridge in [.05, .2, 1.]:
                matrix = np.vstack([a * weights[:, None], ridge * np.eye(len(routes))])
                rhs = np.r_[b * weights, ridge * x0]
                result = lsq_linear(matrix, rhs, bounds=(0, np.inf), tol=1e-8, max_iter=300)
                if not result.success:
                    raise RuntimeError("Nonnegative route fit failed")
                x = result.x
                solutions.append(x)
                for r, value in zip(routes, x):
                    ensemble_rows.append({"case": case, "prior": preference, "ridge": ridge,
                                          "route_id": r["route_id"], "estimated_vph": value})
                if preference == "through" and ridge == .2:
                    baseline = x
        assert baseline is not None
        stack = np.vstack(solutions)
        result_table = route_table.copy()
        result_table["estimated_vph"] = baseline
        result_table["sensitivity_min_vph"] = stack.min(axis=0)
        result_table["sensitivity_max_vph"] = stack.max(axis=0)
        result_table["provenance"] = "NONUNIQUE_INFERRED_BOUNDARY_ROUTE_DEMAND_NOT_observed_OD"
        result_table.to_csv(OUT / f"{case}_inferred_routes.csv", index=False, encoding="utf-8-sig")
        for row in measured[measured.case == case].itertuples():
            incidence = np.array([float(row.sumo_edge in p) for p in sets])
            predictions = stack @ incidence
            comparisons.append({"case": case, "day": row.day, "detector_id": row.detector_id, "role": row.role,
                "observed_estimated_vph": row.flow_vph_estimated, "static_inferred_vph": float(incidence @ baseline),
                "sensitivity_min_vph": predictions.min(), "sensitivity_max_vph": predictions.max(),
                "note": "Next-day comparison freezes day-15 demand; static incidence is not simulated throughput"})
        fitted = a @ baseline
        fits.append({"case": case, "constraint_stations": len(train), "routes": len(routes),
            "constraint_rank": int(np.linalg.matrix_rank(a)), "static_fit_wmape_pct": float(100 * abs(fitted - b).sum() / b.sum()),
            "inferred_total_boundary_vph": float(baseline.sum()), "seconds": (pd.Timestamp(end) - pd.Timestamp(begin)).total_seconds(),
            "sensitivity_solutions": len(solutions), "observed_OD": False})

        duration = int((pd.Timestamp(end) - pd.Timestamp(begin)).total_seconds())
        warmup = 300
        root = ET.Element("routes")
        for i, (name, vclass, length) in enumerate(CLASSES):
            ET.SubElement(root, "vType", id=f"type{i}", vClass=vclass, length=str(length),
                          speedFactor="1.0", speedDev="0.1", sigma="0.5")
        for r in routes:
            ET.SubElement(root, "route", id=r["route_id"], edges=" ".join(r["edges"]))
        excluded = []
        for r, rate in zip(routes, baseline):
            if rate < .1:
                continue
            mix = fleet[(fleet.case == case) & (fleet.direction == r["direction"])]
            usable = mix[mix.sumo_vclass.map(lambda c: all(net.getEdge(e).allows(c) for e in r["edges"]))]
            removed = 1 - usable.proportion.sum()
            if removed > 1e-6:
                excluded.append({"route": r["route_id"], "renormalized_fraction": removed})
            if usable.proportion.sum() <= 0:
                raise RuntimeError("No usable class on route")
            dist_id = "fleet_" + r["route_id"]
            ET.SubElement(root, "vTypeDistribution", id=dist_id, vTypes=" ".join(usable.vtype_id),
                          probabilities=" ".join(str(x) for x in usable.proportion / usable.proportion.sum()))
        # Flows have identical begin time and therefore are departure-sorted.
        for r, rate in zip(routes, baseline):
            if rate >= .1:
                ET.SubElement(root, "flow", id="f_" + r["route_id"], route=r["route_id"], type="fleet_" + r["route_id"],
                    begin="0", end=str(duration + warmup), vehsPerHour=str(rate), departLane="best", departSpeed="max")
        write_xml(root, OUT / f"{case}_diagnostic.rou.xml")
        (OUT / f"{case}_fleet_permissions.json").write_text(json.dumps(excluded, indent=2), encoding="utf-8")

        additional = ET.Element("additional")
        locations = pd.read_csv(ROOT / "processed/detector_locations.csv").set_index("AID_ID_Number")
        for row in mapping.itertuples():
            edge = net.getEdge(row.sumo_edge)
            xy = net.convertLonLat2XY(locations.loc[row.detector_id, "Longitude"], locations.loc[row.detector_id, "Latitude"])
            for lane in edge.getLanes():
                pos, dist = lane.getClosestLanePosAndDist(xy)
                ET.SubElement(additional, "inductionLoop", id=f"{row.detector_id}__{lane.getIndex()}",
                    lane=lane.getID(), pos=str(max(.1, min(lane.getLength() - .1, pos))),
                    period="300", file=f"{case}_e1.xml")
        write_xml(additional, OUT / f"{case}_detectors.add.xml")
        cfg = ET.Element("configuration")
        inp = ET.SubElement(cfg, "input")
        for k, v in {"net-file": "corridor.net.xml", "route-files": f"{case}_diagnostic.rou.xml",
                     "additional-files": f"{case}_detectors.add.xml"}.items():
            ET.SubElement(inp, k, value=v)
        tm = ET.SubElement(cfg, "time")
        ET.SubElement(tm, "end", value=str(duration + warmup))
        ET.SubElement(tm, "step-length", value="0.5")
        output = ET.SubElement(cfg, "output")
        ET.SubElement(output, "summary-output", value=f"{case}_summary.xml")
        ET.SubElement(output, "statistic-output", value=f"{case}_statistics.xml")
        report = ET.SubElement(cfg, "report")
        ET.SubElement(report, "no-step-log", value="true")
        ET.SubElement(report, "duration-log.disable", value="true")
        random = ET.SubElement(cfg, "random_number")
        ET.SubElement(random, "seed", value="20261017")
        write_xml(cfg, OUT / f"{case}_diagnostic.sumocfg")

    pd.DataFrame(ensemble_rows).to_csv(OUT / "route_demand_sensitivity.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(comparisons).to_csv(OUT / "static_count_comparisons.csv", index=False, encoding="utf-8-sig")
    report = {"fits": fits, "spatial_holdout_stations": sorted(HOLDOUT),
        "mode": "all_available_calibration_day_counts" if args.all_counts else "with_spatial_holdout",
        "limitations": ["Route incidence constraints do not uniquely identify OD or turning flows.",
            "Unobserved routes/traffic that never pass these stations are excluded, not proven absent.",
            "Sensitivity range is across selected priors/regularization, not a statistical confidence interval.",
            "Window rates assume eligible observed periods represent the selected window; excluded quarters remain missing.",
            "Day-16 demand is not refitted; day-16 comparisons freeze day-15 input.",
            "Microscopic parameters, vehicle lengths and unmapped road attributes remain assumptions.",
            "This reduced corridor has no signalized junction and cannot validate signal timing or the full B task."]}
    (OUT / "corridor_input_quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
