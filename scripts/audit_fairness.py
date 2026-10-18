#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Independently audit per-vehicle B-minus-A time differences from frozen XML.

Offline, standard library only. Writes outputs/quality/fairness_audit.* while
leaving every experiment artifact unchanged. No action defaults to help.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("primary", "higher_demand", "severe_event", "longer_event")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def path(value: str) -> Path:
    candidate = (ROOT/value).resolve()
    if not candidate.is_relative_to(ROOT) or candidate.name.endswith(".local.json"):
        raise ValueError("Only public workspace paths are accepted")
    return candidate


def audit() -> dict:
    summary_path = ROOT/"outputs/final_experiments/summary.json"
    summary = read(summary_path)
    if summary["status"] != "completed" or summary["all_physics_valid"] is not True:
        raise ValueError("Final paired scenarios are incomplete or failed physical checks")
    sources = {str(summary_path.relative_to(ROOT)): sha(summary_path),
               str(Path(__file__).relative_to(ROOT)): sha(Path(__file__))}
    results, vehicles = [], []
    for profile in PROFILES:
        pair = {}
        for policy in ("A", "B"):
            matches = [r for r in summary["runs"] if r["profile"] == profile and r["scenario_family"] == "S3" and r["policy"] == policy]
            if len(matches) != 1:
                raise ValueError("Missing or duplicate paired case")
            row = matches[0]
            folder = path(row["folder"])
            manifest = read(folder/"run_manifest.json")
            if sha(folder/"run_manifest.json") != row["run_manifest_sha256"] or not row["physics_valid"]:
                raise ValueError("Run manifest or physics gate is invalid")
            sources[str((folder/"run_manifest.json").relative_to(ROOT))] = sha(folder/"run_manifest.json")
            for filename in ("demand.json", "assignments.json", "tripinfo.xml", "metrics.json"):
                actual = sha(folder/filename)
                if actual != manifest["artifacts"][filename]:
                    raise ValueError("Frozen artifact hash mismatch: " + row["case"] + "/" + filename)
                sources[str((folder/filename).relative_to(ROOT))] = actual
            demand = read(folder/"demand.json")
            assigned = read(folder/"assignments.json")
            demand_ids = {v["id"] for v in demand}
            if len(demand_ids) != len(demand) or {v["id"] for v in assigned} != demand_ids:
                raise ValueError("Demand identities changed in assignments")
            for v in assigned:
                original = next(d for d in demand if d["id"] == v["id"])
                if (v["release_s"], v["role"], v["od_id"], v["vehicle_type"]) != (
                        original["release_s"], original["role"], original["od_id"], original["vehicle_type"]):
                    raise ValueError("Original release, OD, role or vehicle type changed")
            trip_elements = ET.parse(folder/"tripinfo.xml").getroot().findall("tripinfo")
            trips = {t.get("id"): dict(t.attrib) for t in trip_elements}
            if len(trips) != len(trip_elements) or set(trips) != demand_ids:
                raise ValueError("Missing or duplicate vehicle records; completed-trip fairness cannot be compared")
            if any(float(t["arrival"]) < 0 for t in trips.values()):
                raise ValueError("Censored trips require a separate fairness analysis")
            pair[policy] = {"row": row, "demand": demand, "assignments": {v["id"]: v for v in assigned}, "trips": trips}
        for key in ("demand_sha256", "physics_sha256", "event_sha256"):
            if pair["A"]["row"][key] != pair["B"]["row"][key]:
                raise ValueError("Paired conditions differ: " + key)
        if pair["A"]["demand"] != pair["B"]["demand"]:
            raise ValueError("Original demand differs despite paired metadata")
        ordinary = [d for d in pair["A"]["demand"] if d["role"] == "ordinary"]
        deltas, times_a, times_b, waits_b = [], [], [], []
        for d in ordinary:
            identity = d["id"]
            release = float(d["release_s"])
            a, b = (pair[k]["trips"][identity] for k in ("A", "B"))
            duration_a, duration_b = float(a["arrival"])-release, float(b["arrival"])-release
            wait = pair["B"]["assignments"][identity].get("source_control_wait_s", 0)
            if duration_a < 0 or duration_b < 0 or not 0 <= wait <= 45:
                raise ValueError("Invalid time or source-control bound")
            delta = duration_b-duration_a
            deltas.append(delta)
            times_a.append(duration_a)
            times_b.append(duration_b)
            waits_b.append(wait)
            vehicles.append({"profile": profile, "vehicle_id": identity, "od_id": d["od_id"],
                "original_release_s": release, "A_release_to_arrival_s": duration_a,
                "B_release_to_arrival_s": duration_b, "B_minus_A_s": delta,
                "B_source_control_wait_s": wait,
                "outcome": "benefited" if delta < 0 else "harmed" if delta > 0 else "unchanged"})
        a_total, b_total = sum(times_a), sum(times_b)
        if a_total != pair["A"]["row"]["ordinary_system_time_s"] or b_total != pair["B"]["row"]["ordinary_system_time_s"]:
            raise ValueError("Independent vehicle totals disagree with frozen summaries")
        item = {"profile": profile, "ordinary_count": len(ordinary),
            "benefited_count": sum(d < 0 for d in deltas), "harmed_count": sum(d > 0 for d in deltas),
            "unchanged_count": sum(d == 0 for d in deltas),
            "maximum_individual_loss_s": max([d for d in deltas if d > 0] or [0]),
            "maximum_individual_gain_s": max([-d for d in deltas if d < 0] or [0]),
            "ordinary_A_system_time_vehicle_s": a_total, "ordinary_B_system_time_vehicle_s": b_total,
            "ordinary_B_minus_A_vehicle_s": sum(deltas), "mean_B_minus_A_s_per_vehicle": sum(deltas)/len(deltas),
            "B_source_control_wait_vehicle_s": sum(waits_b), "B_maximum_source_control_wait_s": max(waits_b),
            "original_release_od_role_type_aligned": True,
            "paired_demand_sha256": pair["A"]["row"]["demand_sha256"],
            "paired_physics_sha256": pair["A"]["row"]["physics_sha256"],
            "paired_event_sha256": pair["A"]["row"]["event_sha256"]}
        if item["benefited_count"] + item["harmed_count"] + item["unchanged_count"] != len(ordinary):
            raise ValueError("Fairness classification does not conserve demand")
        results.append(item)
    return {"schema_version": 1, "status": "audited", "comparison": "S3 B minus A, same vehicle identity and original release",
        "definition": "delta_i = (arrival_B_i - original_release_i) - (arrival_A_i - original_release_i)",
        "sign_convention": "Negative delta benefits the vehicle; positive delta harms it. Maximum gain/loss are positive magnitudes.",
        "units": {"individual_time": "seconds", "aggregate_time": "vehicle-seconds", "counts": "ordinary vehicles"},
        "scope": "Four fixed assumption-based profiles, one deterministic paired run per policy; this is not a field or population fairness estimate.",
        "interpretation": "The 45-second bound applies only to source-control waiting, not total trip-time harm. Aggregate improvement is not a Pareto improvement when any ordinary vehicle is harmed.",
        "all_original_release_od_role_type_aligned": True, "source_sha256": sources,
        "profiles": results, "per_vehicle": vehicles}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    try:
        result = audit()
        directory = ROOT/"outputs/quality"
        directory.mkdir(parents=True, exist_ok=True)
        (directory/"fairness_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=list(result["profiles"][0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(result["profiles"])
        (directory/"fairness_audit.csv").write_text(out.getvalue(), encoding="utf-8")
        lines = ["# 逐车公平性核查：协同 B 相对路径引导 A", "",
            "按相同车辆 ID、原始释放时刻、OD 和车型对齐，直接读取冻结 tripinfo.xml，计算发布至到达总时耗的 B−A 差值。源端等待与入网等待都计入；两组需求、事件和物理参数哈希一致。", "",
            "|情景|普通车辆|受益|受损|持平|最大个体损失(秒)|最大个体收益(秒)|普通总差(车·秒)|",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for row in result["profiles"]:
            lines.append("|"+"|".join(str(row[k]) for k in ("profile", "ordinary_count", "benefited_count", "harmed_count", "unchanged_count", "maximum_individual_loss_s", "maximum_individual_gain_s", "ordinary_B_minus_A_vehicle_s"))+"|")
        lines += ["", "负总差表示普通车辆总体时间下降；最大个体损失与收益均以正数表达。所有组的受益、受损、持平人数之和等于原始普通需求。", "",
            "45 秒只限制源端控制等待，不能写成每车总行程损失上限。主情景与较长事件的最大个体损失为 58 秒；较高需求虽总体改善，仍有 30 辆普通车变慢，故不构成帕累托改善。", "",
            "这些是给定假设需求、机制和单次确定性运行内的配对结果，不代表真实道路上的人群公平性。JSON 保留逐车明细和输入哈希，CSV 为四情景汇总。", "",
            "复现：`python3 -B scripts/audit_fairness.py --run`。核查不修改原实验输出。"]
        (directory/"fairness_audit.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "profiles": result["profiles"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
