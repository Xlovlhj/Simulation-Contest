#!/usr/bin/env python3
"""Summarize conditional flow, stopped vehicles and direct SUMO calibration.

Every queue is a model output, never labelled a field measurement. AI-assisted.
"""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
try:
    from . import calibrate_inverse_v2 as inverse
except ImportError:
    import calibrate_inverse_v2 as inverse

ROOT, OUT = inverse.ROOT, inverse.OUT
LABELS = {"north_to_south": "北→南", "south_to_north": "南→北",
          "west_to_hospital_road": "西→医院外围", "east_to_hospital_road": "东→医院外围"}


def conditional_state(profile, config):
    folder = ROOT / profile["best"]["run_dir"]
    metrics = inverse.read(folder / "metrics.json")
    records = inverse.read(folder / "vehicle_outcomes.json")
    edges = inverse.read(folder / "edge_summary.json")
    protocol = config["engine_protocol"]
    duration = protocol["background_end_s"] - protocol["warmup_s"]
    # A single named directed edge is a screenline. Summing all polyline edges
    # would count the same vehicle many times, so do not call that a flow count.
    screenline = "e000095"
    counts = {"background": 0, "probe": 0}
    for _, vehicle in ET.iterparse(folder / "vehicle_routes.xml", events=("end",)):
        if vehicle.tag != "vehicle":
            continue
        route = vehicle.find("route")
        if route is not None:
            edge_ids = route.get("edges", "").split()
            times = [float(t) for t in route.get("exitTimes", "").split()]
            for edge_id, time in zip(edge_ids, times):
                if edge_id == screenline and protocol["warmup_s"] <= time < protocol["background_end_s"]:
                    counts["probe" if vehicle.get("id", "").startswith("probe.") else "background"] += 1
        vehicle.clear()
    road_by_edge = {e["id"]: "/".join(e["road_names"]) for e in inverse.read(ROOT / protocol["route_catalog"])["network"]["edges"]}
    waiting_by_road = {}
    for edge_id, item in edges["edges"].items():
        road = road_by_edge.get(edge_id, "内部路口")
        waiting_by_road[road] = waiting_by_road.get(road, 0) + item["waiting_vehicle_seconds"]
    background = [r for r in records if r["role"] == "background"]
    completed = [r for r in background if r["completed"]]
    halting = []
    for _, step in ET.iterparse(folder / "summary.xml", events=("end",)):
        if step.tag == "step":
            if protocol["warmup_s"] <= float(step.get("time")) < protocol["background_end_s"]:
                halting.append(int(step.get("halting")))
            step.clear()
    if len(halting) != duration:
        raise ValueError("Missing one-second halting observations")
    return {
        "profile_id": profile["id"], "passes_consistency_screen": profile["passes_consistency_screen"],
        "physics_valid": metrics["physics_valid"], "all_vehicles_arrived": metrics["all_vehicles_arrived"],
        "conditional_background_demand_vph_by_od": metrics["demand_vph_by_od"],
        "conditional_network_demand_vehicles_per_min": sum(metrics["demand_vph_by_od"].values()) / 60,
        "observed_true_flow_estimate": None,
        "screenline": {"edge_id": screenline, "road_name": "瑞金二路", "window_start_s": protocol["warmup_s"],
                       "window_end_s": protocol["background_end_s"], "crossing_counts": counts,
                       "background_flow_vph": counts["background"] * 3600 / duration,
                       "probe_flow_vph": counts["probe"] * 3600 / duration,
                       "meaning": "Simulated directed screenline, not matched to the unspecified 2025 count section"},
        "edge_stopped_occupancy_indicator_by_road": {road: value / duration for road, value in sorted(waiting_by_road.items())},
        "edge_indicator_warning": "Edge waitingTime/window can count a vehicle touching adjacent short edges repeatedly. This is NOT a unique road queue count.",
        "unique_network_halting_vehicles": {"mean": statistics.mean(halting), "max": max(halting), "samples": len(halting)},
        "queue_definition": "summary.xml halting: unique in-network vehicles with speed<0.1m/s, excluding explicit stops, every second; includes probes, excludes vehicles waiting for insertion. No street attribution or field queue claim.",
        "completed_background_mean_stopped_time_s": statistics.mean(r["waiting_time_s"] for r in completed) if completed else None,
        "completed_background_mean_in_network_time_s": statistics.mean(r["in_network_time_s"] for r in completed) if completed else None,
        "completed_means_censored": len(completed) != len(background),
        "background_unfinished": len(background) - len(completed),
        "probe_max_insertion_delay_s": metrics["probe"]["insertion_delay_max_s"],
        "effective_running_constraints_kmh": {k: 30 * v for k, v in profile["best"]["parameters"]["group_speed_factors"].items()},
        "local_traffic_signal_estimate": None,
        "queue_cause_identification": "Not identified from ETA. Model contains its specified merges, priorities, demand and effective speed constraints only.",
        "source_sha256": {str((folder / f).relative_to(ROOT)): inverse.sha(folder / f)
                          for f in ("metrics.json", "vehicle_outcomes.json", "edge_summary.json", "vehicle_routes.xml", "summary.xml")},
    }


