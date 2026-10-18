#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；实现与研究使用待队员复核。
"""Build a compact offline overview and eight source-backed FCD replay pages."""
from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
import re
import sys

try:
    from . import view_baseline as baseline_view
    from .audit_network import ROOT, sha256, workspace_path
    from .replay_baidu import ROUTE_LABELS, safe_json
except ImportError:
    import view_baseline as baseline_view
    from audit_network import ROOT, sha256, workspace_path
    from replay_baidu import ROUTE_LABELS, safe_json


DEFAULT_BATCH = ROOT / "outputs/scenarios/SCENARIOS_20261017T074635_968755Z"


def verified_json(directory, name, hashes):
    path = workspace_path(directory / name)
    if not path.is_relative_to(directory.resolve()) or sha256(path) != hashes.get(name):
        raise ValueError(f"留档文件未通过 SHA-256 核验：{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def display_number(value):
    if value is None:
        return "—"
    return f"{value:,.0f}" if isinstance(value, (int, float)) and float(value).is_integer() else escape(str(value))


def policy_label(case):
    phase = "S3 单任务" if "_S3_" in case else "S2 普通交通"
    policy = "独立出发前指派" if "guided" in case else "固定默认路径"
    return f"{phase} · {policy}"


def read_batch(batch):
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed" or manifest.get("purpose") != "uncalibrated_engineering_scenarios":
        raise ValueError("只展示已完成的未标定工程对照批次")
    if manifest.get("coordination_B_status") != "not_implemented" or manifest.get("eligible_for_governance") is not False:
        raise ValueError("批次必须明确 B 未实现、未作为现实治理效果")
    hashes = manifest.get("artifacts", {})
    summary = verified_json(batch, "summary.json", hashes)
    comparisons = verified_json(batch, "comparisons.json", hashes)
    protocol = verified_json(batch, "scenario_config.json", hashes)
    if len(summary) != len(manifest["runs"]) or len(summary) != 8:
        raise ValueError("本页应完整包含两种事件强度的八组结果")
    rows, replay_data, run_manifests = [], {}, {}
    entries = {r["case"]: r for r in manifest["runs"]}
    if len(entries) != 8 or len({r["case"] for r in summary}) != 8:
        raise ValueError("存在重复组或缺失组")
    for row in summary:
        case = row["case"]
        if not re.fullmatch(r"[A-Za-z0-9_]+", case):
            raise ValueError("不安全的场景标识")
        directory = workspace_path(ROOT / entries[case]["folder"])
        if not directory.is_relative_to(batch):
            raise ValueError("场景目录越出当前批次")
        run = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8"))
        if run.get("status") != "completed" or run.get("physics_valid") is not True:
            raise ValueError("场景未完成或未通过运行物理检查")
        if run.get("coordination_B_status") != "not_implemented" or run.get("real_world_calibrated") is not False:
            raise ValueError("场景状态与工程展示定位不符")
        for name, digest in run["artifacts"].items():
            path = workspace_path(directory / name)
            if not path.is_relative_to(directory) or sha256(path) != digest:
                raise ValueError(f"场景产物摘要不符：{case}/{name}")
        metrics = verified_json(directory, "metrics.json", run["artifacts"])
        demand = verified_json(directory, "demand.json", run["artifacts"])
        ordinary = [v for v in demand if v["role"] == "ordinary"]
        tasks = [v for v in demand if v["role"] != "ordinary"]
        counts = Counter(v["od_id"] for v in ordinary)
        if len(counts) != 6 or set(counts.values()) != {40} or len(tasks) != (1 if "_S3_" in case else 0):
            raise ValueError("需求不符合六个固定 OD 各40辆及S3单任务定义")
        for od in counts:
            if sorted(v["release_s"] for v in ordinary if v["od_id"] == od) != list(range(0, 1200, 30)):
                raise ValueError("固定 OD 请求时刻不是0—1170秒每30秒一次")
        task_metrics = metrics["hospital_tasks"][0] if tasks else {}
        expected = {"planned": metrics["planned"], "arrived": metrics["arrived"],
            "unfinished_total": metrics["unfinished_total"], "ordinary_system_time_s": metrics["ordinary"]["total_system_vehicle_time_s"],
            "ordinary_departure_wait_s": metrics["ordinary"]["total_departure_wait_time_s"],
            "ordinary_unfinished": metrics["ordinary"]["unfinished_total"],
            "changed_assignments": metrics["changed_assignments"],
            "task_release_to_arrival_s": task_metrics.get("release_to_arrival_s"),
            "task_completed": task_metrics.get("completed"), "physics_valid": metrics["physics_valid"],
            "demand_sha256": run["demand_sha256"], **metrics["physics_counters"]}
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError(f"汇总与原组指标不一致：{case}")
        if row["demand_sha256"] != entries[case]["demand_sha256"]:
            raise ValueError("批次与组需求摘要不一致")
        event = run["protocol"]["event"]
        data = baseline_view.read_run(directory)
        demand_note = "0—1170 s，固定 6 个 OD 各每 30 s 请求 1 辆普通车，共 240 辆；候选路线数量不增加需求。"
        if tasks:
            demand_note += f"本组另在 t={tasks[0]['release_s']} s 增加 1 辆医院外围接驳任务车，总计 241 辆；车辆性能和道路权限与普通车相同。"
        data["scenario"] = {"case": case, "title": policy_label(case), "demand_note": demand_note,
            "event_begin_s": event["begin_s"], "event_end_s": event["end_s"], "event_speed_m_s": event["speed_m_s"],
            "task_ids": [v["id"] for v in tasks],
            "warning": f"未标定工程对照 · 假设瑞金二路约68.735 m上游段在 {event['begin_s']}—{event['end_s']} s 限速 {event['speed_m_s']:g} m/s · B 协同机制未实现。",
            "policy_note": "仅西侧至医院 OD 可在预定出发前按当前已知路段长度/速度比较完整候选；已分配车辆不在途中改道。任务车不享受信号、车道或速度特权。"}
        for route in data["per_route"]:
            od = route["id"].split(":", 1)[0]
            suffix = "默认路径" if route["id"] == f"{od}:observed:{od}" else "绕行候选"
            route["label"] = ROUTE_LABELS.get(od, od) + " · " + suffix
        replay_data[case] = data
        run_manifests[case] = run
        rows.append({**row, "event_speed_m_s": event["speed_m_s"], "policy": policy_label(case), "task_count": len(tasks)})
    row_map = {row["case"]: row for row in rows}
    if len(comparisons) != 4:
        raise ValueError("缺少完整四对工程比较")
    for pair in comparisons:
        a, b = row_map[pair["fixed"]], row_map[pair["guided"]]
        ma, mb = run_manifests[a["case"]], run_manifests[b["case"]]
        if any(ma[k] != mb[k] for k in ("demand_sha256", "event_sha256", "config")):
            raise ValueError("配对组的需求、事件或车辆配置不同")
        delta = b["ordinary_system_time_s"] - a["ordinary_system_time_s"]
        task_delta = None if a["task_release_to_arrival_s"] is None else b["task_release_to_arrival_s"] - a["task_release_to_arrival_s"]
        if pair["ordinary_system_time_delta_s"] != delta or pair["task_release_to_arrival_delta_s"] != task_delta:
            raise ValueError("比较差值与原指标不一致")
    return {"batch_id": manifest["batch_id"], "manifest_sha256": sha256(batch / "manifest.json"),
            "rows": rows, "comparisons": comparisons, "protocol": protocol}, replay_data


def replay_page(data):
    page = baseline_view.PAGE.replace("未标定技术基准", "未标定工程对照").replace("SUMO TECHNICAL BASELINE", "SUMO ENGINEERING COMPARISON")
    page = page.replace('href="../replay/index.html">查看百度观测数据回放 →', 'href="../../index.html">← 返回八组工程对照总览')
    page = page.replace("假设需求 · 尚未完成现实验证", "假设慢行 · B 协同机制未实现")
    page = page.replace('<h1>未标定工程对照</h1>', '<h1>未标定工程对照</h1><p id="scenarioTitle"></p><p id="scenarioWarning" class="badge" style="white-space:normal;border-radius:8px;max-width:900px"></p>')
    page = page.replace('<p id="demand" class="muted"></p>', '<p id="demand" class="muted"></p><p id="policyNote" class="muted"></p>')
    page, count = re.subn(r"text\(el\('demand'\),[^;]*;", "text(el('demand'),data.scenario.demand_note);", page)
    if count != 1:
        raise ValueError("基准页面需求模板改变，需要重新核验场景适配")
    page = page.replace(";render();\n</script>", ";text(el('scenarioTitle'),data.scenario.title);text(el('scenarioWarning'),data.scenario.warning);text(el('policyNote'),data.scenario.policy_note);render();\n</script>")
    if "text(el('scenarioWarning')" not in page:
        raise ValueError("场景说明未正确挂载")
    return page.replace("__DATA__", safe_json(data))


def overview_page(info):
    batch = info["batch_id"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", batch):
        raise ValueError("不安全的批次标识")
    rows = []
    for row in info["rows"]:
        href = f"replays/{batch}/{row['case']}.html"
        values = [row["event_speed_m_s"], row["policy"], f"{row['arrived']}/{row['planned']}", row["ordinary_system_time_s"],
                  row["ordinary_departure_wait_s"], row["task_release_to_arrival_s"], row["changed_assignments"]]
        rows.append("<tr>" + "".join("<td>" + display_number(v) + "</td>" for v in values) + f'<td><a href="{escape(href, quote=True)}">打开回放</a></td></tr>')
    lookup = {r["case"]: r for r in info["rows"]}
    pairs = []
    for pair in info["comparisons"]:
        row = lookup[pair["fixed"]]
        values = [row["event_speed_m_s"], "S3 单任务" if row["task_count"] else "S2 普通交通", pair["ordinary_system_time_delta_s"], pair["task_release_to_arrival_delta_s"]]
        pairs.append("<tr>" + "".join("<td>" + display_number(v) + "</td>" for v in values) + "</tr>")
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>上海瑞金片区 · 工程对照总览</title><style>
body{margin:0;background:#f3f6f8;color:#213546;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1220px;margin:auto;padding:28px}h1{font-size:27px;margin:0}h2{font-size:18px}p{margin:9px 0}.card{background:white;border:1px solid #dce5ec;border-radius:12px;padding:20px;margin-top:18px}.notice{background:#fff4df;border-left:5px solid #be8730;padding:14px 18px;margin-top:18px}.small{font-size:12px;color:#607284}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:10px 8px;border-bottom:1px solid #e3e9ee;text-align:left}th{color:#526a7b;white-space:nowrap}a{color:#266a9b}td{font-variant-numeric:tabular-nums}nav{margin:8px 0}code{overflow-wrap:anywhere}@media(max-width:700px){main{padding:14px}th,td{padding:8px 6px}}</style></head><body><main>
<h1>八组工程对照 · 未标定</h1><nav><a href="../replay/index.html">百度真实观测快照</a> · <a href="../baseline/index.html">留档技术基准</a></nav>
<div class="notice"><strong>假设慢行事件；B 协同机制未实现；这些结果不是已验证的现实治理收益。</strong><p>固定 6 个 OD，在0—1170 s各每30 s请求1辆普通车，共240辆。S3另在720 s释放1辆医院外围接驳任务车；车辆性能与道路权限相同。1 m/s主情景和0.5 m/s强度敏感性全部保留。</p></div>
<section class="card"><h2>看什么</h2><p>在同一假设事件下比较“固定默认路径”和“独立出发前指派”。只在预定出发前用当前已知的路段长度/速度估算西侧至医院的候选成本，已经分配的车辆不在途中改道。模型尚未完成现实标定，未实现信号优先、车道特权或协同B。</p><p>慢行发生在瑞金二路约68.735 m上游段，模型时间600—1200 s；两个事件强度是1 m/s与0.5 m/s。医院两方向末端约292 m仍为当前子图中的共同必经区，接驳点不是已核验的院内急救入口。</p></section>
<section class="card"><h2>全部八组 · 实际完成的 SUMO 输出</h2><div class="scroll"><table><thead><tr><th>假设限速<br>m/s</th><th>场景与指派</th><th>到达/计划<br>辆</th><th>普通车系统时间<br>车·秒</th><th>普通车入网等待<br>车·秒</th><th>任务释放至到达<br>秒</th><th>改选路线<br>辆</th><th>真实FCD</th></tr></thead><tbody>''' + "".join(rows) + '''</tbody></table></div><p class="small">普通车系统时间 = 普通车在网时间 + 入网前等待，不含任务车；任务指标从释放时刻计时。—表示该组没有任务。八组均全部完成，碰撞、传送、丢弃计数为0；这只是模型运行检查，不是现实有效性证明。</p></section>
<section class="card"><h2>配对差值：独立指派 − 固定默认</h2><div class="scroll"><table><thead><tr><th>假设限速 m/s</th><th>任务设置</th><th>普通车系统时间差 车·秒</th><th>任务释放至到达差 秒</th></tr></thead><tbody>''' + "".join(pairs) + '''</tbody></table></div><p class="small">负值表示该假设下用时减少，正值表示增加，0表示无变化。1 m/s下没有车辆改选，全部差值为0；不能省略这组无改善结果。0.5 m/s下的变化只说明这一工程设置中的选择机制，不外推为现实改善比例。</p></section>
<p class="small">所有回放使用实际network.net.xml车道几何及5秒一次FCD位置快照，不插值，不映射现实采集时刻。配对组需求、事件及车辆配置一致；汇总与各组指标已逐项对账，页面输入均经留档SHA-256核验。</p><p class="small">批次 <code>''' + escape(batch) + '''</code><br>批次manifest SHA-256 <code>''' + escape(info["manifest_sha256"]) + '''</code></p><p class="small">AI-assisted: OpenAI Codex, 2026-10-17；具体模型快照/发布日期未提供，研究结论待队员复核。</p></main></body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线生成八组工程对照总览与独立FCD回放，不运行仿真")
    parser.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/scenarios")
    args = parser.parse_args(argv)
    try:
        batch, output = map(workspace_path, (args.batch_dir, args.output_dir))
        if output.is_relative_to(batch):
            raise ValueError("页面输出不能写入源批次")
        info, replays = read_batch(batch)
        pages = {case: replay_page(data) for case, data in replays.items()}
        overview = overview_page(info)
        replay_dir = workspace_path(output / "replays" / info["batch_id"])
        replay_dir.mkdir(parents=True, exist_ok=True)
        for case, page in pages.items():
            workspace_path(replay_dir / f"{case}.html").write_text(page, encoding="utf-8")
        workspace_path(output / "index.html").write_text(overview, encoding="utf-8")
        print(f"已生成 {output.relative_to(ROOT)}/index.html 与 {len(pages)} 个独立回放；批次和场景留档摘要、指标对账通过。")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"页面未生成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
