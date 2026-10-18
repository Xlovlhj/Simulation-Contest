"""Review the repaired through boundary and explicit supply sensitivity runs."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际记录补齐并人工复核。
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import pandas as pd

from .evaluation import CASES, WARMUP, aggregate_e1, primary_metrics
from .paths import APP, REFINED


def exit_arrivals(directory, physical_start, duration, exit_edge="45740459"):
    """Use terminal trip arrivals: an end-of-edge E1 may lose rear clearance.

    Vehicles disappear on arrival before their rear fully clears an E1 loop
    positioned only 0.1 m before the sink. nVehContrib is then not exit flow.
    This is simulated discharge, not an observed boundary capacity.
    """
    routes = {r.get("id"): r.get("edges").split()[-1]
        for r in ET.parse(directory / "demand.rou.xml").getroot().findall("route")}
    destinations = {v.get("id"): routes[v.get("route")]
        for v in ET.parse(directory / "demand.rou.xml").getroot().findall("vehicle")}
    counts = [0] * (duration // 900)
    for trip in ET.parse(directory / "tripinfo.xml").getroot().findall("tripinfo"):
        arrival = float(trip.get("arrival"))
        if destinations[trip.get("id")] == exit_edge and WARMUP <= arrival < WARMUP + duration:
            counts[int((arrival - WARMUP) // 900)] += 1
    return pd.DataFrame([{"window": (pd.Timestamp(physical_start) + pd.Timedelta(seconds=i * 900)).isoformat(),
        "simulated_exit_arrivals": n, "simulated_exit_flow_vph": n * 4,
        "basis": "Actual simulated terminal trip arrival timestamps; NOT observed capacity"}
        for i, n in enumerate(counts)])


def review_run(directory, output, core_ids):
    metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    compared = pd.read_csv(directory / "comparison_15min.csv")
    compared["reference_core_station"] = compared.detector_id.isin(core_ids)
    compared["used_in_primary_metrics"] = (compared.eligible.fillna(False).astype(bool)
        & ~compared.requires_review.astype(bool) & compared.reference_core_station)
    primary = primary_metrics(compared)
    child = output / directory.name
    child.mkdir(exist_ok=True)
    compared.to_csv(child / "comparison_fixed_core.csv", index=False, encoding="utf-8-sig")
    auxiliary = compared[~compared.reference_core_station | compared.requires_review.astype(bool)].copy()
    auxiliary["role"] = "Auxiliary provisional/partial-network diagnostic; NOT added to original core metric set"
    auxiliary.to_csv(child / "auxiliary_comparison.csv", index=False, encoding="utf-8-sig")
    (child / "primary_metrics.json").write_text(json.dumps(primary, indent=2), encoding="utf-8")
    discharge = exit_arrivals(directory, CASES[metadata["case"]][0], CASES[metadata["case"]][1])
    discharge.to_csv(child / "boundary_discharge_15min.csv", index=False, encoding="utf-8-sig")
    boundary_mean = float(discharge.simulated_exit_flow_vph.mean())
    if (directory / "boundary_out.xml").exists():
        _, loop = aggregate_e1(directory / "boundary_out.xml", CASES[metadata["case"]][0], CASES[metadata["case"]][1])
        loop["note"] = "E1 upstream crossings; terminal discharge separately derived from trip arrivals. Historical sink loops can lose rear-clearance counts."
        loop.to_csv(child / "boundary_loop_15min.csv", index=False, encoding="utf-8-sig")
    sha = lambda name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
    for name, key in [("network.net.xml", "network_sha256"), ("demand.rou.xml", "demand_sha256"),
                      ("vehicles.csv", "vehicle_manifest_sha256")]:
        if sha(name) != metadata[key]:
            raise ValueError(f"Archived input hash mismatch: {directory.name}/{name}")
    for name, key in [("core_vehicles.csv", "core_vehicle_manifest_sha256"),
                      ("network_definition.json", "network_definition_sha256")]:
        if metadata.get(key) and sha(name) != metadata[key]:
            raise ValueError(f"Archived input hash mismatch: {directory.name}/{name}")
    a, t = metrics["accounting"], metrics["trip_accounting"]
    return {"run_id": directory.name, "case": metadata["case"], "network": metadata["network_version"],
        "background_vph_assumed": metadata["background_vph_scenario"],
        "supply_vph_nominal_assumed": metadata.get("residual_supply_vph_scenario", 0),
        "core_flow_wmape_pct": primary["flow_wmape_pct"], "core_speed_mae_kmh": primary["speed_mae_kmh"],
        "core_occupancy_mae_pp": primary["occupancy_mae_percentage_points"],
        "core_station_windows": primary["station_windows"], "observed_slow_windows": primary["observed_slow_station_windows"],
        "reproduced_slow_windows": primary["reproduced_slow_station_windows"],
        "false_slow_windows": primary["false_slow_station_windows"], "planned": metadata["planned_vehicles"],
        "pending_at_horizon": t["pending_at_horizon"], "running_at_horizon": t["running_at_horizon"],
        "unfinished_after_clearance": t["unfinished_after_clearance"],
        "mean_experienced_time_lower_bound_s": t["mean_experienced_time_lower_bound_s"],
        "actual_boundary_outflow_vph": boundary_mean, "teleports": metrics["teleports"], "collisions": metrics["collisions"],
        "conservation_residual": a["conservation_residual"], "input_hashes_verified": True,
        "stored_primary_station_windows": metrics["primary_metrics"]["station_windows"]}


def report_stage03():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    output = APP / "results" / "stage03_report"
    output.mkdir(parents=True, exist_ok=True)
    mapping = pd.read_csv(REFINED / "detector_mapping.csv", dtype={"sumo_edge": str})
    core = json.loads((REFINED / "corridor_definition.json").read_text(encoding="utf-8"))["core_edges"]
    core_ids = set(mapping[mapping.sumo_edge.isin(core)].detector_id)
    directories = sorted(p.parent for p in (APP / "results").glob("stage03_*/run.json"))
    table = pd.DataFrame([review_run(p, output, core_ids) for p in directories])
    table.to_csv(output / "all_runs.csv", index=False, encoding="utf-8-sig")
    old = APP / "results/stage03_peak_bg900"
    fixed = APP / "results/stage03_peak_through_bg900"
    def routes(path):
        return {r.get("id"): r.get("edges").split() for r in ET.parse(path / "demand.rou.xml").getroot().findall("route")}
    old_routes, new_routes = routes(old), routes(fixed)
    agents = pd.read_csv(fixed / "core_vehicles.csv")
    route_counts = agents[agents.phase.eq("evaluation")].route_id.value_counts()
    sink_rows = [{"route_id": rid, "old_terminal": edges[-1], "new_terminal": new_routes[rid][-1],
        "scheduled_evaluation_agents": int(route_counts.get(rid, 0)),
        "old_stopped_at_last_core_detector": edges[-1] == "1095134817",
        "old_passes_last_core": "1095134817" in edges,
        "new_reaches_downstream_detector": "249233908" in new_routes[rid]} for rid, edges in old_routes.items() if not rid.startswith("bg_")]
    sinks = pd.DataFrame(sink_rows)
    sinks.to_csv(output / "mainline_sink_audit.csv", index=False, encoding="utf-8-sig")
    stopped = int(sinks.loc[sinks.old_stopped_at_last_core_detector, "scheduled_evaluation_agents"].sum())
    reaching = int(sinks.loc[sinks.old_passes_last_core, "scheduled_evaluation_agents"].sum())
    # Across supply hypotheses all exogenous agents and routes must be identical.
    paired = [fixed] + [APP / "results" / f"stage03_peak_supply{q}_nojump" for q in [600, 900, 1200]]
    same_agents = all((p / "vehicles.csv").read_bytes() == (fixed / "vehicles.csv").read_bytes() for p in paired)
    same_routes = all((p / "demand.rou.xml").read_bytes() == (fixed / "demand.rou.xml").read_bytes() for p in paired)
    if not same_agents or not same_routes:
        raise ValueError("Supply comparison changed exogenous vehicles or routes")
    loop_fixed = APP / "results/stage03_peak_supply900_loopfix"
    old_supply = APP / "results/stage03_peak_supply900_nojump"
    before = json.loads((old_supply / "metrics.json").read_text(encoding="utf-8"))
    after = json.loads((loop_fixed / "metrics.json").read_text(encoding="utf-8"))
    unchanged = all(before[k] == after[k] for k in ["primary_metrics", "accounting", "trip_accounting", "teleports", "collisions"])
    unchanged_inputs = all((loop_fixed / name).read_bytes() == (old_supply / name).read_bytes()
        for name in ["vehicles.csv", "demand.rou.xml", "network.net.xml"])
    def loop_counts(directory):
        rows = [e for e in ET.parse(directory / "boundary_out.xml").getroot().findall("interval")
            if WARMUP <= float(e.get("begin")) < WARMUP + CASES["peak"][1]]
        return {key: sum(int(e.get(key)) for e in rows) for key in ["nVehContrib", "nVehEntered"]}
    loop_evidence = {"run_id": loop_fixed.name, "before": loop_counts(old_supply), "after": loop_counts(loop_fixed),
        "actual_terminal_arrivals": int(exit_arrivals(loop_fixed, *CASES["peak"]).simulated_exit_arrivals.sum()),
        "input_agents_routes_network_unchanged": unchanged_inputs, "core_and_accounting_metrics_unchanged": unchanged}
    if not unchanged or not unchanged_inputs or loop_evidence["after"]["nVehContrib"] != loop_evidence["actual_terminal_arrivals"]:
        raise ValueError("Terminal loop repair did not preserve physics or recover exit counts")
    plot_runs = [("Before sink repair", old), ("Repaired; free exit", fixed),
        ("Residual supply = 900 (assumed)", APP / "results/stage03_peak_supply900_nojump")]
    fig, axes = plt.subplots(2, 3, figsize=(13.2, 7.1), sharex=True)
    for j, did in enumerate(["AID01210", "AID01212", "AID01213"]):
        for label, path in plot_runs:
            d = pd.read_csv(path / "comparison_15min.csv")
            d = d[d.detector_id.eq(did)].sort_values("window")
            x = pd.to_datetime(d.window)
            axes[0, j].plot(x, d.simulated_speed_kmh, "o--", linewidth=1, label=label)
            axes[1, j].plot(x, d.simulated_flow_vph, "o--", linewidth=1, label=label)
        eligible = d.eligible.astype(bool)
        color = "grey" if did == "AID01213" else "black"
        label = "Provisional mapping reference" if did == "AID01213" else "Eligible observation / estimate"
        axes[0, j].plot(x, d.speed_kmh_observed.where(eligible), "o-", color=color, label=label)
        axes[1, j].plot(x, d.flow_vph_estimated.where(eligible), "o-", color=color, label=label)
        for i, field in [(0, "speed_kmh_observed"), (1, "flow_vph_estimated")]:
            axes[i, j].scatter(x[~eligible], d.loc[~eligible, field], marker="x", color="grey", label="Low coverage (excluded)")
        axes[0, j].set_title(did + (" (mapping still provisional)" if did == "AID01213" else ""))
        axes[0, j].set_ylim(0, 80)
        axes[0, j].set_ylabel("Speed (km/h)")
        axes[1, j].set_ylabel("Flow (vehicles/hour)")
    for ax in axes.flat:
        ax.grid(alpha=.25)
        ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 15, 30, 45]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    handles, labels = axes[0, 0].get_legend_handles_labels()
    h2, l2 = axes[0, 2].get_legend_handles_labels()
    extra = l2.index("Provisional mapping reference")
    fig.legend(handles + [h2[extra]], labels + [l2[extra]], loc="lower center", ncol=3, fontsize=8)
    fig.suptitle("Boundary mechanism diagnosis, not independent validation | 2026-10-15 | UTC+8")
    fig.tight_layout(rect=(0, .10, 1, .96))
    fig.savefig(output / "boundary_mechanism.png", dpi=160)
    plt.close(fig)
    selected_ids = ["stage03_peak_bg900", "stage03_peak_through_bg900"] + [
        f"stage03_peak_supply{q}_nojump" for q in [1200, 900, 600]] + ["stage03_ordinary_through_bg900"]
    chosen = table.set_index("run_id").loc[selected_ids]
    content = f"""# 第三轮：修复主线人工出口，检验下游供给

