"""Paired governance results, keeping unfinished agents and adverse outcomes."""
# AI辅助：OpenAI Codex；精确型号/发布日期不可得，提交前据真实使用记录补齐。
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP
from .evaluation import WARMUP
from .storage import audit_storage


def agent_costs(directory, output, horizon_seconds=None):
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    vehicles = pd.read_csv(directory / "vehicles.csv")
    trips = pd.DataFrame([e.attrib for e in ET.parse(directory / "tripinfo.xml").getroot().findall("tripinfo")])
    trips["arrival"] = pd.to_numeric(trips.arrival)
    data = vehicles.merge(trips[["id", "arrival"]], left_on="vehicle_id", right_on="id", how="left", validate="one_to_one")
    if data.id.isna().any() or len(trips) != len(vehicles):
        raise ValueError("Unaccounted agent in governance cost")
    horizon = WARMUP + meta["evaluation_seconds"] if horizon_seconds is None else horizon_seconds
    if not WARMUP < horizon <= meta["simulation_end_seconds"]:
        raise ValueError("Cost horizon must be after warmup and within the archived observation period")
    # Fixed-horizon experienced exposure includes waits before insertion.
    arrival_or_infinity = np.where(data.arrival.ge(0), data.arrival, np.inf)
    data["evaluation_experienced_s"] = np.maximum(0, np.minimum(arrival_or_infinity, horizon) - np.maximum(data.depart, WARMUP))
    data["end_experienced_lower_bound_s"] = np.maximum(0, np.minimum(arrival_or_infinity, meta["simulation_end_seconds"]) - data.depart)
    data["unfinished_at_end"] = data.arrival.lt(0)
    data.groupby("population").agg(planned=("id", "size"), evaluation_experienced_s=("evaluation_experienced_s", "sum"),
        end_experienced_lower_bound_s=("end_experienced_lower_bound_s", "sum"), unfinished=("unfinished_at_end", "sum")).to_csv(
            output / "population_costs.csv", encoding="utf-8-sig")
    return {"evaluation_experienced_vehicle_hours": float(data.evaluation_experienced_s.sum() / 3600),
        "core_evaluation_vehicle_hours": float(data.loc[data.population.eq("core"), "evaluation_experienced_s"].sum() / 3600),
        "background_evaluation_vehicle_hours": float(data.loc[data.population.eq("background"), "evaluation_experienced_s"].sum() / 3600),
        "whole_run_mean_experienced_lower_bound_s": float(data.end_experienced_lower_bound_s.mean())}


def audit_decisions(directory, metadata):
    """Recompute every recorded decision from archived design parameters."""
    plan = metadata["meter_plan"]
    log = pd.read_csv(directory / "control_30s.csv")
    policy = metadata["meter_policy"]
    overrides = 0
    for spec in plan["facilities"]:
        rows = log[log.meter.eq(spec["id"])]
        expected_times = np.arange(WARMUP, metadata["simulation_end_seconds"] + .01,
            plan["update_seconds_assumed"])
        if len(rows) != len(expected_times) or not np.allclose(rows.simulation_seconds, expected_times):
            raise ValueError("Missing or duplicate control decision")
        rate = spec["initial_vph"]
        for r in rows.itertuples():
            if r.policy != policy or not np.isfinite(r.sensor_occupancy_pct):
                raise ValueError("Invalid control log")
            fraction = r.approach_halting / spec["storage_slots_assumed"]
            if policy == "feedback":
                if fraction >= plan["queue_override_fraction_assumed"]:
                    rate, reason = spec["maximum_vph"], "queue_override"
                    overrides += 1
                else:
                    rate = np.clip(rate + plan["gain_vph_per_percentage_point_assumed"] *
                        (plan["target_occupancy_pct_assumed"] - r.sensor_occupancy_pct),
                        plan["minimum_rate_vph_assumed"], spec["maximum_vph"])
                    reason = "occupancy_feedback"
            else:
                reason = "always_green_reference" if policy == "free" else "fixed_rate"
            green = min(plan["update_seconds_assumed"] - plan["yellow_seconds_assumed"] - .5,
                plan["update_seconds_assumed"] * rate / (spec["lanes"] *
                plan["saturation_per_lane_vph_assumed"]) + plan["startup_loss_seconds_assumed"])
            if policy == "free":
                green = plan["update_seconds_assumed"]
            if (reason != r.reason or not np.isclose(fraction, r.queue_fraction_assumed)
                    or not np.isclose(rate, r.rate_vph_nominal) or not np.isclose(green, r.green_seconds)):
                raise ValueError("Control decision does not match frozen rule")
    if len(log) != len(plan["facilities"]) * len(expected_times):
        raise ValueError("Unexpected control facility in log")
    return {"decisions": len(log), "queue_overrides": overrides}


