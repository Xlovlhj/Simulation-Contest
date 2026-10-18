"""Check refined observation estimates and route/fleet input consistency."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from download_sources import ROOT

OUT = ROOT / "refined"
SUMO_HOME = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
sys.path.insert(0, str(SUMO_HOME / "tools"))
import sumolib


def main():
    issues = []
    data = pd.read_csv(OUT / "traffic_15min_estimates.csv")
    if data.duplicated(["detector_id", "window"]).any():
        issues.append("Duplicate station/window")
    if not data.coverage.between(0, 1).all():
        issues.append("Coverage outside [0, 1]")
    if not (data.loc[data.eligible, "coverage"].ge(.6) & data.loc[data.eligible, "max_missing_run_seconds"].le(180)).all():
        issues.append("Ineligible interval included")
    if (data.flow_vph_estimated.dropna() < 0).any():
        issues.append("Negative inferred rate")
    if not np.isclose(data.loc[data.complete_intervals.eq(0), "observed_vehicles"].notna().sum(), 0):
        issues.append("Missing counts replaced with observed values")
    ci = data.dropna(subset=["conditional_flow_ci95_low", "conditional_flow_ci95_high"])
    if not (ci.conditional_flow_ci95_low.ge(0) & ci.conditional_flow_ci95_high.ge(ci.conditional_flow_ci95_low)).all():
        issues.append("Invalid conditional interval")
    net = sumolib.net.readNet(str(OUT / "corridor.net.xml"))
    routes = pd.read_csv(OUT / "corridor_candidate_routes.csv")
    for row in routes.itertuples():
        edges = [net.getEdge(e) for e in row.edges.split()]
        if any(b not in a.getOutgoing() for a, b in zip(edges, edges[1:])):
            issues.append("Disconnected candidate " + row.route_id)
    modes = []
    for directory in [OUT, OUT / "all_counts"]:
        fleet = pd.read_csv(directory / "vehicle_class_priors.csv")
        if not np.allclose(fleet.groupby(["case", "direction"]).proportion.sum(), 1):
            issues.append("Fleet proportions do not sum to one")
        measurements = pd.read_csv(directory / "corridor_measurements.csv")
        fit = measurements[measurements.role == "calibration_count_constraint"]
        if not fit.day.eq("2026-10-15").all():
            issues.append("Day-16 leakage into count fit")
        if directory == OUT and fit.detector_id.isin(["AID01115", "AID01210"]).any():
            issues.append("Spatial holdout leaked into count fit")
        for case in ["ordinary", "peak"]:
            root = ET.parse(directory / f"{case}_diagnostic.rou.xml").getroot()
            types = {e.get("id"): e.get("vClass") for e in root.findall("vType")}
            paths = {e.get("id"): e.get("edges").split() for e in root.findall("route")}
            distributions = {e.get("id"): e.get("vTypes").split() for e in root.findall("vTypeDistribution")}
            for flow in root.findall("flow"):
                for tid in distributions[flow.get("type")]:
                    if not all(net.getEdge(e).allows(types[tid]) for e in paths[flow.get("route")]):
                        issues.append("Vehicle class forbidden on route")
        diagnostic = json.loads((directory / "corridor_diagnostic.json").read_text())
        modes.append({"directory": directory.relative_to(ROOT).as_posix(),
            "runs": len(diagnostic["runs"]), "traffic_baseline_validated": diagnostic["traffic_baseline_validated"]})
    report = {"data_and_route_consistency_passed": not issues, "issues": issues,
        "station_windows": len(data), "eligible_station_windows": int(data.eligible.sum()),
        "candidate_routes_checked": len(routes), "modes": modes,
        "meaning": "Data, route, fleet and split consistency only. Does not certify simulation fidelity or contest compliance."}
    (OUT / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