**本轮发现并修复了影响拥堵传播的路径错误。** 现已能通过明确的外部供给情景产生回溢；但流量、速度、占有率和拥堵时段尚未同时吻合，仍不能宣称真实高峰基准通过。共完成{len(table)}次新运行，旧结果与失败均保留。

## 关键修复

候选路径把南向主线终点表示为两种形式：停在最后核心边1095134817，或再走到528507294。旧延伸只接了第二种6条路径。核心边终点另有6条路径，在本次900背景流量对照的评估期内，计划经过最后断面的{reaching}辆核心车辆中，有{stopped}辆在该断面附近直接结束行程。下游道路再长、背景流量再大，也无法让这些车辆真实进入并排队。

新`boundary_v3`把两种主线终点都接到45740459，共12条路径续接；明确的侧向出口保持其原路径。原始合成车辆ID、计划出发、车型、速度因子不变，原“目的地”解释为截取网络的边界，而非已观测的真实目的地。逐路径证据见`mainline_sink_audit.csv`。boundary_v1/v2保留用于复现旧诊断。

## 道路与检测器核查

- 在两个缺少背景流量的汇入口上游补齐3条候选进路，长度约430、515、400米；各注入起点边长约93、66、125米，避免在4—12米的截断边强行发车。新网络98条外部边。
- 新馈入范围有1处从原urban网继承的90秒生成信号方案，仍是假设，不是实际配时。没有根据拟合效果擅自拓宽主线或删除让行关系。
- AID01213官方单向中心线的局部方向约195.66°，SUMO约191.11°，相差4.55°；检测点Rotation为135°，与弯道局部方向不符。因此原大角度告警并不直接证明反向映射。车道覆盖和具体位置尚未核实，原复核标记保留，仅作辅助诊断。
- 常兴街、忠民街、民丰径的入口连接含小写m让行状态；长等待不能当成主线拥堵复现。车道连接和OSM条件权限证据在`../stage03_topology_audit/`。

