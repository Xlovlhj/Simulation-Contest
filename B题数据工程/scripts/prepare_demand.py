"""Prepare measured count constraints and preliminary inferred routes for SUMO.

The inverse problem is underdetermined. This is NOT an observed OD matrix.
Only station mappings without automatic quality flags constrain the fit.
No microscopic traffic-model calibration or validation is claimed.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from download_sources import ROOT

SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path[:0] = [str(ROOT / ".deps"), str(SUMO_HOME / "tools")]
import sumolib

WINDOWS = [
    ("normal_calibration", "2026-10-15T10:00:00", "2026-10-15T11:00:00", "calibration"),
    ("am_peak_calibration", "2026-10-15T08:00:00", "2026-10-15T09:00:00", "calibration"),
    ("normal_validation", "2026-10-16T10:00:00", "2026-10-16T11:00:00", "holdout_unfitted"),
    ("am_peak_validation", "2026-10-16T08:00:00", "2026-10-16T09:00:00", "holdout_unfitted"),
]


def main():
    out = ROOT / "processed"
    net = sumolib.net.readNet(str(out / "kowloon.net.xml"))
    mapping = pd.read_csv(out / "detector_sumo_mapping.csv")
    data = pd.read_csv(out / "traffic_lane_30s.csv.gz")
    data["usable"] = ((data.valid_flag == "Y") & (data.interval_seconds == 30)
                     & data.vehicle_count.ge(0) & data.speed_kmh.ge(0)
                     & data.occupancy_pct.between(0, 100))
    lane_counts = data.groupby("detector_id").lane_id.nunique()
    measurements = []
    summaries = []
    for label, start, stop, split in WINDOWS:
        d = data[(data.period_from >= start) & (data.period_from < stop)]
        for did, g in d.groupby("detector_id"):
            v = g[g.usable]
            n = int(lane_counts[did])
            coverage = len(v) / (120 * n)
            count = v.vehicle_count.sum()
            rate = float(count * 120 * n / len(v)) if len(v) else None
            measurements.append({"window": label, "start": start, "stop": stop, "split": split,
                "detector_id": did, "valid_lane_intervals": len(v), "expected_lane_intervals": 120 * n,
                "coverage": coverage, "vehicle_count_observed": count,
                "hour_vehicle_count_estimated": rate,
                "count_estimation": "sample_rate_expansion_assumes_observed_intervals_represent_hour",
                "speed_kmh_weighted": float((v.speed_kmh * v.vehicle_count).sum() / count) if count else None})
        summaries.append({"window": label, "start": start, "stop": stop, "split": split,
            "purpose": "Observed ordinary/AM peak candidates; confirm congestion by detector before scenario selection"})
    measurements = pd.DataFrame(measurements).merge(mapping[["detector_id", "sumo_edge", "requires_manual_review"]], on="detector_id", how="left")
    measurements["eligible_for_initial_fit"] = (measurements.coverage >= .6) & ~measurements.requires_manual_review.fillna(True)
    measurements.to_csv(out / "demand_measurements.csv", index=False, encoding="utf-8-sig")

    all_edges = [e for e in net.getEdges() if e.allows("passenger")]
    sources = [e for e in all_edges if not e.getIncoming() and e.getOutgoing()]
    sinks = [e for e in all_edges if not e.getOutgoing() and e.getIncoming()]
    routes = set()
    fitted_edges = sorted(set(measurements.loc[measurements.eligible_for_initial_fit, "sumo_edge"]))
    # Five geographically close reachable sources/sinks per observed edge.
    for edge_id in fitted_edges:
        via = net.getEdge(edge_id)
        origin = via.getShape()[0]
        destination = via.getShape()[-1]
        upstream, downstream = [], []
        for src in sorted(sources, key=lambda e: math.dist(e.getShape()[-1], origin)):
            path, cost = net.getShortestPath(src, via, vClass="passenger")
            if path and cost > 150:
                upstream.append(path)
                if len(upstream) == 5:
                    break
        for sink in sorted(sinks, key=lambda e: math.dist(e.getShape()[0], destination)):
            path, cost = net.getShortestPath(via, sink, vClass="passenger")
            if path and cost > 150:
                downstream.append(path)
                if len(downstream) == 5:
                    break
        for up in upstream:
            for down in downstream:
                route = tuple(e.getID() for e in (*up, *down[1:]))
                if len(route) == len(set(route)):
                    routes.add(route)
    routes = sorted(routes)
    route_ids = [f"candidate_{i}" for i in range(len(routes))]
    rroot = ET.Element("routes")
    for rid, path in zip(route_ids, routes):
        ET.SubElement(rroot, "route", id=rid, edges=" ".join(path))
    ET.ElementTree(rroot).write(out / "candidate_routes.rou.xml", encoding="utf-8", xml_declaration=True)
    route_sets = [set(r) for r in routes]
    fits = []
    for label, _, _, split in WINDOWS:
        selected = measurements[(measurements.window == label) & measurements.eligible_for_initial_fit].copy()
        croot, interval = ET.Element("data"), None
        interval = ET.SubElement(croot, "interval", begin="0", end="3600")
        for row in selected.itertuples():
            ET.SubElement(interval, "edge", id=row.sumo_edge, entered=str(round(row.hour_vehicle_count_estimated)))
        ET.ElementTree(croot).write(out / f"{label}.edgeData.xml", encoding="utf-8", xml_declaration=True)
        if split != "calibration":
            continue
        a = np.array([[float(e in route) for route in route_sets] for e in selected.sumo_edge])
        b = selected.hour_vehicle_count_estimated.to_numpy()
        # Weak L2 regularization selects one spread solution, NOT a unique true OD.
        ridge = .1
        matrix = np.vstack([a, ridge * np.eye(len(routes))])
        rhs = np.r_[b, np.zeros(len(routes))]
        result = lsq_linear(matrix, rhs, bounds=(0, np.inf), tol=1e-7, max_iter=200)
        if not result.success:
            raise RuntimeError("Preliminary route count fit did not converge")
        x = result.x
        selected["fitted_static_count"] = a @ x
        selected["residual"] = selected.fitted_static_count - b
        selected.to_csv(out / f"{label}_count_fit.csv", index=False, encoding="utf-8-sig")
        root = ET.Element("routes")
        ET.SubElement(root, "vType", id="passenger_placeholder", vClass="passenger")
        rng = np.random.default_rng(20261017)
        numbers = np.floor(x).astype(int) + (rng.random(len(x)) < (x - np.floor(x)))
        inferred = []
        for rid, path, value, number in zip(route_ids, routes, x, numbers):
            if number <= 0:
                continue
            ET.SubElement(root, "route", id=rid, edges=" ".join(path))
        for rid, path, value, number in zip(route_ids, routes, x, numbers):
            if number <= 0:
                continue
            ET.SubElement(root, "flow", id="flow_" + rid, route=rid,
                type="passenger_placeholder", begin="0", end="3600", number=str(int(number)),
                departLane="best", departSpeed="max")
            inferred.append({"candidate_route": rid, "origin_edge": path[0], "destination_edge": path[-1],
                "expected_vehicles": value, "realized_vehicles": int(number),
                "provenance": "INFERRED_nonunique_count_constrained_route_demand_NOT_observed_OD"})
        ET.ElementTree(root).write(out / f"{label}.rou.xml", encoding="utf-8", xml_declaration=True)
        pd.DataFrame(inferred).to_csv(out / f"{label}_inferred_demand.csv", index=False, encoding="utf-8-sig")
        cfg = ET.Element("configuration")
        inp = ET.SubElement(cfg, "input")
        ET.SubElement(inp, "net-file", value="kowloon.net.xml")
        ET.SubElement(inp, "route-files", value=f"{label}.rou.xml")
        tm = ET.SubElement(cfg, "time")
        ET.SubElement(tm, "end", value="4500")
        ET.ElementTree(cfg).write(out / f"{label}.sumocfg", encoding="utf-8", xml_declaration=True)
        fits.append({"window": label, "observed_stations_used": len(selected), "candidate_routes": len(routes),
            "realized_vehicles": int(numbers.sum()), "constraint_rank": int(np.linalg.matrix_rank(a)),
            "static_count_rmse": float(np.sqrt(np.mean((a @ x - b) ** 2))),
            "traffic_model_validated": False})
        print(label, fits[-1], flush=True)
    report = {"windows": summaries, "fits": fits,
        "limitations": ["This inverse estimate is nonunique and covers measured corridors; other local traffic is underidentified.",
            "Missing periods use sample-rate expansion, not observed complete-hour counts; check sensitivity.",
            "Traffic counts measured inside the network are not independent boundary inflows.",
            "Static route incidence fit does not prove time-dependent SUMO outputs match observations.",
            "Placeholder passenger fleet is for input checking; integrate measured class proportions before final experiments.",
            "Validation-day observations were not used to fit these route counts.",
            "Manual network/mapping/turn restriction review remains necessary."]}
    (out / "demand_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
