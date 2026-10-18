"""Audit paired rescue times, ordinary costs, controls and structural changes."""
# AI辅助：OpenAI Codex；提交前据真实记录补齐工具信息并人工复核。
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from .paths import APP
from .governance_report import agent_costs, audit_decisions
from .emergency import VEHICLE, DISPATCH


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_emergency(directory, meta):
    scenario = meta["emergency_scenario"]
    log = pd.read_csv(directory / "emergency_commands.csv")
    task = scenario["with_task"]
    event = scenario["with_event"]
    starts, ends = log[log.command.eq("incident_start")], log[log.command.eq("incident_end")]
    expected_start = scenario["incident"]["begin_seconds"]
    expected_end = scenario["incident"]["end_seconds"]
    if event:
        if len(starts) != 1 or len(ends) != 1 or starts.iloc[0].simulation_seconds != expected_start or ends.iloc[0].simulation_seconds != expected_end:
            raise ValueError("Incident start/end differs from frozen schedule")
        lane = next(l for l in ET.parse(directory / "network.net.xml").getroot().iter("lane")
            if l.get("id") == scenario["incident"]["lane"])
        if (json.loads(starts.iloc[0].details)["speed_limit_mps"] != scenario["incident"]["speed_limit_mps_assumed"]
                or json.loads(ends.iloc[0].details)["speed_limit_mps"] != float(lane.get("speed"))):
            raise ValueError("Incident speed limit or restoration is incorrect")
    elif len(starts) or len(ends):
        raise ValueError("Unexpected incident in no-event run")
    trace = pd.read_csv(directory / "emergency_trace_2s.csv")
    if len(trace) and (not trace.speed_mode.eq(31).all() or not trace.lane_change_mode.eq(1621).all()):
        raise ValueError("Emergency proxy safety modes changed")
    yielded, extended = 0, 0
    for r in log.itertuples():
        details = json.loads(r.details)
        if r.command == "safe_lane_change_request":
            if meta["emergency_policy"] == "baseline" or details["lane_change_mode"] != 1621 or not 0 < details["gap_m"] < scenario["yield"]["lookahead_m_assumed"]:
                raise ValueError("Yield command violates archived assumptions")
            yielded += 1
        elif r.command == "extend_existing_green":
            if meta["emergency_policy"] != "combined" or details["state"] not in "gG" or details["extra_seconds"] > scenario["signal"]["maximum_extension_s_assumed"]:
                raise ValueError("Signal command violates green-only extension")
            if details["state_unchanged"][details["link_index"]] not in "gG":
                raise ValueError("Extended link was not green")
            extended += 1
    route_root = ET.parse(directory / "vehroute.xml").getroot()
    routes = [v for v in route_root.findall("vehicle") if v.get("id") == VEHICLE]
    trips = [v for v in ET.parse(directory / "tripinfo.xml").getroot().findall("tripinfo") if v.get("id") == VEHICLE]
    if not task:
        if routes or trips or len(trace):
            raise ValueError("Task appeared in no-task ablation")
        return {"rescue_completed": False, "rescue_elapsed_s": np.nan, "rescue_elapsed_lower_bound_s": np.nan,
            "rescue_departure_delay_s": np.nan, "rescue_within_design_threshold": False,
            "yield_requests": yielded, "green_extensions": extended}
    if len(routes) != 1 or len(trips) != 1:
        raise ValueError("Missing or duplicate rescue proxy")
    actual = routes[0].find("route").get("edges").split()
    if actual != scenario["fixed_route_edges"]:
        raise ValueError("Rescue route changed across policies")
    t = trips[0]
    arrival, depart = float(t.get("arrival")), float(t.get("depart"))
    done = arrival >= 0
    elapsed = arrival - DISPATCH if done else np.nan
    notify = json.loads((directory / "emergency_result.json").read_text(encoding="utf-8"))
    if done and (notify["arrival_seconds"] is None or abs(notify["arrival_seconds"] - arrival) > meta["step_seconds"] + 1e-6):
        raise ValueError("Arrival notification differs from authoritative tripinfo by more than one step")
    return {"rescue_completed": done, "rescue_elapsed_s": elapsed,
        "rescue_elapsed_lower_bound_s": (arrival if done else meta["simulation_end_seconds"]) - DISPATCH,
        "rescue_departure_delay_s": depart - DISPATCH if depart >= 0 else np.nan,
        "rescue_within_design_threshold": bool(done and elapsed <= scenario["deadline_seconds_assumed"]),
        "yield_requests": yielded, "green_extensions": extended}


