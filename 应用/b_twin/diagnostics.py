"""Summarize isolated paired runs and save reviewable figures/reports."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
from __future__ import annotations

import json

import pandas as pd

from .paths import APP


def report_stage01():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output = APP / "results" / "stage01_report"
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for case in ["ordinary", "peak"]:
        for network in ["original", "extended"]:
            directory = APP / "results" / f"stage01_{case}_{network}"
            metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
            records.append({"case": case, "network": network, "flow_wmape_pct": metrics["flow_wmape_pct"],
                "speed_mae_kmh": metrics["speed_mae_kmh"], "pending": metrics["vehicles"]["waiting"],
                "running_at_end": metrics["vehicles"]["running"], "teleports": metrics["teleports"],
                "collisions": metrics["collisions"]})
    table = pd.DataFrame(records)
    table.to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.8), sharex=True, sharey=True)
    plots = []
    for network in ["original", "extended"]:
        d = pd.read_csv(APP / "results" / f"stage01_peak_{network}" / "comparison_15min.csv")
        plots.append((network, d))
    for ax, did in zip(axes.flat, ["AID01208", "AID01209", "AID01210", "AID01212"]):
        measured = plots[0][1]
        measured = measured[measured.detector_id == did].sort_values("window")
        x = pd.to_datetime(measured.window)
        valid = measured.eligible.fillna(False).to_numpy().astype(bool)
        ax.plot(x[valid], measured.loc[valid, "speed_kmh_observed"], "ko-", label="Official observed samples")
        if (~valid).any():
            ax.scatter(x[~valid], measured.loc[~valid, "speed_kmh_observed"], marker="x", color="grey", label="Low coverage (excluded)")
        for label, frame in plots:
            g = frame[frame.detector_id == did].sort_values("window")
            ax.plot(pd.to_datetime(g.window), g.simulated_speed_kmh, "o--", label="SUMO " + label)
        ax.set_title(did)
        ax.set_ylabel("Speed (km/h)")
        ax.grid(alpha=.25)
        ax.set_ylim(0, 80)
    import matplotlib.dates as mdates
    for ax in axes.flat:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.legend(fontsize=7)
    fig.suptitle("Peak boundary diagnosis | 2026-10-15 (UTC+8) | 15-minute aligned windows")
    fig.tight_layout()
    fig.savefig(output / "peak_speed_comparison.png", dpi=160)
    plt.close(fig)
    content = """# 首轮边界与运行入口诊断

2026-10-17。以下均为已经运行的结果，不代表基准已通过验证。

## 对照设计

原52边走廊与延伸74边网络分别运行普通/高峰，延伸公主道—康庄道—海底隧道约1.14公里。沿用旧版窗口平均需求，仅延长以南向主线出口结束的路径；其他出口不变。未补入未知的下游汇入交通、初始排队或实际瓶颈供给，因此用于检验“仅延伸道路几何是否足够”。

输入车辆仍由SUMO流分布生成；相同种子不能保证不同网络中所有随机抽样一致。因此本轮是单种子初步诊断，正式治理对照将预生成同一车辆清单。

## 结果

| 场景 | 网络 | 流量WMAPE | 速度MAE | 未发车 | 结束时仍在网内 | 瞬移/碰撞 |
|---|---|---:|---:|---:|---:|---|
"""
    for r in table.itertuples():
        content += f"| {r.case} | {r.network} | {r.flow_wmape_pct:.2f}% | {r.speed_mae_kmh:.2f} km/h | {r.pending} | {r.running_at_end} | {r.teleports}/{r.collisions} |\n"
    content += """
本轮逐个匹配合格的15分钟观测窗口，再对比SUMO同一时段输出。旧报告采用窗口平均计数与速度汇总；统计口径不同，不能把旧版2.4%和新版4.8%的普通流量误差直接解释为模型退化。新口径能暴露时变需求与窗口内误差。

