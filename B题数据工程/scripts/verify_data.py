"""Meaningful data checks and a short SUMO input/route loading check."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pandas as pd

from download_sources import ROOT


def main():
    out = ROOT / "processed"
    problems, counts = [], {}
    for manifest in ["sources_initial.json", "sources_downloads.json", "sources_supplementary.json"]:
        records = json.loads((ROOT / manifest).read_text(encoding="utf-8"))
        for record in records:
            path = ROOT / record["file"]
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
                problems.append("Source integrity failure: " + record["file"])
    data = pd.read_csv(out / "traffic_lane_30s.csv.gz")
    if data.duplicated(["period_from", "detector_id", "lane_id"]).any():
        problems.append("Duplicate observed lane interval")
    if not (data.interval_seconds == 30).all():
        problems.append("Unexpected observation interval")
    mapping = pd.read_csv(out / "detector_sumo_mapping.csv")
    if set(data.detector_id) - set(mapping.detector_id):
        problems.append("Unmapped detector")
    counts.update(lane_rows=len(data), mapped_detectors=len(mapping))
    demands = pd.read_csv(out / "demand_measurements.csv")
    if not demands.loc[demands.eligible_for_initial_fit, "coverage"].ge(.6).all():
        problems.append("Inadequate coverage entered initial fit")
    sumo_home = Path(os.environ.get("SUMO_HOME", r"C:\Program Files (x86)\Eclipse\Sumo"))
    sys.path[:0] = [str(ROOT / ".deps"), str(sumo_home / "tools")]
    import sumolib
    net = sumolib.net.readNet(str(out / "kowloon.net.xml"))
    candidates = ET.parse(out / "candidate_routes.rou.xml").getroot().findall("route")
    for route in candidates:
        edges = [net.getEdge(e) for e in route.get("edges").split()]
        for a, b in zip(edges, edges[1:]):
            if b not in a.getOutgoing():
                problems.append("Disconnected route: " + route.get("id"))
    counts["candidate_routes_checked"] = len(candidates)
    command = [str(sumo_home / "bin/sumo.exe"), "-c", str(out / "normal_calibration.sumocfg"),
        "--end", "1", "--route-steps", "0", "--no-step-log", "true", "--xml-validation", "never"]
    result = subprocess.run(command, capture_output=True, timeout=45)
    (out / "sumo_input_loading_check.log").write_bytes(result.stdout + result.stderr)
    if result.returncode != 0:
        problems.append("SUMO input loading failed")
    report = {"data_and_input_checks_passed": not problems, "problems": problems,
        "counts": counts, "sumo_exit_code": result.returncode,
        "network_sha256": hashlib.sha256((out / "kowloon.net.xml").read_bytes()).hexdigest(),
        "traffic_baseline_validated": False,
        "meaning": "Source integrity, interval uniqueness and route/input loading only; not simulation fidelity."}
    (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
