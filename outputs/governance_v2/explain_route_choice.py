#!/usr/bin/env python3
"""Recompute why causal route-cost rule A retained the default in frozen V2 runs.

Writes two supplemental explanations only; never changes the frozen runner,
governance runs, their summary, or their manifests. Offline standard library.
"""
from pathlib import Path
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def explain():
    sources = {}

    def read(path):
        path = (ROOT / path).resolve()
        if not path.is_relative_to(ROOT) or path.name.endswith(".local.json"):
            raise ValueError("Only public workspace inputs are allowed")
        sources[str(path.relative_to(ROOT))] = sha(path)
        return json.loads(path.read_text())

    summary = read(OUT / "summary.json")
    config = read("config/inverse_estimation_v2.json")
    catalog = read(config["engine_protocol"]["route_catalog"])
    basis = read("research/parameter_audit/scenario_basis_v2.json")
    fit = read("outputs/identification_v2/calibration/fit.json")
    top_manifest = read(OUT / "manifest.json")
    if top_manifest["artifacts"]["summary.json"] != sha(OUT / "summary.json"):
        raise ValueError("Frozen governance summary hash mismatch")
    for path, expected in summary["input_hashes"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("A frozen governance source changed: " + path)
    sources[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    od = basis["event_geometry"]["route_pair_id"]
    demand = next(d for d in catalog["demand_ods"] if d["id"] == od)
    candidates = [c for c in catalog["route_candidates"] if c["demand_od_id"] == od]
    default = next(c for c in candidates if c["edge_ids"] == demand["baseline_edge_ids"])
    alternatives = [c for c in candidates if c["id"] != default["id"]]
    if not alternatives:
        raise ValueError("No alternate path exists for threshold analysis")
    lengths = {e["id"]: e["length_m"] for e in catalog["network"]["edges"]}
    profiles = []
    for fitted in fit["profiles"]:
        if fitted["best"]["objective"] is None:
            continue
        row = next(r for r in summary["runs"] if r["profile"] == fitted["id"] and r["preset"] == "step_q25" and r["scenario_family"] == "S2" and r["policy"] == "fixed")
        folder = ROOT / row["folder"]
        manifest = read(folder / "run_manifest.json")
        for name in ("expanded_parameters.json", "event.json"):
            if sha(folder / name) != manifest["artifacts"][name]:
                raise ValueError("Frozen run artifact changed: " + name)
        expanded = read(folder / "expanded_parameters.json")
        event = read(folder / "event.json")
        event_set = set(event["edge_ids"])
        speeds = expanded["edge_effective_speed_m_s"]

        def decompose(candidate):
            inside = [e for e in candidate["edge_ids"] if e in event_set]
            outside = [e for e in candidate["edge_ids"] if e not in event_set]
            return {"route_id": candidate["id"], "event_edge_ids": inside,
                    "event_length_m": sum(lengths[e] for e in inside),
                    "outside_event_length_m": sum(lengths[e] for e in outside),
                    "route_length_m": sum(lengths[e] for e in candidate["edge_ids"]),
                    "event_baseline_cost_s": sum(lengths[e]/speeds[e] for e in inside),
                    "outside_event_cost_s": sum(lengths[e]/speeds[e] for e in outside)}

        d = decompose(default)
        comparisons = []
        for candidate in alternatives:
            a = decompose(candidate)
            shared = [e for e in d["event_edge_ids"] if e in a["event_edge_ids"]]
            avoidable = [e for e in d["event_edge_ids"] if e not in a["event_edge_ids"]]
            extra = a["outside_event_cost_s"]-d["outside_event_cost_s"]
            saved = d["event_baseline_cost_s"]-a["event_baseline_cost_s"]
            threshold = saved/extra if extra > 0 and saved > 0 else None
            costs = []
            for preset in basis["presets"]:
                rho = preset["speed_multiplier"]
                cd = d["outside_event_cost_s"]+d["event_baseline_cost_s"]/rho
                ca = a["outside_event_cost_s"]+a["event_baseline_cost_s"]/rho
                # Independent sum directly over every edge validates algebra.
                direct_d = sum(lengths[e]/speeds[e]/(rho if e in event_set else 1) for e in default["edge_ids"])
                direct_a = sum(lengths[e]/speeds[e]/(rho if e in event_set else 1) for e in candidate["edge_ids"])
                if not math.isclose(cd, direct_d, abs_tol=1e-9) or not math.isclose(ca, direct_a, abs_tol=1e-9):
                    raise ValueError("Route decomposition and direct edge sum disagree")
                costs.append({"preset": preset["id"], "speed_multiplier": rho,
                              "default_estimated_cost_s": cd, "alternative_estimated_cost_s": ca,
                              "alternative_minus_default_s": ca-cd,
                              "chosen_route": candidate["id"] if ca < cd-1e-9 else default["id"]})
            if threshold is not None:
                cd = d["outside_event_cost_s"]+d["event_baseline_cost_s"]/threshold
                ca = a["outside_event_cost_s"]+a["event_baseline_cost_s"]/threshold
                if not math.isclose(cd, ca, abs_tol=1e-8):
                    raise ValueError("Critical multiplier fails equality check")
            comparisons.append({"alternative": a, "shared_event_edges": shared,
                                "shared_event_length_m": sum(lengths[e] for e in shared),
                                "avoidable_default_event_edges": avoidable,
                                "avoidable_default_event_length_m": sum(lengths[e] for e in avoidable),
                                "alternative_additional_full_route_length_m": a["route_length_m"]-d["route_length_m"],
                                "delta_outside_cost_s": extra, "delta_event_cost_s": saved,
                                "critical_speed_multiplier": threshold,
                                "threshold_meaning": "Alternative wins only below critical multiplier; equality retains default. Derived only for this static complete-path cost rule, not a physical queue-optimal threshold.",
                                "costs_at_declared_presets": costs})
        observed = [r for r in summary["runs"] if r["profile"] == fitted["id"] and r["policy"] == "A"]
        if any(r["changed_route_count"] for r in observed):
            raise ValueError("Frozen route choices no longer agree with the no-switch explanation")
        profiles.append({"profile": fitted["id"], "profile_passes_training_screen": fitted["passes_consistency_screen"],
                         "default": d, "comparisons": comparisons,
                         "observed_guided_run_count": len(observed), "observed_changed_route_count": sum(r["changed_route_count"] for r in observed)})
    estimate = basis["estimator"]
    report = {"schema_version": 1, "purpose": "Analytic explanation of the retained default route in frozen governance V2",
              "derivation": {"route_cost": "C_r(rho) = O_r + E_r / rho",
                             "definitions": "E_r=sum(length/fitted_effective_speed) on event edges; O_r same sum on all other edges",
                             "difference": "C_alternative-C_default = (O_alternative-O_default) - (E_default-E_alternative)/rho",
                             "critical_multiplier": "rho_star=(E_default-E_alternative)/(O_alternative-O_default), when numerator and denominator are positive",
                             "shared_edge_cancellation": "Identical shared event edges have identical fitted speed and multiplier, hence cancel exactly in the route-cost difference",
                             "tie_break": "Default route retained when costs tie; alternate requires a strictly lower current known cost"},
              "training_observed_effective_speed_ratio_range": [estimate["minimum_speed_kmh"]/estimate["median_speed_kmh"], estimate["maximum_speed_kmh"]/estimate["median_speed_kmh"]],
              "declared_preset_multipliers": {p["id"]: p["speed_multiplier"] for p in basis["presets"]},
              "profiles": profiles, "source_sha256": sources,
              "limitations": ["Costs are algorithm A's static estimates, not simulated trip outcomes or observed vehicle times.",
                              "Both declared severity levels remain above the computed switching thresholds; thresholds below the training minimum ratio have no support in this observation window.",
                              "No parameter, event edge, or governance output is modified to create a positive result.",
                              "Profiles failing the training consistency screen are shown only as diagnostics; thresholds are not unique true-traffic quantities."]}
    (OUT / "route_choice_explanation.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    first = profiles[0]["comparisons"][0]
    lines = ["# 为什么 V2 的 A 策略没有改道", "",
             "先看直觉：两条去医院外围的候选路线最后都要经过同一段瑞金二路。把这段共同道路变慢，两条路线都要付出代价；绕行真正能够避开的，只是上游较短的一段。现有观测支持的慢行变化，还不足以抵消绕行多出来的路程成本。", "",
             f"完整事件步骤模型长度为 {profiles[0]['default']['event_length_m']:.6f} 米，其中两候选共同经过 {first['shared_event_length_m']:.6f} 米，只能避开 {first['avoidable_default_event_length_m']:.6f} 米。备选完整路线还比默认路线多 {first['alternative_additional_full_route_length_m']:.6f} 米。这里长度来自冻结的V2路网，所有时间成本使用每个需求剖面已拟合的各边有效速度。", "",
             "## 公式怎样得到", "",
             "把每条路线的成本分成两部分：`O`是事件之外各边的长度/有效速度之和，`E`是事件之内各边在未施加事件倍率时的同类成本。事件将这些边的有效速度乘以ρ，因此：", "",
             "`C_default(ρ) = O_default + E_default / ρ`", "",
             "`C_alternative(ρ) = O_alternative + E_alternative / ρ`", "",
             "两条路线共同事件边的长度、原速度和倍率完全相同，相减后精确抵消。于是：", "",
             "`C_alternative − C_default = ΔO − ΔE / ρ`", "",
             "其中 `ΔO = O_alternative − O_default` 是绕行增加的事件外成本，`ΔE = E_default − E_alternative` 是能够避开的事件内成本。两者在本项目都为正。令差值为零，得到临界倍率：", "",
             "`ρ* = ΔE / ΔO`", "",
             "只有 `ρ < ρ*` 时，当前这条静态成本规则才会选择备选；相等时按既定规则保留默认。这里没有预测未来队列，也不预知恢复时刻，因此它不是全交通系统的最优阈值。", "",
             "## 用冻结参数检算", "",
             "| 需求剖面 | 训练筛选通过 | 可避事件内原成本ΔE(秒) | 绕行增加事件外成本ΔO(秒) | 临界倍率ρ* |",
             "|---|---|---:|---:|---:|"]
    for p in profiles:
        for c in p["comparisons"]:
            lines.append(f"| {p['profile']} | {p['profile_passes_training_screen']} | {c['delta_event_cost_s']:.6f} | {c['delta_outside_cost_s']:.6f} | {c['critical_speed_multiplier']:.9f} |")
    lo, hi = report["training_observed_effective_speed_ratio_range"]
    lines += ["", f"本次事件倍率为 **{basis['presets'][0]['speed_multiplier']:.9f}** 与 **{basis['presets'][1]['speed_multiplier']:.9f}**，都高于临界倍率。即使取本训练窗口最小有效速度，其相对中位数倍率也约为 **{lo:.6f}**，仍高于通过训练筛选的约0.13／0.118阈值。", "",
              "因此，A在16组治理运行里全部保留默认路线，符合其已声明的成本机制。把事件进一步调强直到它改道，会超出当前训练变化的支持范围；我们保留负结果。18秒、15秒需求剖面未通过训练一致性筛选，其阈值只作诊断。", "",
              "以下是A用于选路的估计成本，不是SUMO实际车辆旅行时间：", "",
              "| 需求剖面 | 强度 | 默认估计成本(秒) | 备选估计成本(秒) | 备选多耗时(秒) |", "|---|---|---:|---:|---:|"]
    for p in profiles:
        for c in p["comparisons"]:
            for item in c["costs_at_declared_presets"]:
                lines.append(f"| {p['profile']} | {item['preset']} | {item['default_estimated_cost_s']:.6f} | {item['alternative_estimated_cost_s']:.6f} | {item['alternative_minus_default_s']:.6f} |")
    lines += ["", "检算独立逐边求和，并在临界倍率检查两条路线成本相等；与冻结summary中A的改道计数核对。来源SHA-256见[机器结果](route_choice_explanation.json)。", "",
              "复现：`python3 -B outputs/governance_v2/explain_route_choice.py`。此脚本仅生成本说明及对应JSON，不修改冻结治理程序、运行结果或原manifest。"]
    (OUT / "route_choice_explanation.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"profiles": len(profiles), "source_hashes": len(sources), "default_retained_in_all_guided_runs": True}))


if __name__ == "__main__":
    explain()