## 情景设置与结果

入口先验正则固定0.5、随机种子固定20261017。先在旧/新续接规则下测试背景总量0、900、1800、2700、3600 veh/h，按三进路等分，从评估开始加入。它们是机理敏感性假设，不是已取得的真实汇入量。单补背景仍未复现核心5个合格低速站窗。

随后固定**同一批5692辆核心＋背景车辆及相同路径**，测试外部名义剩余供给600、900、1200 veh/h。用最后出口处的虚拟放行周期实现数值约束，不强制观测速率/速度，也不表示那里存在或计划建设真实信号灯。周期60秒、单车道饱和率1800 veh/h、启动损失2秒、黄灯3秒均是假设：`绿灯时长=60×目标供给/(2×1800)+2`。名义值不等于实际通过量；根据终点行程到达时间统计实际模拟出流，另存`boundary_discharge_15min.csv`。

表格统一使用原核心检测站集合，普通24、高峰26个合格站窗。新增上游检测站只覆盖部分建模转向，不能直接把其全部实测流量都作为模型应承担的输入；这些偏差仍保存在辅助诊断中，不因加入站点而改变主指标口径。

| 场景与设置 | 核心流量WMAPE | 速度MAE km/h | 占有率MAE 百分点 | 低速复现/实测 | 错报低速 | 清空后未完成 |
|---|---:|---:|---:|---:|---:|---:|
"""
    labels = ["高峰，旧终点，背景900", "高峰，修复终点，背景900，自由出口", "高峰，名义供给1200",
        "高峰，名义供给900", "高峰，名义供给600", "普通，修复终点，背景900，自由出口"]
    for label, (_, r) in zip(labels, chosen.iterrows()):
        content += f"| {label} | {r.core_flow_wmape_pct:.2f}% | {r.core_speed_mae_kmh:.2f} | {r.core_occupancy_mae_pp:.2f} | {int(r.reproduced_slow_windows)}/{int(r.observed_slow_windows)} | {int(r.false_slow_windows)} | {int(r.unfinished_after_clearance)} |\n"
    content += "\n| 名义供给假设 veh/h | 评估小时实际模拟终点出流 veh/h |\n|---|---:|\n"
    for q in [600, 900, 1200]:
        row = table.set_index("run_id").loc[f"stage03_peak_supply{q}_nojump"]
        content += f"| {q} | {row.actual_boundary_outflow_vph:.0f} |\n"
    content += f"""
