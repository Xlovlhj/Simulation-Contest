"""Frozen, resumable paired runs in separate SUMO/Python processes."""
# AI辅助：OpenAI Codex；精确型号/发布日期不可得，提交前按真实使用记录补齐。
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .paths import APP


def run_suite(jobs=3, prefix="stage04_main"):
    if not prefix.replace("_", "").isalnum() or not 1 <= jobs <= 3:
        raise ValueError("Invalid prefix/jobs")
    directory = APP / "results" / (prefix + "_suite")
    directory.mkdir(exist_ok=True)
    sources = {name: hashlib.sha256((APP / "b_twin" / name).read_bytes()).hexdigest()
        for name in ["runner.py", "governance.py", "demand.py", "background.py", "evaluation.py"]}
    planned = [{"run_id": f"{prefix}_s{seed}_{policy}", "seed": seed, "policy": policy}
        for seed in [20261017, 20261018, 20261019, 20261020, 20261021] for policy in ["free", "fixed", "feedback"]]
    specification = {"runs": planned, "case": "peak", "network": "through", "entry_ridge": .5,
        "background_vph": 900, "nominal_supply_vph": 900, "clearance_seconds": 1800,
        "source_sha256": sources, "role": "Designed supply scenario; NOT validated real peak governance"}
    manifest = directory / "suite.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8"))["specification"] != specification:
            raise ValueError("Frozen suite changed; use a new prefix")
    else:
        manifest.write_text(json.dumps({"created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "specification": specification}, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_one(spec):
        path = APP / "results" / spec["run_id"]
        if path.exists():
            if (path / "run.json").exists() and json.loads((path / "run.json").read_text(encoding="utf-8")).get("status") == "completed":
                return {**spec, "status": "already_completed"}
            raise ValueError("Failed/incomplete output preserved; use a new suite prefix: " + spec["run_id"])
        command = [sys.executable, str(APP / "run.py"), "run", "--case", "peak", "--network", "through",
            "--entry-ridge", ".5", "--background-vph", "900", "--supply-vph", "900", "--clearance", "1800",
            "--backend", "traci", "--meter-policy", spec["policy"], "--seed", str(spec["seed"]), "--run-id", spec["run_id"]]
        result = subprocess.run(command, capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=300)
        (directory / (spec["run_id"] + ".log")).write_bytes(result.stdout + result.stderr)
        result.check_returncode()
        metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
        return {**spec, "status": "completed", "mean_time_lower_bound_s": metrics["trip_accounting"]["mean_experienced_time_lower_bound_s"],
            "unfinished": metrics["trip_accounting"]["unfinished_after_clearance"], "teleports": metrics["teleports"]}

    results = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_one, spec): spec for spec in planned}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = {**futures[future], "status": "failed", "error": str(exc)}
            results.append(result)
            (directory / "execution.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(r["status"] == "failed" for r in results):
        raise RuntimeError("At least one run failed; preserved outputs and logs in suite directory")
    return {"suite": str(directory), "completed": len(results), "specification": specification}