def figure(targets, old, selected, path):
    names = list(targets)
    maximum = max(list(targets.values()) + list(old.values()) + list(selected.values())) * 1.12
    elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="430" viewBox="0 0 1000 430">',
                '<rect width="1000" height="430" fill="#fff"/>',
                '<g font-family="sans-serif" font-size="16">',
                '<text x="24" y="30" font-size="22">直接 SUMO 时间与训练窗口百度 ETA 中位数</text>',
                '<text x="24" y="58" fill="#475569">秒；V2 不使用仿射输出修正。训练吻合不代表跨日预测或真实流量已验证。</text>']
    series = [(targets, "#374151", "百度训练中位数"), (old, "#94a3b8", "V1 原始 SUMO"), (selected, "#0f766e", "V2 直接 SUMO")]
    for i, od in enumerate(names):
        y = 90 + i * 72
        elements.append(f'<text x="24" y="{y + 25}">{LABELS[od]}</text>')
        for j, (values, color, _) in enumerate(series):
            width = 680 * values[od] / maximum
            elements.append(f'<rect x="190" y="{y + j * 19}" width="{width:.2f}" height="15" fill="{color}"/>')
            elements.append(f'<text x="{196 + width:.2f}" y="{y + j * 19 + 13}" font-size="13">{values[od]:.1f}</text>')
    for j, (_, color, label) in enumerate(series):
        elements.append(f'<rect x="{24 + j * 280}" y="390" width="16" height="16" fill="{color}"/><text x="{48 + j * 280}" y="404">{label}</text>')
    path.write_text("\n".join(elements + ['</g></svg>']), encoding="utf-8")