供给900的无瞬移运行开始产生部分低速，但速度改善同时伴随流量与占有率误差加大；供给600虽覆盖全部5个低速站窗，却多报2个，流量误差约25%、占有率误差约16个百分点，且1540辆在900秒清空后仍未完成。不能据“5/5”将其选为真实最佳基准。

![边界修复与供给机理对比](boundary_mechanism.png)

## 异常与可复现性

- 首次供给900运行使用SUMO默认300秒等待瞬移，发生3次，保留在`stage03_peak_supply900`，不作为可信基准。后续三个供给情景显式关闭超时自动瞬移，所有长队、待发和未完成车辆照实计数；三次均0瞬移/0碰撞、车辆守恒差0。关闭瞬移不代表排队合理。
- 所有{len(table)}次运行均完成程序执行并守恒，碰撞均0；上述首次供给试验有3次瞬移，其余无瞬移。部分有急刹警告，原日志保留。当前清空900秒是有限观察，未清空时旅行时间仅报告全体计划车辆的删失下界。
- 600/900/1200供给情景的完整车辆CSV与路径XML逐字节相同，仅外部供给条件不同。这属于机理试验；正式治理对照还必须保持供给相同，再比较控制策略，并使用多个种子。
- 第一批两次馈入网运行的原主指标加入了新的未标记站点（30站窗）；本报告重新固定原26核心站窗，补充结果存本报告子目录，原指标未覆盖。其他新增站点的缺量与错误映射风险没有被认定为已解决。
- 另发现新增终点E1仅在道路末端前0.1米，部分车辆结束行程时车尾尚未完全通过，`nVehContrib`会漏计。例如供给900实验原E1仅439辆，终点行程实际1009辆（该实验E1的`nVehEntered`也为1009）。因此本报告实际出流采用行程到达时间，历史E1另存且不作为容量证据。
- 新增`stage03_peak_supply900_loopfix`实际复跑：将该辅助圈前移至最大车长＋5米之前，E1完整通过量恢复1009，与终点行程一致。网络/车辆/需求逐字节相同，核心指标、全体车辆核算、瞬移与碰撞结果完全相同，确认这次仅修正测量。未来生成器沿用该位置；原回放使用历史运行，核心指标不受影响。

