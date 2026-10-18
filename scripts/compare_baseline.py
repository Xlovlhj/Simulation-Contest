#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date
# was not provided; the team must review this code and complete AI disclosure.
"""Diagnose geometry and time gaps; this is not an independent validation score."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="outputs/baseline/natural_surface_baseline_v1")
    args = parser.parse_args()
    run = (ROOT / args.run).resolve()
    if not run.is_relative_to(ROOT):
        raise ValueError("Run must be inside workspace")
    manifest = json.loads((run / "route_manifest.json").read_text())
    buckets = {}
    for trip in ET.parse(run / "tripinfo.xml").getroot().findall("tripinfo"):
        if float(trip.get("arrival", "-1")) < 0 or float(trip.get("depart", "-1")) < 0:
            continue
        route = trip.get("id").split("_v", 1)[0]
        buckets.setdefault(route, []).append(float(trip.get("duration")))
    rows = []
    for route in manifest["routes"]:
        times = buckets.get(route["id"], [])
        projected = route["projected_length_m"]
        platform = route["platform_distance_m"]
        rows.append({
            "route_id": route["source_id"], "platform_requested_at": route["requested_at"],
            "projected_length_m": round(projected, 3), "platform_distance_m": platform,
            "geometry_distance_difference_pct": round(100*(projected-platform)/platform, 3),
            "platform_estimated_time_s": route["platform_duration_s"],
            "uncalibrated_simulated_mean_s": round(statistics.mean(times), 3) if times else None,
            "completed_simulated_vehicles": len(times),
            "purpose": "uncalibrated_diagnostic_not_validation_or_policy_effect",
        })
    out = ROOT / "outputs/tables"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "baseline_platform_diagnostic.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "baseline_run": str(run.relative_to(ROOT)), "route_count": len(rows),
        "max_absolute_geometry_distance_difference_pct": max(abs(r["geometry_distance_difference_pct"]) for r in rows),
        "rows": rows,
        "interpretation": [
            "Geometry distance agreement checks approximate spatial scale, not topology or survey-grade accuracy.",
            "SUMO times use assumed low demand and no measured signals; they are intentionally uncalibrated.",
            "The model has no physical start timestamp and is not aligned with the observation window.",
            "Time differences are a calibration gap, not governance improvement or independent validation error.",
            "Future calibration and evaluation must use separate time blocks and identical actual paths.",
        ],
    }
    (out / "baseline_platform_diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k: result[k] for k in ("route_count", "max_absolute_geometry_distance_difference_pct")}))


if __name__ == "__main__":
    main()