def main():
    config, fitted = inverse.read(inverse.CONFIG), inverse.read(OUT / "fit.json")
    evaluation = inverse.read(OUT / "evaluation.json")
    states = [conditional_state(p, config) for p in fitted["profiles"]]
    accepted = [s for s in states if s["passes_consistency_screen"]]
    envelope = {}
    for key in ("conditional_network_demand_vehicles_per_min", "completed_background_mean_stopped_time_s"):
        values = [s[key] for s in accepted if s[key] is not None]
        envelope[key] = {"min": min(values), "max": max(values)} if values else None
    inverse.write(OUT / "conditional_states.json", {"states": states, "accepted_grid_envelope": envelope,
                  "range_status": "Finite tested-profile sensitivity envelope, NOT a confidence/credible interval or guaranteed identification set"})
    selected = next((p for p in fitted["profiles"] if p["id"] == fitted["selected_for_time_prediction"]), None)
    if selected is None:
        raise ValueError("No physically valid fitted profile; cannot present calibrated results")
    targets, predictions = config["target_median_eta_s"], selected["best"]["predictions_s"]
    old_path = ROOT / "outputs/validation/ETA_cc5eba1e5891e909/model.json"
    old = {od: x["physical_sumo_s"] for od, x in inverse.read(old_path)["models"]["per_od"].items()}
    figure(targets, old, predictions, OUT / "direct_sumo_times.svg")
    governance_path = ROOT / "outputs/governance_v2/summary.json"
    governance = inverse.read(governance_path)
    if governance["status"] != "completed":
        raise ValueError("Governance results must be complete before the final report")
    lines = ["# V2：参数来源、路网修正与 SUMO 内部反演", "",
             "## 先用通俗的话说明", "",
             "用户指出的问题成立：地图位置画对了，并不等于交通时间已经对。原先在仿真结果外乘系数、加常数，只能做经验时间换算，不能补上缺失的车流、信号和路口行为。本轮改成调整仿真输入、真正重跑 SUMO，再直接比较车辆在网时间。百度提供的是预计时间，仍不是现场车辆计时。", "",
             "还修正了两处同一物理道路被折线采样差异拆成重叠道路的问题。V2 的外部边/允许转向数是305/308，V1为307/310；保存旧边到规范边链映射、几何残差、源路径连续性和终端可达关系检查。没有通过关闭碰撞检测来通过验收。", "",
             "## 数据依据", "",
             "逐项审计见 `research/parameter_audit/README.md` 与参数登记表；涵盖48项参数/隐含假设、222个配置叶字段和26项一手资料。", "",
             "黄浦区政府公示的瑞金医院相关项目环评，在2025年9月监测中记录瑞金二路昼间20分钟207辆车，折算621辆/小时。它是当地历史量级参考；路段、方向、年份、车型与当前六OD不同，不能直接当2026周末流量。本轮增加三条经过共同段的OD各200辆/小时、合计600的条件参考，并保留更低/更高需求。30km/h是环评噪声预测用值，不是已核实法定限速。", "",
             "615个百度步骤均可按有向几何匹配，但步骤时间几乎与路线总时间同比变化，不作为额外独立样本。训练48条路线，只有4组重复OD。终点3米/20秒一类值包含综合时间成本，不用于推断真实限速。", "",
             "## 方法与可识别性", "",
             "采用 SUMO 仿真循环优化：设定候选需求 → 调整瑞金二路、建国西路及其他走廊三组有效运行约束 → 实际仿真 → 比较四条路线训练中位ETA → 保留误差小且没有碰撞、传送、丢弃和未完成车辆的解。使用有界坐标搜索，不声称执行了 demandify 遗传算法。", "",
             "目标函数为四条路线相对中位数误差的均方：J(θ|q)=mean[((T_SUMO,r(θ,q)−median(T_Baidu,r))/median(T_Baidu,r))²]。需求q逐档剖面化，θ是三组有效运行约束。没有 αT+β，也没有人为停车补齐时间。", "",
             "有效运行约束会综合吸收未能分解的信号、沿街干扰和运行速度影响。它能用于条件交通实验，不能解释成真实道路限速；较好的ETA一致性也不能证明真实红绿灯、车道、每分钟车数和排队成因已被唯一恢复。", "",
             f"实际评价 {fitted['candidates']} 个候选，{fitted['invalid_candidates']} 个未通过物理/完整性/探针入网条件。训练误差最小剖面：{selected['id']}。允许各OD中位误差≤15%是工程一致性筛选条件；20秒探针入网阈值源于最短训练中位417秒的约5%，在烟测后、正式搜索前确定，所有候选统一执行。", "",
             "| 路线 | 百度训练中位数(s) | V1直接SUMO(s) | V2直接SUMO(s) |",
             "|---|---:|---:|---:|"]
    for od in targets:
        lines.append(f"| {LABELS[od]} | {targets[od]:.1f} | {old[od]:.2f} | {predictions[od]:.2f} |")
    lines += ["", "![时间比较](direct_sumo_times.svg)", "", "## 时间留出评价", "",
              "参数只用训练窗口拟合；保留窗口不重拟合。V1已看过这些留出结果，所以本轮是回顾性时间留出，不能称全新盲测或前瞻验证。固定一套交通状态跨日预测，不能自动跟随当天需求变化。", "",
              "| 窗口 | 样本数 | V2直接SUMO MAE(s) | 训练中位数基准MAE(s) |", "|---|---:|---:|---:|"]
    for phase, value in evaluation["profiles"][selected["id"]].items():
        ref = evaluation["training_median_reference"][phase]
        lines.append(f"| {phase} | {value['n']} | {value['mae_s']:.2f} | {ref['mae_s']:.2f} |")
    lines += ["", "## 第三个问题：缺失车流与排队怎样估计", "",
              "下面是给定候选需求和已标定有效约束时，实际SUMO产生的车流和等待。不同需求若都能匹配ETA，就必须同时保留，不能因为某一组数值漂亮就把它写成现实真值。范围是本轮试过的有限候选包络，没有统计置信区间含义。", "",
              "| 需求剖面 | 每OD辆/分钟 | 全网请求辆/分钟 | 瑞金断面背景辆/小时 | 普通车平均停车等待(s) | 通过一致性筛选 |",
              "|---|---:|---:|---:|---:|---|"]
    for s in states:
        od_rate = next(iter(s["conditional_background_demand_vph_by_od"].values())) / 60
        wait = s["completed_background_mean_stopped_time_s"]
        lines.append(f"| {s['profile_id']} | {od_rate:.2f} | {s['conditional_network_demand_vehicles_per_min']:.2f} | {s['screenline']['background_flow_vph']:.1f} | {wait:.2f} | {'是' if s['passes_consistency_screen'] else '否'} |")
    lines += ["", "完整条件状态、全网唯一停车车辆的逐秒均值/最大值、未完成数及逐车来源见 `conditional_states.json`。唯一停车车辆数取自SUMO每秒summary.halting；逐道路另提供边累计停车占用指标，短边车身跨越会重复计数，不能当唯一排队车数。断面流量只计同一条边的穿越，避免把同车经过多个几何分段重复加总。", "",
              "真实信号配时、各路口红灯等待及排队成因仍无法由四条平台ETA唯一识别。本轮不会编造信号周期；若要识别，需要路口车次/轨迹/信号观测等额外约束。上海公开车辆轨迹、pNEUMA/NGSIM等可用于算法和先验验证，不能搬用为瑞金医院当天实测。", "",
              "## 第二问拥堵取值修订", "",
              "原68.735米事件段的1/0.5m/s无直接来源。修订为西侧到医院路线中完整瑞金二路步骤：平台276m、模型278.665139m、19条边。仅训练12个样本的有效速度中位6.0041km/h，p25=4.9473、p10=4.2317，因此中度/较强倍率为0.82399/0.70480。倍率乘在已校准运行约束上，属于同几何观测支持的情景代理，不是把平均速度当真实点限速。", "",
              "其中约209.93m属于共同必经段，备选只能绕过约68.74m，不保证改道有收益。300/600/900秒事件长度、任务时刻、45/180秒控制上限仍是实验设计变量；来源见 `research/parameter_audit/scenario_basis_v2.md`。治理重跑见 `outputs/governance_v2/`，不得沿用V1的8.77%或60秒收益当作V2结论。", "",
              "## 修订后治理结果", "",
              f"已完成{len(governance['runs'])}组实际SUMO对照、{len(governance['comparisons'])}个配对。所有车辆到达且未检测到碰撞/传送/丢弃。A全部没有改道；下面只列通过训练时间筛选的需求档中B相对A的结果。正数表示耗时增加。", "",
              "| 需求档 | 拥堵强度 | 普通总时间变化(车·秒) | 保障任务时间变化(秒) |",
              "|---|---|---:|---:|"]
    accepted_ids = set(fitted["accepted_profile_ids"])
    for comparison in governance["comparisons"]:
        if comparison["profile"] in accepted_ids and comparison["policy"] == "B" and comparison["reference_policy"] == "A":
            lines.append(f"| {comparison['profile']} | {comparison['preset']} | {comparison['ordinary_system_time_delta_s']:+.0f} | {comparison['task_time_delta_s']:+.0f} |")
    lines += ["", "本轮通过筛选的情景不支持现有B策略能缩短保障任务；普通交通成本全部增加。不能将旧版假设情景的治理收益继续作为新模型结论。未通过时间筛选的18/15秒需求仍完整保留，但只作诊断。", "",
              "A不改道有可核算原因：完整事件段约209.93m是两候选共同必经，成本在比较中抵消；备选只能避开68.735m，却多走约717.126m。在60/30秒需求档，降速倍率须低到约0.1312/0.1180才使改道代价更小；训练得到的0.8240/0.7048不接近这个阈值。推导及来源见 `outputs/governance_v2/route_choice_explanation.md`。没有通过放大事故强度制造收益。", "",
              "## 依据与复现", "",
              "- SUMO官方仿真循环标定示例：https://sumo.dlr.de/docs/Tutorials/Calibration/San_Pablo_Dam.html",
              "- demandify作者项目（思路参考，本轮未安装执行）：https://github.com/aonurakman/demandify",
              "- FHWA微观交通模型标定要求：https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter5.htm",
              "- 本地环评、上海公开轨迹和其余资料：`research/parameter_audit/sources.json`；文件摘要已保存。", "",
              "复现脚本：`python3 scripts/prepare_observations_v2.py --run`、`python3 scripts/normalize_network_v2.py --run`；已有冻结结果勿覆盖。内部标定入口 `python3 scripts/calibrate_inverse_v2.py fit`，已有fit时会拒绝覆盖；评价 `python3 scripts/calibrate_inverse_v2.py evaluate`，汇总 `python3 scripts/summarize_inverse_v2.py`。完整搜索保留每次参数、真实SUMO XML、无效原因、来源及产物摘要。", "",
              "AI参与数据审查、程序和模型构建；队员仍需依赛方要求独立开展必要核心工作及实质复核。"]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    inverse.write(OUT / "summary_manifest.json", {"source_sha256": {str(p.relative_to(ROOT)): inverse.sha(p) for p in
                  [inverse.CONFIG, OUT / "fit.json", OUT / "evaluation.json", old_path, governance_path, Path(__file__)]},
                  "artifacts": {p: inverse.sha(OUT / p) for p in ["conditional_states.json", "report.md", "direct_sumo_times.svg"]}})
    print(str((OUT / "report.md").relative_to(ROOT)))


if __name__ == "__main__":
    main()
