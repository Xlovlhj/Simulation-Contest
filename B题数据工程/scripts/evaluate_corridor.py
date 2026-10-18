"""Compare actual SUMO point-detector outputs with the withheld observations."""
import argparse
import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from download_sources import ROOT

OUT = ROOT / "refined"


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-counts", action="store_true")
    args = parser.parse_args()
    if args.all_counts:
        OUT = OUT / "all_counts"
    measured = pd.read_csv(OUT / "corridor_measurements.csv")
    results, runs = [], []
    for case in ["ordinary", "peak"]:
        intervals = []
        for e in ET.parse(OUT / f"{case}_e1.xml").getroot().findall("interval"):
            d = e.attrib
            # E1 ignores begin/end attributes; filter explicit short intervals.
            if float(d["begin"]) < 300:
                continue
            n = int(d["nVehContrib"])
            intervals.append({"loop_id": d["id"], "detector_id": d["id"].split("__")[0],
                "begin": float(d["begin"]), "end": float(d["end"]), "vehicles": n,
                "speed_numerator": max(0, float(d["speed"])) * 3.6 * n})
        df = pd.DataFrame(intervals)
        for did, g in df.groupby("detector_id"):
            periods = g[["begin", "end"]].drop_duplicates()
            seconds = (periods.end - periods.begin).sum()
            expected_seconds = 2700 if case == "ordinary" else 3600
            if seconds != expected_seconds or periods.begin.min() != 300:
                raise ValueError("Unexpected detector measurement window")
            n = g.vehicles.sum()
            q = n * 3600 / seconds
            v = g.speed_numerator.sum() / n if n else np.nan
            for row in measured[(measured.case == case) & (measured.detector_id == did)].itertuples():
                results.append({"case": case, "day": row.day, "detector_id": did, "role": row.role,
                    "simulated_vph": q, "observation_estimated_vph": row.flow_vph_estimated,
                    "simulated_speed_kmh": v, "observation_speed_kmh": row.observed_speed_kmh,
                    "flow_absolute_error": abs(q - row.flow_vph_estimated),
                    "speed_absolute_error_kmh": abs(v - row.observed_speed_kmh),
                    "note": "Day 16 freezes day-15 demand; no day-16 input update; E1 covers all SUMO lanes"})
        stat = ET.parse(OUT / f"{case}_statistics.xml").getroot()
        vehicles, teleports, safety = (stat.find(k).attrib for k in ["vehicles", "teleports", "safety"])
        runs.append({"case": case, "loaded": int(vehicles["loaded"]), "inserted": int(vehicles["inserted"]),
            "pending_at_end": int(vehicles["waiting"]), "teleports": int(teleports["total"]),
            "collisions": int(safety["collisions"]), "emergency_braking_events": int(safety["emergencyBraking"]),
            "pending_pct_of_loaded": 100 * int(vehicles["waiting"]) / int(vehicles["loaded"])})
    result = pd.DataFrame(results)
    result.to_csv(OUT / "simulation_observation_comparison.csv", index=False, encoding="utf-8-sig")
    metrics = []
    for (case, day, role), g in result.groupby(["case", "day", "role"]):
        metrics.append({"case": case, "day": day, "role": role, "stations": len(g),
            "flow_wmape_pct": float(100 * g.flow_absolute_error.sum() / g.observation_estimated_vph.sum()),
            "speed_mae_kmh": float(g.speed_absolute_error_kmh.mean())})
    statics = pd.read_csv(OUT / "static_count_comparisons.csv")
    static_metrics = []
    for (case, day, role), g in statics.groupby(["case", "day", "role"]):
        static_metrics.append({"case": case, "day": day, "role": role,
            "flow_wmape_pct": float(100 * abs(g.static_inferred_vph - g.observed_estimated_vph).sum() / g.observed_estimated_vph.sum())})
    report = {"runs": runs, "simulation_metrics": metrics, "static_metrics": static_metrics,
        "traffic_baseline_validated": False,
        "interpretation": "Observation agreement and peak congestion still require calibration; inspect pending vehicles and teleports in each run.",
        "limitations": ["One stochastic seed and one restricted corridor, not generalization evidence for the full study area.",
            "Calibration-site simulation outputs are not independent validation.",
            "Static fit can be nearly exact despite wrong internal route splits; check spatial holdouts.",
            "The fitted inputs are inferred window-average rates, not real vehicle identities or complete observed OD.",
            "No actual signal timing, real emergency incident or hospital gate has been validated."]}
    (OUT / "corridor_diagnostic.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
