#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date
# was not provided; the team must review the implementation and interpretation.
"""Run an offline, assumed-demand engineering check; this is not S2 evidence."""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def physics_quality(metrics: dict) -> dict:
    summary = metrics.get("final_sumo_summary", {})
    reasons = []
    for key in ("collisions", "teleports", "discarded"):
        value = summary.get(key)
        try:
            count = int(value)
        except (TypeError, ValueError):
            reasons.append(f"missing_or_invalid_{key}")
        else:
            if count != 0:
                reasons.append(f"{key}={count}")
    if metrics.get("count_conservation") is not True or metrics.get("final_summary_consistent") is not True:
        reasons.append("count_audit_failed")
    if "physics_valid" in metrics and metrics["physics_valid"] is not True:
        reasons.append("runner_physics_gate_failed")
    return {"physics_valid": not reasons, "quality_reasons": ";".join(reasons)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervals", nargs="+", type=int, default=[60, 30, 15],
                        help="Assumed seconds between departures per route; no observed flows")
    args = parser.parse_args(argv)
    if len(args.intervals) > 6 or len(set(args.intervals)) != len(args.intervals) or any(value < 3 for value in args.intervals):
        parser.error("Use at most six distinct departure intervals of at least 3 seconds")
    source = ROOT / "config/baseline.json"
    baseline = json.loads(source.read_text(encoding="utf-8"))
    identifier = datetime.now(timezone.utc).strftime("PRE_%Y%m%dT%H%M%S_%fZ")
    batch = ROOT / "experiments" / identifier
    batch.mkdir(parents=True, exist_ok=False)
    manifest = {
        "batch_id": identifier, "status": "running", "purpose": "assumed_demand_engineering_check",
        "real_world_calibrated": False, "governance_comparison": False,
        "base_config": str(source.relative_to(ROOT)), "base_config_sha256": digest(source),
        "script_sha256": digest(Path(__file__)), "intervals_s": args.intervals,
        "rules": [
            "Only assumed departure interval changes; geometry, vehicle model, seed and duration are fixed.",
            "All model parameters and demands remain assumptions; no field congestion is reconstructed.",
            "Total system time retains insertion waiting and unfinished vehicles at the common horizon.",
            "Different demands are not a with/without-governance comparison; no improvement rate is computed.",
            "The deterministic model does not obtain uncertainty estimates by repeating identical seeds.",
        ], "runs": [],
    }
    save(batch / "manifest.json", manifest)
    rows = []
    try:
        for interval in args.intervals:
            config = copy.deepcopy(baseline)
            config["departure_interval_s"] = interval
            config["assumptions"] = [
                text for text in baseline["assumptions"] if not text.startswith("Each candidate route receives")
            ] + [f"Each selected route receives an assumed vehicle every {interval} seconds for the first "
                 f"{config['departure_duration_s']} seconds; no observed OD flow is used."]
            changed = {key for key in baseline if baseline[key] != config[key]}
            if not changed <= {"departure_interval_s", "assumptions"}:
                raise ValueError("Unexpected scenario parameter changes")
            config_path = batch / f"interval_{interval}.json"
            save(config_path, config)
            run_id = f"{identifier}_interval{interval}"
            command = [sys.executable, str(ROOT / "scripts/run_baseline.py"),
                       "--config", str(config_path), "--run-id", run_id]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600)
            (batch / f"interval_{interval}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
            if completed.returncode not in (0, 2):
                raise RuntimeError(f"Scenario interval={interval} failed; see batch log")
            folder = ROOT / "outputs/baseline" / run_id
            run_manifest = json.loads((folder / "run_manifest.json").read_text(encoding="utf-8"))
            metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
            if run_manifest["status"] not in ("completed", "physics_invalid") or not metrics["count_conservation"] or not metrics["final_summary_consistent"]:
                raise RuntimeError("Run did not complete its conservation checks")
            if completed.returncode == 2 and run_manifest["status"] != "physics_invalid":
                raise RuntimeError("Nonzero exit did not identify a preserved physics-invalid run")
            if manifest["runs"] and run_manifest["input_sha256"] != manifest["runs"][0]["input_sha256"]:
                raise RuntimeError("Source routes changed during the batch")
            entry = {"interval_s": interval, "run_directory": str(folder.relative_to(ROOT)),
                     "input_sha256": run_manifest["input_sha256"], "config_sha256": digest(config_path),
                     "run_manifest_sha256": digest(folder / "run_manifest.json"),
                     "metrics_sha256": digest(folder / "metrics.json")}
            manifest["runs"].append(entry)
            row = {"interval_s": interval, "relative_assumed_demand": baseline["departure_interval_s"] / interval,
                   **{key: metrics[key] for key in (
                       "planned", "departed", "arrived", "not_departed", "unfinished_on_network", "completion_rate",
                       "completed_travel_time_mean_s", "total_in_network_vehicle_time_s",
                       "total_departure_wait_time_s", "total_system_vehicle_time_s")},
                   "collisions": metrics["final_sumo_summary"].get("collisions"),
                   "teleports": metrics["final_sumo_summary"].get("teleports"),
                   "discarded": metrics["final_sumo_summary"].get("discarded"),
                   **physics_quality(metrics),
                   "run_directory": str(folder.relative_to(ROOT))}
            rows.append(row)
            save(batch / "manifest.json", manifest)
            print(f"interval={interval}s: planned={metrics['planned']}, arrived={metrics['arrived']}, "
                  f"unfinished={metrics['unfinished_total']}", flush=True)
        with (batch / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        save(batch / "summary.json", rows)
        all_valid = all(row["physics_valid"] for row in rows)
        manifest.update(status="completed" if all_valid else "completed_with_invalid_scenarios",
                        all_scenarios_physics_valid=all_valid, eligible_for_governance=False,
                        summary_sha256=digest(batch / "summary.csv"))
        save(batch / "manifest.json", manifest)
        print(f"Engineering check only: {batch.relative_to(ROOT)}/summary.csv")
        return 0 if all_valid else 2
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        manifest.update(status="failed", error=str(error))
        save(batch / "manifest.json", manifest)
        print(f"Preflight failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
