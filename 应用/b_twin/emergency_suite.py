"""Frozen, resumable paired emergency runs and predeclared ablations."""
# AI辅助：OpenAI Codex；提交前据真实使用记录补齐并人工复核。
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import subprocess
import sys

from .paths import APP
from .emergency_access import build_emergency_access


def run_emergency_suite(jobs=3, prefix="stage05_main"):
    if not prefix.replace("_", "").isalnum() or not 1 <= jobs <= 3:
        raise ValueError("Invalid prefix/jobs")
    model = build_emergency_access()
    directory = APP / "results" / (prefix + "_suite")
    directory.mkdir(exist_ok=True)
    planned = [{"run_id": f"{prefix}_s{seed}_{policy}", "seed": seed, "policy": policy,
        "with_task": True, "with_event": True, "role": "paired"}
        for seed in [20261017, 20261018, 20261019, 20261020, 20261021]
        for policy in ["baseline", "yield", "combined"]]
    for label, task, event in [("topology_only", False, False), ("event_only", False, True), ("task_no_event", True, False)]:
        planned.append({"run_id": f"{prefix}_s20261017_{label}", "seed": 20261017, "policy": "baseline",
            "with_task": task, "with_event": event, "role": label})
    source_names = ["runner.py", "emergency.py", "emergency_access.py", "emergency_suite.py", "governance.py",
        "demand.py", "background.py", "evaluation.py", "boundary_supply.py"]
    sources = {name: hashlib.sha256((APP / "b_twin" / name).read_bytes()).hexdigest() for name in source_names}
    specification = {"runs": planned, "network_sha256_before_supply_and_meters": model["network_sha256"],
        "meter_policy": "feedback", "background_vph": 900, "supply_vph": 900, "entry_ridge": .5,
        "clearance_seconds": 1800, "source_sha256": sources,
        "scope": "Designed lane restriction and public-road rescue endpoint; NOT actual emergency response"}
    manifest = directory / "suite.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8"))["specification"] != specification:
            raise ValueError("Frozen emergency suite changed; use a new prefix")
    else:
        manifest.write_text(json.dumps({"created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "specification": specification}, ensure_ascii=False, indent=2), encoding="utf-8")
        archive = directory / "frozen_sources"
        archive.mkdir()
        for name in source_names:
            (archive / name).write_bytes((APP / "b_twin" / name).read_bytes())

    def one(spec):
        output = APP / "results" / spec["run_id"]
        if output.exists():
            meta_path = output / "run.json"
            if meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")).get("status") == "completed":
                return {**spec, "status": "already_completed"}
            raise ValueError("Incomplete output preserved; use a new prefix")
        command = [sys.executable, str(APP / "run.py"), "emergency-run", "--policy", spec["policy"],
            "--seed", str(spec["seed"]), "--run-id", spec["run_id"]]
        if not spec["with_task"]:
            command.append("--no-task")
        if not spec["with_event"]:
            command.append("--no-event")
        result = subprocess.run(command, capture_output=True, timeout=300, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        (directory / (spec["run_id"] + ".log")).write_bytes(result.stdout + result.stderr)
        result.check_returncode()
        emergency = json.loads((output / "emergency_result.json").read_text(encoding="utf-8"))
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
        return {**spec, "status": "completed", "elapsed_from_dispatch_s_step_notification": emergency["elapsed_from_dispatch_s"],
            "teleports": metrics["teleports"], "collisions": metrics["collisions"]}

    results = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        pending = {pool.submit(one, spec): spec for spec in planned}
        for future in as_completed(pending):
            try:
                result = future.result()
            except Exception as exc:
                result = {**pending[future], "status": "failed", "error": str(exc)}
            results.append(result)
            (directory / "execution.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if any(r["status"] == "failed" for r in results):
        raise RuntimeError("Emergency suite has failures; outputs and logs preserved")
    return {"suite": str(directory), "completed": len(results)}