**仅延伸几何没有解决高峰速度偏高。** 新路网高峰速度MAE约14.6km/h，与原版接近。继续查下游供给、未知汇流、初始队列和时变需求；不要因此反复调整驾驶参数或直接强制观测速度。

![高峰关键点速度对比](peak_speed_comparison.png)

## 运行与状态

统一入口`应用/run.py`已完成状态查询、边界构建、隔离运行与指标导出。每个运行目录包含网络、需求、检测器、配置、输入哈希、SUMO输出及评估文件，避免覆盖历史诊断。

TraCI普通时段实际运行已完成，输出60秒状态日志，保存物理时刻、路段ID、坐标、仿真速度、车辆数、停车车辆数和待发车数。它目前是历史模型运行状态，还没有实现观测驱动的需求同化、治理或应急控制。

## 下一步

1. 按15分钟估计时变需求，并在正式对照前固定合成车辆清单。
2. 选择有依据的下游供给/背景汇流与初始队列方案，保留不同假设和留出检验。
3. 在基准可解释后增加入口协调与应急任务；实时观测状态不能冒充已同化的仿真状态。
"""
    (output / "首轮开发诊断.md").write_text(content, encoding="utf-8")
    return {"report": str(output / "首轮开发诊断.md"), "figure": str(output / "peak_speed_comparison.png"),
        "conclusion": "Geometry extension alone does not reproduce peak congestion; baseline not validated"}


def report_stage02():
    """Recompute review statistics from archived outputs, preserving old runs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from .evaluation import CASES, WARMUP, trip_accounting, vehicle_accounting
    from .supply import inspect_supply

    output = APP / "results" / "stage02_report"
    output.mkdir(parents=True, exist_ok=True)
    configurations = [(case, label, name) for case in ["ordinary", "peak"] for label, name in
        [("fixedmean", f"stage02_{case}_fixedmean_v2"), ("time15", f"stage02_{case}_time15"),
         ("time15_entry05", f"stage02_{case}_entry05")]] + [("peak", "time15_entry10", "stage02_peak_entry10")]
    records = []
    for case, label, run_id in configurations:
        directory = APP / "results" / run_id
        metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        review = output / run_id
        review.mkdir(exist_ok=True)
        accounting = vehicle_accounting(directory / "summary.xml", WARMUP + CASES[case][1], .5, run["planned_vehicles"])
        trips = trip_accounting(directory, WARMUP + CASES[case][1], run["simulation_end_seconds"], review)
        (review / "accounting.json").write_text(json.dumps({"summary": accounting, "trips": trips}, indent=2), encoding="utf-8")
        p = metrics["primary_metrics"]
        records.append({"case": case, "demand": label, "run_id": run_id, "flow_wmape_pct": p["flow_wmape_pct"],
            "speed_mae_kmh": p["speed_mae_kmh"], "occupancy_mae_pp": p["occupancy_mae_percentage_points"],
            "station_windows": p["station_windows"], "observed_slow_windows": p["observed_slow_station_windows"],
            "reproduced_slow_windows": p["reproduced_slow_station_windows"],
            "planned": run["planned_vehicles"], "pending_end_summary": accounting["evaluation_end"]["pending"],
            "loaded_not_yet_attempted_end": accounting["evaluation_end"]["loaded_not_yet_attempted"],
            "running_end": accounting["evaluation_end"]["running"],
            "pending_after_clearance": accounting["after_clearance"]["pending"],
            "running_after_clearance": accounting["after_clearance"]["running"],
            "mean_departure_delay_s": trips["mean_departure_delay_lower_bound_s"],
            "conservation_residual": accounting["conservation_residual"],
            "teleports": metrics["teleports"], "collisions": metrics["collisions"]})
    table = pd.DataFrame(records)
    table.to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    supply = inspect_supply(output)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True)
    for j, did in enumerate(["AID01210", "AID01212"]):
        for label, name in [("Fixed mean", "fixedmean_v2"), ("Time-varying", "time15"), ("Time-varying + entry prior", "entry05")]:
            d = pd.read_csv(APP / "results" / f"stage02_peak_{name}" / "comparison_15min.csv")
            d = d[d.detector_id.eq(did)].sort_values("window")
            x = pd.to_datetime(d.window)
            axes[0, j].plot(x, d.simulated_speed_kmh, "o--", label=label, linewidth=1)
            axes[1, j].plot(x, d.simulated_flow_vph, "o--", label=label, linewidth=1)
        eligible = d.used_in_primary_metrics.astype(bool)
        axes[0, j].plot(x[eligible], d.loc[eligible, "speed_kmh_observed"], "ko-", label="Eligible observed samples")
        axes[1, j].plot(x[eligible], d.loc[eligible, "flow_vph_estimated"], "ko-", label="Eligible expanded count")
        axes[0, j].scatter(x[~eligible], d.loc[~eligible, "speed_kmh_observed"], marker="x", color="grey", label="Low coverage (excluded)")
        axes[1, j].scatter(x[~eligible], d.loc[~eligible, "flow_vph_estimated"], marker="x", color="grey", label="Low coverage (excluded)")
        axes[0, j].set_title(did)
        axes[0, j].set_ylabel("Speed (km/h)")
        axes[0, j].set_ylim(0, 80)
        axes[1, j].set_ylabel("Flow (vehicles/hour)")
    for ax in axes.flat:
        ax.grid(alpha=.25)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    handles, labels = axes[0, 0].get_legend_handles_labels()
    labels = ["Eligible observation / estimate" if label == "Eligible observed samples" else label for label in labels]
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=8)
    fig.suptitle("Count fit does not reproduce congestion | 2026-10-15 | UTC+8")
    fig.tight_layout(rect=(0, .09, 1, .96))
    fig.savefig(output / "peak_flow_speed.png", dpi=160)
    plt.close(fig)
    report = """# 第二轮：分时需求、固定车辆与供给诊断

本轮已完成普通/高峰实际运行。**分时需求改善了流量拟合，但高峰低速仍未复现，基准不能宣称通过。** 全部指标来自15日校准数据，本轮未查看或重调16日数据。

## 改了什么

1. 用58条候选路径逐15分钟估计非负路径小时率，只约束覆盖合格、映射无复核标记的计数；缺测不补零。预热采用前一15分钟的估计率运行300秒，从空网开始，不冒充观测初始队列。
2. 运行前固定每辆合成车的ID、计划出发时间、原始OD、车型和速度因子。同种子不依赖网络/治理策略；原/延伸网络的清单字节与SHA256一致已自动检查。延伸只改变6条路径的下游尾段。SUMO每步随机驾驶扰动仍可能随运行分岔，正式效果评价需要多个种子。
3. 观测时段后停止新增出发，再运行900秒清空。观测时段末的待发/在网、清空后未完成、全体车辆发车等待、车辆守恒分别记录，清空输出不参与观测误差。
4. 增加未观测入口总量的正则敏感性检查，取0、0.5、1。其结果是不同需求假设，不能把减少需求造成的成本下降称为治理收益。

## 估计方法与合理性

设x为各路径小时率，A表示路径是否经过检测点，q为合格样本扩展的流量。求非负最小二乘：

`min(x>=0) ||W(Ax-q)||² + 0.2²||x-x_mean||² + 0.15²||x-x_previous||² + entry_ridge²||Bx||²`

W使用条件流量区间宽度形成的标准差代理，权重为`500/max(100, SD)`；首个窗口无前窗项。B把相同未观测外部入口的路径率相加。零入口先验只是偏好少加缺乏依据的车，不证明真实入口流量为零。无合格计数约束时保留均值先验并明确标注。代表车长、截断正态速度因子及南向借用北向车型比例仍是假设。

车辆数按每条路径累计取整，总数与连续期望相差小于1；区间内分层随机安排出发，车型和速度因子预先抽样，SUMO不再随机选择车型/速度分布。

约束矩阵远不足58个独立维度，OD与转向流仍不唯一。A没有旅行时滞或路段蓄积项，堵车时的内部通过量也不等于上游自由需求。因此拟合值只能作为初始化，不能独立证明真实需求、排队或瓶颈容量。

## 实际结果

全部采用同窗合格观测。流量是WMAPE，速度是MAE；普通24个站窗、高峰26个站窗。低于30 km/h作为本报告的工程拥堵筛查阈值。

| 场景 | 需求假设 | 流量误差 | 速度误差 km/h | 观察期末待发/在网 | 复现低速窗口 |
|---|---|---:|---:|---:|---:|
"""
    for r in table.itertuples():
        report += f"| {r.case} | {r.demand} | {r.flow_wmape_pct:.2f}% | {r.speed_mae_kmh:.2f} | {r.pending_end_summary}/{r.running_end} | {r.reproduced_slow_windows}/{r.observed_slow_windows} |\n"
    report += """
以上7次主报告运行均无瞬移、碰撞；900秒清空后待发/在网均为0，全体车辆守恒差为0。部分运行有急刹警告，原日志保留。末个0.5秒步还有计划出发尚未尝试的车辆时，另列`loaded_not_yet_attempted_end`；不能把它漏出未完成量。

未观测入口正则从0改为0.5，普通流量误差约2.77%→0.94%，高峰约2.28%→1.38%；高峰速度误差仍约14.60 km/h，5个合格低速站窗一个也未复现。选择更强入口先验减少了旁支注入及其排队，但没有识别真实入口流量，也没有解决主线拥堵。

![关键点流量与速度对比](peak_flow_speed.png)

## 发现的问题与下一步

- 原分时估计下，常兴街、忠民街、民丰径等缺少转向观测的入口产生较长发车等待；`source_accounting.csv`保留逐入口诊断。它们的排队不能当作真实主线拥堵的复现证据。
- 01210在08:30的合格样本速度约12.94 km/h，原分时仿真约65.14 km/h；01212约22.21对68.64 km/h。小流量误差掩盖了速度与占有率的严重偏差。
- 07:45可靠核心检测点01210/01212样本速度约59.8/59.2 km/h；没有观测队列长度，不能随意加入大初始队列。更远下游的01213映射仍待复核。
- `unmeasured_downstream_inlets.csv`列出延伸路网上尚未补入的入口；当前0表示未建模，并非真实零流量。车道收缩在`downstream_lane_reductions.csv`，只证明模型几何存在，不识别真实容量。
- 两断面流量差既可能来自侧向驶出，也可能来自车辆蓄积及采样/时滞误差。`flow_balance_ambiguity.csv`保留差值，不将其直接认定为转向流。

下一轮优先复核连接车道、转向与下游映射，再建立有边界说明的剩余供给情景。治理对照必须固定同一入口先验、供给条件、车辆清单，并计入全部计划车辆成本。不能强制观测速度后再用同一速度自证验证。此轮尚未完成实时需求同化、治理、应急及独立泛化验证。

## 复现

在`B题项目/`运行，示例ID不能与已有目录相同：

```powershell
python 应用/run.py run --case peak --network extended --demand time15 --entry-ridge 0 --clearance 900 --run-id my_peak_time15
python 应用/run.py run --case peak --network extended --demand time15 --entry-ridge 0.5 --clearance 900 --run-id my_peak_entry05
python 应用/run.py report-stage02
python 应用/run.py dashboard --run-id stage02_ordinary_time15
python -m unittest discover -s 应用/tests -v
```

`fixedmean_v2`修正了第一批fixedmean运行中仅供说明的分时拟合诊断字段，并补全行程核算；实际车辆清单和仿真结果未变化。第一批文件仍保留。早期time15运行的补充行程核算写入本报告同名子目录，不覆盖原运行指标。
"""
    (output / "第二轮开发诊断.md").write_text(report, encoding="utf-8")
    return {"report": str(output / "第二轮开发诊断.md"), "runs": len(table),
        "supply_identified": supply["capacity_identified"], "traffic_baseline_validated": False}