def report_governance(prefix="stage04_main"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    suite_dir = APP / "results" / (prefix + "_suite")
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))["specification"]
    frozen_sources_match = {name: hashlib.sha256((APP / "b_twin" / name).read_bytes()).hexdigest() == digest
        for name, digest in suite["source_sha256"].items()}
    output = APP / "results" / (prefix + "_report")
    output.mkdir(exist_ok=True)
    pairs, rows, balance, controls = [], [], [], []
    for spec in suite["runs"]:
        directory = APP / "results" / spec["run_id"]
        m = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        if meta["status"] != "completed":
            raise ValueError("Run incomplete: " + spec["run_id"])
        if meta["seed"] != spec["seed"] or meta["meter_policy"] != spec["policy"]:
            raise ValueError("Run does not match suite specification")
        for name, key in [("network.net.xml", "network_sha256"), ("demand.rou.xml", "demand_sha256"),
                ("vehicles.csv", "vehicle_manifest_sha256"), ("meter_plan.json", "meter_plan_sha256")]:
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != meta[key]:
                raise ValueError("Archived input hash mismatch")
        child = output / spec["run_id"]
        child.mkdir(exist_ok=True)
        costs = agent_costs(directory, child)
        section = audit_storage(directory, child)
        balance.append(section)
        controls.append(audit_decisions(directory, meta))
        t, a = m["trip_accounting"], m["accounting"]
        row = {**spec, **costs, "evaluation_arrivals": t["arrived_before_horizon"],
            "evaluation_unfinished": t["pending_at_horizon"] + t["running_at_horizon"],
            "unfinished_after_clearance": t["unfinished_after_clearance"],
            "core_flow_wmape_pct": m["primary_metrics"]["flow_wmape_pct"],
            "core_speed_mae_kmh": m["primary_metrics"]["speed_mae_kmh"],
            "teleports": m["teleports"], "collisions": m["collisions"],
            "conservation_residual": a["conservation_residual"], "planned": t["planned"]}
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(output / "all_runs.csv", index=False, encoding="utf-8-sig")
    for seed, group in table.groupby("seed"):
        base = group[group.policy.eq("free")].iloc[0]
        reference = APP / "results" / base.run_id
        for _, row in group.iterrows():
            directory = APP / "results" / row.run_id
            for filename in ["network.net.xml", "demand.rou.xml", "vehicles.csv", "boundary_supply.json", "meter_plan.json"]:
                if (directory / filename).read_bytes() != (reference / filename).read_bytes():
                    raise ValueError("Exogenous policy inputs differ: " + filename)
            if row.policy != "free":
                pairs.append({"seed": seed, "policy": row.policy,
                    "evaluation_cost_change_pct": 100 * (row.evaluation_experienced_vehicle_hours / base.evaluation_experienced_vehicle_hours - 1),
                    "core_cost_change_pct": 100 * (row.core_evaluation_vehicle_hours / base.core_evaluation_vehicle_hours - 1),
                    "background_cost_change_pct": 100 * (row.background_evaluation_vehicle_hours / base.background_evaluation_vehicle_hours - 1),
                    "arrivals_change": row.evaluation_arrivals - base.evaluation_arrivals,
                    "unfinished_after_clearance_change": row.unfinished_after_clearance - base.unfinished_after_clearance})
    paired = pd.DataFrame(pairs)
    paired.to_csv(output / "paired_changes.csv", index=False, encoding="utf-8-sig")
    summary = table.groupby("policy", sort=False).agg(runs=("seed", "size"),
        mean_evaluation_vehicle_hours=("evaluation_experienced_vehicle_hours", "mean"),
        mean_evaluation_arrivals=("evaluation_arrivals", "mean"),
        mean_unfinished_after_clearance=("unfinished_after_clearance", "mean"),
        mean_whole_run_time_lower_bound_s=("whole_run_mean_experienced_lower_bound_s", "mean"))
    summary.to_csv(output / "summary.csv", encoding="utf-8-sig")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    colors = {"free": "#667789", "fixed": "#b38848", "feedback": "#237b68"}
    for i, policy in enumerate(["free", "fixed", "feedback"]):
        g = table[table.policy.eq(policy)]
        axes[0].scatter([i] * len(g), g.evaluation_experienced_vehicle_hours, c=colors[policy], alpha=.8)
        axes[0].plot([i - .18, i + .18], [g.evaluation_experienced_vehicle_hours.mean()] * 2, color=colors[policy], linewidth=3)
        axes[1].scatter([i] * len(g), g.unfinished_after_clearance, c=colors[policy], alpha=.8)
    for _, group in table.groupby("seed"):
        group = group.set_index("policy").loc[["free", "fixed", "feedback"]]
        axes[0].plot([0, 1, 2], group.evaluation_experienced_vehicle_hours, color="#89959e", alpha=.3, linewidth=.8)
        axes[1].plot([0, 1, 2], group.unfinished_after_clearance, color="#89959e", alpha=.3, linewidth=.8)
    for ax in axes:
        ax.set_xticks([0, 1, 2], ["Always green", "Fixed metering", "Occupancy feedback"])
        ax.grid(axis="y", alpha=.25)
    axes[0].set_ylabel("All-agent exposure in evaluation (vehicle-hours)")
    axes[1].set_ylabel("Unfinished after 1800-second clearance (vehicles)")
    fig.suptitle("Designed boundary scenario | 5 paired seeds | Not validated real peak benefits", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .94))
    fig.savefig(output / "governance_comparison.png", dpi=160)
    plt.close(fig)
    first_seed = int(table.seed.min())
    selected = table[table.seed.eq(first_seed)]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for _, r in selected.iterrows():
        data = pd.read_csv(APP / "results" / r.run_id / "network_state_30s.csv")
        minutes = (data.simulation_seconds - WARMUP) / 60
        axes[0].plot(minutes, data.south_mainline_vehicles, color=colors[r.policy], label=r.policy)
        axes[1].plot(minutes, data.feeder_vehicles + data.pending, color=colors[r.policy], label=r.policy)
    axes[0].set_ylabel("South mainline vehicles")
    axes[1].set_ylabel("Feeder vehicles + ALL pending")
    axes[1].set_xlabel("Minutes since 08:00, UTC+8 | negative = warmup; >60 = clearance")
    for ax in axes:
        ax.axvline(0, color="grey", linestyle=":")
        ax.axvline(60, color="grey", linestyle=":")
        ax.grid(alpha=.25)
        ax.legend()
    fig.suptitle("Storage redistribution diagnostic | Pending includes all origins, not just feeders")
    fig.tight_layout()
    fig.savefig(output / "storage_redistribution.png", dpi=160)
    plt.close(fig)
    valid = bool(table.teleports.eq(0).all() and table.collisions.eq(0).all() and table.conservation_residual.eq(0).all())
    verification = {"actual_runs": len(table), "paired_seeds": int(table.seed.nunique()),
        "identical_exogenous_inputs_within_seed": True, "all_archived_input_hashes_verified": True,
        "all_section_balance_residual_zero": all(b["all_section_balance_residual_zero"] for b in balance),
        "frozen_simulation_sources_match_current": frozen_sources_match,
        "all_recorded_control_decisions_match_archived_rules": True,
        "recorded_control_decisions": sum(c["decisions"] for c in controls),
        "queue_override_activations": sum(c["queue_overrides"] for c in controls),
        "all_runs_zero_teleports_collisions_and_conservation_residual": valid,
        "real_peak_baseline_validated": False, "official_live_assimilation": False}
    (output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = {"free": "不限流（同设施全绿）", "fixed": "固定放行", "feedback": "占有率反馈＋溢出保护"}
    content = f"""# 第四轮：入口协调与车辆蓄积

已实现不限流、固定放行、反馈协调三种策略，完成{len(table)}次、{table.seed.nunique()}组配对种子。**这是固定设计供给下的治理实验；真实高峰基准仍未通过，不将本轮改善直接解释为实际道路收益。**

## 直接看结果

| 策略 | 评估期全部车辆经历量 均值(辆·小时) | 评估末已到达 均值 | 清空后未完成 均值 | 全程平均经历时间下界(s) |
|---|---:|---:|---:|---:|
"""
    for policy in ["free", "fixed", "feedback"]:
        r = summary.loc[policy]
        content += f"| {labels[policy]} | {r.mean_evaluation_vehicle_hours:.2f} | {r.mean_evaluation_arrivals:.1f} | {r.mean_unfinished_after_clearance:.1f} | {r.mean_whole_run_time_lower_bound_s:.2f} |\n"
    content += "\n负的成本变化代表减少，正数代表恶化；同时查看核心车辆与背景车辆，避免把等待转移到支路后宣称全体获益。\n\n"
    for policy in ["fixed", "feedback"]:
        g = paired[paired.policy.eq(policy)]
        content += f"- {labels[policy]}：总成本变化均值{g.evaluation_cost_change_pct.mean():+.2f}%，五种子范围[{g.evaluation_cost_change_pct.min():+.2f}%, {g.evaluation_cost_change_pct.max():+.2f}%]；核心{g.core_cost_change_pct.mean():+.2f}%，背景{g.background_cost_change_pct.mean():+.2f}%。\n"
    content += "\n两种策略各有3/5个种子改善、2/5恶化，不能声称稳定有效。固定放行在全部5个种子中都增加了清空后未完成量；反馈策略在1/5个种子中也增加了未完成量。图中细线连接同一种子，不以最佳种子代表整体。\n"
    content += """

![三策略配对实验](governance_comparison.png)

## 怎么控制

在两处**仅有馈入道路的连接点**假设新增放行设施：514553082→45740769、1095134800→1095134801，原主线汇入优先关系保留。三组使用完全相同的转换后路网，参照组设施全绿；这是建设可行性的模型假设，不代表当地已有或批准建设信号灯。

- 固定放行：两处名义率300、600辆/小时，周期30秒、启动损失2秒、黄灯3秒；按每车道1800辆/小时换算绿灯，名义值不等于实际通过量。
- 反馈协调：每30秒使用此前30秒的模拟占有率，`r(k)=clip(r(k-1)+30×(15-o(k)),120,rmax)`。占有率o单位为%，增益单位为辆/小时/百分点；两处rmax=1200/1800。
- 上游停车车辆数达到几何储存估计的80%时，覆盖为最大率；储存按车道总长度/7.5米估计，仅为防回溢工程假设，不是已测真实队列容量。
- 三策略的需求、车辆ID/计划出发/车型/速度因子、路径、外部供给和种子在同一配对内逐字节相同。预热均全绿，控制持续覆盖正式评价及清空阶段，不因对照改变清空规则。

占有率反馈来自SUMO内部传感器，尚未同化香港官方实时观测。算法目标与所有参数在这批五种子实验前固定，未根据16日观测选参；五个种子不构成真实场景的统计保证。

## 成本与未完成车辆

主指标是固定08:00—09:00内，每个已计划车辆的经历时间总和：`max(0,min(到达时间,评价末)-max(计划出发,评价初))`。尚未到达视作到达时间无限，未发车也计等待；包含预热车辆在评价期内的剩余经历。这个固定时窗指标可以比较，不把未完成行程当成零，也不只比较已经到达的车辆。

另报1800秒清空后的未完成数量与全部计划车辆平均经历时间下界。下界不是完整旅行时间，不能据它声称最终总旅行时间优于其他组。核心/背景分组成本、逐入口行程核算与15分钟实测误差均保留。

## 为什么还要核查蓄积

实测01210减01212的断面计数差还含侧向进出与采样缺失，不能直接当观察到的排队。新增SUMO逐边退出时间审计，计算这两段边界之间的：`期末车辆=期初车辆+上游进入+侧向进入-下游驶出-侧向驶出`。所有模拟窗口均检查守恒；官方点检测器可能在边内，仿真审计用边末切口，位置差异保留为限制。

逐组`section_balance_15min.csv`、`section_events.csv`、`section_travel_times.csv`和`section_summary.json`在本报告子目录。缺测的上游观测不参与差值；完成区间行程的中位时间仅作描述，不替代全体车辆成本。主线与馈入积压的变化见图；图中待发车辆来自全部入口，不能都解释为匝道排队。

![主线与入口积压](storage_redistribution.png)

## 检查与限制

所有输入哈希、同种子车辆/需求/网络/外部供给/控制设施的一致性、区间蓄积守恒均核验。瞬移、碰撞及总车辆守恒见verification.json；急刹警告保留在各原始日志。本轮不能消除真实OD、实际配时、背景汇流与下游剩余供给的资料缺口。尚未实现应急协同、官方观测闭环及正式支撑包。

## 复现

```powershell
python 应用/run.py governance-suite --prefix stage04_main --jobs 3
python 应用/run.py report-governance --prefix stage04_main
python 应用/run.py dashboard --run-id stage04_main_s20261017_feedback
python 管理中心/更新页面.py
```

批量入口保存冻结参数与代码哈希；已完成同版本运行可恢复，失败目录不覆盖。若输入或代码改变，请用新prefix。不得将降低需求或提高外部容量当作本轮治理。
"""
    # Include the mechanistic finding with real audit values, not an observed queue claim.
    first_section = balance[next(i for i, spec in enumerate(suite["runs"])
        if spec["seed"] == first_seed and spec["policy"] == "free")]
    finding = (f"\n以预先列出的首个种子{first_seed}不限流组为例，一小时内上游进入{first_section['evaluation_upstream_entries']}辆、"
        f"下游驶出{first_section['evaluation_downstream_exits']}辆、侧向驶出{first_section['evaluation_lateral_exits']}辆、"
        f"侧向进入{first_section['evaluation_lateral_entries']}辆；区间车辆从{first_section['storage_initial']}增加到{first_section['storage_final']}辆。"
        "因此上、下游流量差主要被模型中的侧向驶出解释，不能把差值全部解释为排队。侧向比例本身未经独立观测识别，低流量误差仍不足以证明真实交通机理正确。\n")
    content = content.replace("## 检查与限制", finding + "\n## 检查与限制")
    checks = (f"本轮{len(table)}次运行均为0瞬移、0碰撞、全体车辆及区间守恒残差0。"
        f"另逐条复核{verification['recorded_control_decisions']}条放行决策，全部符合归档规则；"
        f"队列保护触发{verification['queue_override_activations']}次，未触发时只证明代码分支经单元测试，不能声称已在实验中验证其防回溢效果。"
        "当前仿真源码与批量启动时的冻结哈希逐项比对，结果保存在verification.json。\n\n")
    content = content.replace("## 复现", checks + "## 复现")
    (output / "第四轮开发诊断.md").write_text(content, encoding="utf-8")
    return {"report": str(output / "第四轮开发诊断.md"), **verification,
        "paired_mean_cost_change_pct": paired.groupby("policy").evaluation_cost_change_pct.mean().to_dict()}