## 下一步

1. 冻结本轮修复，采用流量、速度、占有率和拥堵时段联合诊断；增加路段蓄积及旅行时滞，区分自由需求、实际通过量和侧向流。
2. 用可解释的边界范围与初始状态做有限留出；暂不把某一人工供给参数当真实容量，不反复根据16日成绩调参。
3. 在明确标注的供给情景上开发入口协调/应急模块，治理必须同车辆、同外部供给，全部计划车辆成本与未完成量同时比较。真实场景效果仍需基准支持。

本轮没有完成实时需求同化、真实容量辨识、应急及正式比赛提交。旧“仅延伸几何没有改善”结论需要加上路径未完全续接这一实现缺陷，不能再作为已经排除下游回溢的证据。

## 复现

在`B题项目/`运行，使用未占用的运行ID：

```powershell
python 应用/run.py build-feeders
python 应用/run.py build-continuation
python 应用/run.py audit-topology
python 应用/run.py run --case peak --network through --entry-ridge 0.5 --background-vph 900 --supply-vph 900 --backend traci --run-id my_supply900
python 应用/run.py report-stage03
python 应用/run.py dashboard --run-id stage03_peak_supply900_nojump
python -m unittest discover -s 应用/tests -v
```
"""
    (output / "第三轮开发诊断.md").write_text(content, encoding="utf-8")
    verification = {"actual_runs": len(table), "original_fixed_core_ids": sorted(core_ids),
        "old_artificial_sink_evaluation_agents": stopped, "evaluation_core_agents_passing_last_detector": reaching,
        "paired_supply_vehicle_manifests_identical": same_agents, "paired_supply_route_xml_identical": same_routes,
        "all_input_hashes_verified": bool(table.input_hashes_verified.all()),
        "all_conservation_residual_zero": bool(table.conservation_residual.eq(0).all()),
        "runs_with_teleports": table.loc[table.teleports.gt(0), "run_id"].tolist(),
        "terminal_loop_repair": loop_evidence,
        "baseline_validated": False, "background_or_supply_observed": False}
    (output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report": str(output / "第三轮开发诊断.md"), **verification}