def report_emergency(prefix="stage05_main"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    suite_dir = APP / "results" / (prefix + "_suite")
    spec = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))["specification"]
    output = APP / "results" / (prefix + "_report")
    output.mkdir(exist_ok=True)
    source_ok = {n: sha(suite_dir / "frozen_sources" / n) == h for n, h in spec["source_sha256"].items()}
    if not all(source_ok.values()):
        raise ValueError("Frozen emergency source archive changed")
    rows = []
    for r in spec["runs"]:
        directory = APP / "results" / r["run_id"]
        meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        if meta.get("status") != "completed" or meta["seed"] != r["seed"] or meta["emergency_policy"] != r["policy"]:
            raise ValueError("Incomplete or mismatched emergency run")
        for filename, key in [("network.net.xml", "network_sha256"), ("demand.rou.xml", "demand_sha256"),
                ("vehicles.csv", "vehicle_manifest_sha256"), ("meter_plan.json", "meter_plan_sha256"),
                ("emergency_scenario.json", "emergency_scenario_sha256"),
                ("emergency_network_definition.json", "emergency_network_definition_sha256")]:
            if sha(directory / filename) != meta[key]:
                raise ValueError("Archived emergency input hash changed: " + r["run_id"] + "/" + filename)
        child = output / r["run_id"]
        child.mkdir(exist_ok=True)
        cost = agent_costs(directory, child)
        full_child = child / "full_observation"
        full_child.mkdir(exist_ok=True)
        full_cost = agent_costs(directory, full_child, meta["simulation_end_seconds"])
        control = audit_decisions(directory, meta)
        audit = audit_emergency(directory, meta)
        m = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        rows.append({**r, **audit, **cost,
            "ordinary_evaluation_vehicle_hours": cost["core_evaluation_vehicle_hours"] + cost["background_evaluation_vehicle_hours"],
            "ordinary_vehicle_hours": full_cost["core_evaluation_vehicle_hours"] + full_cost["background_evaluation_vehicle_hours"],
            "full_core_vehicle_hours": full_cost["core_evaluation_vehicle_hours"],
            "full_background_vehicle_hours": full_cost["background_evaluation_vehicle_hours"],
            "planned": m["trip_accounting"]["planned"], "unfinished": m["trip_accounting"]["unfinished_after_clearance"],
            "teleports": m["teleports"], "collisions": m["collisions"],
            "conservation_residual": m["accounting"]["conservation_residual"], "meter_decisions": control["decisions"]})
    table = pd.DataFrame(rows)
    main = table[table.role.eq("paired")]
    pairs = []
    for seed, group in main.groupby("seed"):
        base = group[group.policy.eq("baseline")].iloc[0]
        for r in group.itertuples():
            for filename in ["network.net.xml", "demand.rou.xml", "vehicles.csv", "meter_plan.json",
                    "boundary_supply.json", "emergency_scenario.json"]:
                if (APP / "results" / r.run_id / filename).read_bytes() != (APP / "results" / base.run_id / filename).read_bytes():
                    raise ValueError("Emergency paired exogenous inputs differ: " + filename)
            if r.policy != "baseline":
                pairs.append({"seed": seed, "policy": r.policy,
                    "rescue_horizon_exposure_change_pct": 100 * (r.rescue_elapsed_lower_bound_s / base.rescue_elapsed_lower_bound_s - 1),
                    "rescue_horizon_exposure_change_s": r.rescue_elapsed_lower_bound_s - base.rescue_elapsed_lower_bound_s,
                    "rescue_time_change_pct": 100 * (r.rescue_elapsed_s / base.rescue_elapsed_s - 1),
                    "rescue_time_change_s": r.rescue_elapsed_s - base.rescue_elapsed_s,
                    "ordinary_cost_change_pct": 100 * (r.ordinary_vehicle_hours / base.ordinary_vehicle_hours - 1),
                    "ordinary_evaluation_cost_change_pct": 100 * (r.ordinary_evaluation_vehicle_hours / base.ordinary_evaluation_vehicle_hours - 1),
                    "core_cost_change_pct": 100 * (r.full_core_vehicle_hours / base.full_core_vehicle_hours - 1),
                    "background_cost_change_pct": 100 * (r.full_background_vehicle_hours / base.full_background_vehicle_hours - 1),
                    "unfinished_change": r.unfinished - base.unfinished})
    paired = pd.DataFrame(pairs)
    table.to_csv(output / "all_runs.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(output / "paired_changes.csv", index=False, encoding="utf-8-sig")
    summary = main.groupby("policy", sort=False).agg(runs=("seed", "size"), completed=("rescue_completed", "sum"),
        mean_task_experienced_until_end_s=("rescue_elapsed_lower_bound_s", "mean"), max_task_experienced_until_end_s=("rescue_elapsed_lower_bound_s", "max"),
        completed_only_mean_rescue_elapsed_s=("rescue_elapsed_s", "mean"),
        within_design_threshold=("rescue_within_design_threshold", "sum"),
        mean_ordinary_vehicle_hours=("ordinary_vehicle_hours", "mean"), mean_unfinished=("unfinished", "mean"),
        yield_requests=("yield_requests", "sum"), green_extensions=("green_extensions", "sum"))
    summary.to_csv(output / "summary.csv", encoding="utf-8-sig")
    labels = {"baseline": "无专门协同", "yield": "周边车辆让行", "combined": "让行＋既有绿灯延长"}
    colors = {"baseline": "#667789", "yield": "#b38848", "combined": "#237b68"}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    for _, g in main.groupby("seed"):
        g = g.set_index("policy").loc[["baseline", "yield", "combined"]]
        axes[0].plot([0, 1, 2], g.rescue_elapsed_lower_bound_s, color="#99a4ad", alpha=.45, linewidth=.8)
    for i, policy in enumerate(["baseline", "yield", "combined"]):
        g = main[main.policy.eq(policy)]
        axes[0].scatter([i] * len(g), g.rescue_elapsed_lower_bound_s, color=colors[policy])
        missing = g[~g.rescue_completed]
        axes[0].scatter([i] * len(missing), missing.rescue_elapsed_lower_bound_s, marker="x", color="#bd584d", s=80)
        if policy != "baseline":
            changes = paired[paired.policy.eq(policy)]
            axes[1].scatter([i] * len(changes), changes.ordinary_cost_change_pct, color=colors[policy])
    for ax in axes:
        ax.set_xticks([0, 1, 2], ["No dedicated aid", "Yield requests", "Yield + green extension"])
        ax.grid(axis="y", alpha=.25)
    axes[0].set_ylabel("Task experienced seconds until fixed end (x = unfinished)")
    axes[1].set_ylabel("Ordinary all-agent exposure change (%)")
    axes[1].axhline(0, color="grey", linestyle=":")
    fig.suptitle("Designed event | 5 paired seeds | Public-road endpoint, not hospital handover", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, .94))
    fig.savefig(output / "emergency_comparison.png", dpi=160)
    plt.close(fig)

    no_task = table[table.role.eq("topology_only")].iloc[0]
    fourth = APP / "results/stage04_main_s20261017_feedback"
    child = output / "stage04_topology_reference"
    child.mkdir(exist_ok=True)
    previous = agent_costs(fourth, child)
    ordinary_same = all((APP / "results" / no_task.run_id / name).read_bytes() == (fourth / name).read_bytes()
        for name in ["core_vehicles.csv", "background_vehicles.csv"])
    topology_change = 100 * (no_task.ordinary_evaluation_vehicle_hours / (previous["core_evaluation_vehicle_hours"] + previous["background_evaluation_vehicle_hours"]) - 1)
    task_no_event = table[table.role.eq("task_no_event")].iloc[0]
    seed_base = main[main.seed.eq(20261017) & main.policy.eq("baseline")].iloc[0]
    ablation = {"same_ordinary_agents_as_stage04": ordinary_same, "topology_only_ordinary_cost_change_pct": topology_change,
        "seed17_task_without_lane_restriction_s": task_no_event.rescue_elapsed_s,
        "seed17_task_with_lane_restriction_s": seed_base.rescue_elapsed_s,
        "note": "Topology change is not a control benefit; one-seed ablations diagnose model structure only."}
    (output / "ablations.json").write_text(json.dumps(ablation, ensure_ascii=False, indent=2), encoding="utf-8")
    verified = bool(table.teleports.eq(0).all() and table.collisions.eq(0).all() and table.conservation_residual.eq(0).all())
    verification = {"actual_runs": len(table), "paired_runs": len(main), "paired_seeds": int(main.seed.nunique()),
        "identical_exogenous_inputs_within_seed": True, "all_archived_input_hashes_verified": True,
        "frozen_source_archive_verified": source_ok, "all_emergency_commands_and_safety_modes_verified": True,
        "all_meter_decisions_verified": int(table.meter_decisions.sum()),
        "all_runs_zero_teleports_collisions_and_conservation_residual": verified,
        "all_paired_rescue_tasks_completed": bool(main.rescue_completed.all()),
        "ordinary_primary_cost_window": "2026-10-15T08:00:00+08:00--2026-10-15T09:30:00+08:00",
        "actual_ambulance_gate_verified": False, "real_peak_baseline_validated": False,
        "official_live_assimilation": False}
    (output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    content = """# 第五轮：应急任务与交通协同

已完成公共道路接入、设计事件、三策略和五组配对种子。**这是设计应急情景；真实高峰基准、救护专用入口、医院内部及病人交接均未验证。**

## 做了什么

官方医院数据确认伊利沙伯医院设急症室、地址为加士居道30号。补充OSM拓扑得到从公主道到伊利沙伯医院路的约2.18公里有向路径，路网由98扩为125条外部边；保留沿途信号节点的相关道路。终点是`149555152`公共道路末端，**不是已确认的救护入口，也不把到路末当病人送达急诊**。

- 事件：08:30—08:45，`771547540_0`车道限速3m/s，结束恢复原限速。它代表设计的单车道低速作业，不是实际事故记录。
- 任务：08:35从`1433971795`出发，固定路线到公共道路终点；三组使用同一车辆类型、出发与权限。
- 对照：无专门协同、周边车辆让行、让行加既有绿灯延长。三组均保留第四轮同一反馈入口控制、背景900及名义外部供给900，清空1800秒。
- 让行：每2秒对前方80米同车道车辆请求安全换道；保留SUMO默认安全检查，请求不等于换道必定执行。
- 绿灯：120米内遇到既有绿灯时最多延长10秒，同一绿灯阶段只延长一次；不跳过红灯、黄灯或清空，不切换为冲突绿灯。

以上距离、时刻、配时及阈值均是预设实验参数，未据五种子结果反复调整。车辆采用普通通行权限，无蓝灯特权、强制速度、位置搬移或关闭安全检查；不把策略收益与车辆特权变化混在一起。

## 直接看结果

| 策略 | 完成任务 | 截止已耗时均值 秒 | 截止已耗时最大 秒 | 普通交通经历量 辆·小时 | 清空后未完成 均值 |
|---|---:|---:|---:|---:|---:|
"""
    for policy in ["baseline", "yield", "combined"]:
        r = summary.loc[policy]
        content += f"| {labels[policy]} | {r.completed:.0f}/5 | {r.mean_task_experienced_until_end_s:.1f} | {r.max_task_experienced_until_end_s:.1f} | {r.mean_ordinary_vehicle_hours:.2f} | {r.mean_unfinished:.1f} |\n"
    content += "\n时间从计划调度算起，包含入口等待，以tripinfo为准；未完成任务在共同09:30截止计入3300秒已耗时。这是可比较的固定截止经历量，未完成者只给最终旅行时间下界；不能将均值称为最终平均救援时间。TraCI到达通知最多晚一个0.5秒步。\n\n普通交通主成本覆盖08:00—09:30，包含全部核心和背景车辆的等待及行驶，含未发车和未完成，救援车辆另计。这个窗口覆盖09:00之后的应急控制；08:00—09:00成本另存CSV，不能以它漏掉后续控制代价。\n\n"
    for policy in ["yield", "combined"]:
        g = paired[paired.policy.eq(policy)]
        content += (f"- {labels[policy]}：固定截止任务经历量平均变化{g.rescue_horizon_exposure_change_pct.mean():+.2f}%，"
            f"范围[{g.rescue_horizon_exposure_change_pct.min():+.2f}%, {g.rescue_horizon_exposure_change_pct.max():+.2f}%]；"
            f"{g.rescue_time_change_s.lt(0).sum()}/5个种子更快。普通交通成本平均{g.ordinary_cost_change_pct.mean():+.2f}%，"
            f"范围[{g.ordinary_cost_change_pct.min():+.2f}%, {g.ordinary_cost_change_pct.max():+.2f}%]。\n")
    content += "\n![应急与普通交通配对结果](emergency_comparison.png)\n\n"
    if summary.loc["yield", "completed"] < summary.loc["baseline", "completed"] or paired.rescue_horizon_exposure_change_pct.mean() > 0:
        content += "当前让行和组合方案没有改善整体应急表现，完成率还下降，不建议把它们作为最终优选策略。应先检验请求频率、目标车道可用空间及路线瓶颈；不能删掉未完成种子后用完成者均值宣称提升。\n\n"
    content += (f"共记录让行请求{int(main.yield_requests.sum())}次、绿灯延长{int(main.green_extensions.sum())}次。"
        "有命令不等于有收益，未改善或变差的种子都保留。180秒仅作预设设计阈值，不是当地救护标准；"
        f"各组达标数量为无协同{int(summary.loc['baseline','within_design_threshold'])}/5、"
        f"让行{int(summary.loc['yield','within_design_threshold'])}/5、组合{int(summary.loc['combined','within_design_threshold'])}/5。\n\n")
    content += ("## 结构与事件检查\n\n"
        f"新增医院道路、无事件无救援的单种子组，相对第四轮同一普通车辆输入，固定时窗成本变化{topology_change:+.2f}%。"
        "这是路网结构影响，不能计作应急治理收益；正式救援比较全部在125边网络内配对。\n\n"
        f"同种子无单车道限速的任务耗时{task_no_event.rescue_elapsed_s:.1f}秒，含限速无协同为{seed_base.rescue_elapsed_s:.1f}秒。"
        "另保留仅事件、无任务组，用于辨认事件与任务影响；单种子消融不构成泛化证据。\n\n"
        "## 核验与局限\n\n"
        f"15次配对加3次消融共{len(table)}次：输入哈希、同种子需求/车辆/路径/事件/设施一致性、固定救援路径、事件起止及限速恢复、"
        "安全模式和绿灯延长记录均核验。瞬移、碰撞、全体守恒及完成情况见verification.json；急刹等警告在各运行原日志保留。\n\n"
        "沿途新纳入的本地道路交叉流量与真实配时缺失；新路不额外注入没有依据的本地交通。生成信号、交通需求、事件和控制因此都带假设，"
        "当前效果不能直接外推为实际救护时间。固定路线仅检验通行协同，不宣称动态路径优化。官方实时观测尚未同化。\n\n"
        "## 复现与输出\n\n"
        "```powershell\npython 应用/run.py build-emergency-access\n"
        "python 应用/run.py emergency-suite --prefix stage05_main --jobs 3\n"
        "python 应用/run.py report-emergency --prefix stage05_main\n"
        "python 应用/run.py dashboard --run-id stage05_main_s20261017_combined\n"
        "python 管理中心/更新页面.py\n```\n\n"
        "每次运行保留emergency_scenario.json、emergency_commands.csv、emergency_trace_2s.csv、固定车辆、实际路线、行程及SUMO日志。"
        "本报告目录保存all_runs.csv、paired_changes.csv、summary.csv、ablations.json与逐组population_costs.csv。"
        "批量参数及源码副本在同名前缀suite目录，修改模型需新前缀；试跑和失败保留。\n")
    path = output / "第五轮开发诊断.md"
    path.write_text(content, encoding="utf-8")
    return {"report": str(path), **verification,
        "paired_changes": paired.groupby("policy")[["rescue_horizon_exposure_change_pct", "ordinary_cost_change_pct"]].mean().to_dict()}
