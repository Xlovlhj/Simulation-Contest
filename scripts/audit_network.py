#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；实现与研究使用待队员复核。
"""Audit route alternatives in the observed directed SUMO subgraph, offline."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "north_to_south": "北端→南端", "south_to_north": "南端→北端",
    "west_to_east": "西端→东端", "east_to_west": "东端→西端",
    "west_to_hospital_road": "西端→医院外围接驳点",
    "east_to_hospital_road": "东端→医院外围接驳点",
}


def workspace_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("输入与输出路径必须在当前工作区内")
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_path(graph, starts, goals, *, blocked_edge=None, blocked_turn=None,
              blocked_start=None, blocked_end=None):
    """BFS over road-edge states; proximity never creates a transition."""
    goals = set(goals) - {blocked_edge, blocked_end}
    queue, previous = deque(), {}
    for edge in sorted(set(starts) - {blocked_edge, blocked_start}):
        if edge in graph:
            previous[edge] = None
            queue.append(edge)
    while queue:
        edge = queue.popleft()
        if edge in goals:
            path = []
            while edge is not None:
                path.append(edge)
                edge = previous[edge]
            return list(reversed(path))
        for following in sorted(graph[edge]):
            if following == blocked_edge or (edge, following) == blocked_turn:
                continue
            if following not in previous:
                previous[following] = edge
                queue.append(following)
    return None


def valid_path(graph, path, starts, goals):
    return bool(path and len(path) == len(set(path)) and path[0] in starts
                and path[-1] in goals and all(edge in graph for edge in path)
                and all(b in graph[a] for a, b in zip(path, path[1:])))


def classify_paths(graph, starts, goals, preferred=None):
    """Find zero/one/at-least-two simple paths, without full enumeration.

    Any different simple source-to-sink path must omit the first path's start
    anchor, end anchor, or at least one transition. Removing each in turn and
    running BFS therefore proves uniqueness when no alternative is found.
    Stop immediately at two examples; this is a lower bound, not a total count.
    """
    starts, goals = set(starts), set(goals)
    first = list(preferred) if valid_path(graph, preferred, starts, goals) else find_path(graph, starts, goals)
    if first is None:
        return {"classification": "zero", "candidate_count": 0,
                "candidate_limit": 2, "count_complete": True, "paths": []}
    exclusions = [{"blocked_start": first[0]}, {"blocked_end": first[-1]}]
    exclusions.extend({"blocked_turn": pair} for pair in zip(first, first[1:]))
    for exclusion in exclusions:
        alternative = find_path(graph, starts, goals, **exclusion)
        if alternative is not None:
            return {"classification": "multiple", "candidate_count": 2,
                    "candidate_limit": 2, "count_complete": False,
                    "paths": [first, alternative]}
    return {"classification": "one", "candidate_count": 1,
            "candidate_limit": 2, "count_complete": True, "paths": [first]}


def mandatory_edges(graph, starts, goals, known_path):
    """Exact reachability cuts; do not infer them from two example paths."""
    if not known_path:
        return []
    return [edge for edge in known_path
            if find_path(graph, starts, goals, blocked_edge=edge) is None]


def path_details(path, edges):
    return {"edge_ids": path, "edge_count": len(path),
            "model_length_m": round(sum(edges[edge]["length_m"] for edge in path), 2)}


def read_network(run_dir, survey_path):
    paths = {name: workspace_path(run_dir / name) for name in
             ("network.net.xml", "route_manifest.json", "run_manifest.json")}
    run = json.loads(paths["run_manifest.json"].read_text(encoding="utf-8"))
    if run.get("status") != "completed":
        raise ValueError("只审计已完成且留档的基准运行")
    if run.get("input_sha256") != sha256(survey_path):
        raise ValueError("调查文件 SHA-256 与该基准的 input_sha256 不符；不能用新几何审计旧网络")
    for name in ("network.net.xml", "route_manifest.json"):
        if run.get("artifacts", {}).get(name) != sha256(paths[name]):
            raise ValueError("网络或路线清单与留档 SHA-256 不符")
    xml = ET.parse(paths["network.net.xml"]).getroot()
    edges = {}
    for element in xml.findall("edge"):
        if element.get("function") == "internal":
            continue
        lanes = element.findall("lane")
        if len(lanes) != 1:
            raise ValueError("本审计器限定当前单车道技术基准；多车道需单独审查换道约束")
        length = float(lanes[0].get("length", "nan"))
        if not math.isfinite(length) or length <= 0:
            raise ValueError("模型边长度无效")
        if lanes[0].get("allow") or lanes[0].get("disallow"):
            raise ValueError("存在车型通行约束，需扩展为指定车型的车道级审计")
        edges[element.attrib["id"]] = {"from": element.attrib["from"],
            "to": element.attrib["to"], "length_m": length}
    graph = {edge: set() for edge in edges}
    connection_details = []
    for element in xml.findall("connection"):
        a, b = element.get("from"), element.get("to")
        if a not in graph or b not in graph:
            continue
        if edges[a]["to"] != edges[b]["from"]:
            raise ValueError("显式转向连接的模型端点不连续")
        if element.get("fromLane") != "0" or element.get("toLane") != "0":
            raise ValueError("连接引用了当前基准之外的车道")
        graph[a].add(b)
        connection_details.append({"from": a, "to": b, "direction": element.get("dir")})
    manifest = json.loads(paths["route_manifest.json"].read_text(encoding="utf-8"))
    manifest_turns = {tuple(pair) for pair in manifest["connections"]}
    xml_turns = {(a, b) for a, children in graph.items() for b in children}
    if manifest_turns != xml_turns:
        raise ValueError("路线清单与 SUMO XML 的有向连接集合不一致")
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    chosen = {r["id"]: r for r in survey["routes"] if r.get("use_for_baseline") and r.get("accepted_surface")}
    routes = manifest["routes"]
    if {r["source_id"] for r in routes} != set(chosen):
        raise ValueError("留档路线与当前调查中选定的自然地面路线不一致")
    for route in routes:
        source = chosen[route["source_id"]]
        if any(route[k] != source[k] for k in ("origin_id", "destination_id")):
            raise ValueError("调查与模型的 OD 标识不一致")
        path = route["edge_ids"]
        if not path or not valid_path(graph, path, {path[0]}, {path[-1]}):
            raise ValueError("基准路线包含缺边、缺转向或重复边")
    nodes = {j.attrib["id"]: (float(j.attrib["x"]), float(j.attrib["y"]))
             for j in xml.findall("junction") if j.get("type") != "internal"}
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in paths.values()}
    source_hashes[str(survey_path.relative_to(ROOT))] = sha256(survey_path)
    return edges, graph, routes, nodes, connection_details, source_hashes


def build_report(run_dir, survey_path):
    edges, graph, routes, nodes, turns, hashes = read_network(run_dir, survey_path)
    starts, ends, usage = defaultdict(set), defaultdict(set), defaultdict(set)
    for route in routes:
        starts[route["origin_id"]].add(route["edge_ids"][0])
        ends[route["destination_id"]].add(route["edge_ids"][-1])
        for edge in route["edge_ids"]:
            usage[edge].add(route["source_id"])
    details = []
    for route in routes:
        path = route["edge_ids"]
        fixed = classify_paths(graph, {path[0]}, {path[-1]}, path)
        grouped = classify_paths(graph, starts[route["origin_id"]], ends[route["destination_id"]], path)
        fixed["mandatory_edge_ids"] = mandatory_edges(graph, {path[0]}, {path[-1]}, path)
        grouped["mandatory_edge_ids"] = mandatory_edges(graph, starts[route["origin_id"]], ends[route["destination_id"]], path)
        for query in (fixed, grouped):
            query["paths"] = [path_details(p, edges) for p in query["paths"]]
        grouped["allowed_start_edges"] = sorted(starts[route["origin_id"]])
        grouped["allowed_end_edges"] = sorted(ends[route["destination_id"]])
        details.append({"id": route["id"], "source_id": route["source_id"],
            "label": LABELS.get(route["source_id"], route["source_id"]),
            "origin_id": route["origin_id"], "destination_id": route["destination_id"],
            "fixed_model_edges": fixed, "observed_od_anchors": grouped})
    shared_groups = defaultdict(list)
    for edge, users in usage.items():
        if len(users) >= 2:
            shared_groups[tuple(sorted(users))].append(edge)
    shared = [{"route_ids": list(users), "route_count": len(users),
               **path_details(sorted(ids), edges)} for users, ids in shared_groups.items()]
    shared.sort(key=lambda group: (-group["route_count"], -group["model_length_m"]))
    # Shared groups are sets of segments, not necessarily a contiguous corridor.
    hospital = [r for r in details if r["destination_id"] == "RJ_YJ"]
    hospital_common = set.intersection(*(set(r["observed_od_anchors"]["mandatory_edge_ids"]) for r in hospital)) if hospital else set()
    anchor_groups = []
    for origin, entry_edges in sorted(starts.items()):
        anchors = [{"edge": e, "node": edges[e]["from"],
                    "model_xy_m": list(nodes[edges[e]["from"]])} for e in sorted(entry_edges)]
        distances = [{"from_edge": a["edge"], "to_edge": b["edge"],
                      "distance_m": round(math.dist(a["model_xy_m"], b["model_xy_m"]), 3)}
                     for i, a in enumerate(anchors) for b in anchors[i + 1:]]
        anchor_groups.append({"origin_id": origin, "anchors": anchors, "pair_distances": distances})
    by_from = defaultdict(set)
    for e, attrs in edges.items():
        by_from[attrs["from"]].add(e)
    missing_turns = sorted((a, b) for a, attrs in edges.items()
                           for b in by_from[attrs["to"]] if b not in graph[a])
    reversed_pairs = sorted((a, b) for a, attrs in edges.items()
                            for b in by_from[attrs["to"]] if edges[b]["to"] == attrs["from"])
    strict_multi = [r["source_id"] for r in details if r["fixed_model_edges"]["classification"] == "multiple"]
    conditional_multi = [r["source_id"] for r in details if r["observed_od_anchors"]["classification"] == "multiple"]
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "scope": "当前六条调查自然路线构成的已观测有向子图，不是完整上海路网；真实路网全拓扑未验证。",
        "model_status": "uncalibrated_technical_baseline",
        "source_sha256": hashes,
        "method": {
            "graph": "以模型有向边为状态，只使用 XML 中显式 from→to 连接；不按距离、相交或同节点补转向。",
            "fixed_model_edges": "严格保持现有路线首边和末边，可用于判断现有出发边之后是否存在途中重路由。",
            "observed_od_anchors": "仅将同一调查 OD 标识已出现的首末边作为出发/到达候选；不添加连接，起点选择只发生在出发前，入口可达性需另核验。",
            "classification": "首条路径逐一禁用起点锚点、终点锚点及转向并运行 BFS；无第二条则证明唯一，发现第二条即停止。",
            "candidate_limit": 2,
            "count_semantics": "multiple 的 2 表示至少两条，未全部枚举；one/zero 为该范围内完整结论。忽略重复边的绕圈。",
            "path_example": "保留基准作为首个示例，备选由 BFS 搜索；段数少不等于距离短或 ETA 优，未估算备选实时 ETA。",
            "mandatory_edges": "逐边删除后做可达性检查；结论不依赖只保存两条候选路径。",
        },
        "network": {"directed_edge_count": len(edges), "explicit_connection_count": len(turns),
                    "junction_count": len(nodes), "segmentation_note": "边/节点多为折线离散分段，数量不是实际道路条数或真实交叉口数。",
                    "branch_edges": [{"edge": e, "allowed_next_edges": sorted(v)} for e, v in sorted(graph.items()) if len(v) > 1],
                    "opposite_node_pair_count": len(reversed_pairs) // 2,
                    "opposite_pairs_note": "计数只描述模型精确端点反向边；不能据此推断现实道路均为单行。",
                    "node_compatible_but_unobserved_turns": [list(pair) for pair in missing_turns],
                    "unobserved_turns_note": "这些边虽在同一模型节点相接，XML 未许可连接，本审计不使用。现实是否可转向尚未核实。"},
        "routes": details,
        "origin_anchors": anchor_groups,
        "shared_segment_groups": shared,
        "shared_note": "共用分段表示结构性共同暴露，不是已观测拥堵或经容量校准证实的交通瓶颈；同组分段不保证连续。",
        "hospital_common_mandatory": {"route_ids": [r["source_id"] for r in hospital],
            **path_details(sorted(hospital_common), edges),
            "meaning": "对列出的医院外围 OD，即便允许上述出发前锚点选择，其中任意单边失效均无合法路径；实际医院急救入口未验证。"},
        "readiness": {"dynamic_rerouting_from_existing_start_edges": bool(strict_multi),
            "routes_with_strict_alternatives": strict_multi,
            "routes_with_conditional_predeparture_alternatives": conditional_multi,
            "capacity_or_eta_benefit_tested": False,
            "next_step": "针对共同必经段及替代走廊补充合法方向/转向证据和可连接几何，更新独立网络版本后复审；先勿把无路径的动态绕行作为已可实施策略。"},
    }


def compact_path(ids):
    groups = []
    for identity in ids:
        if groups and identity.startswith("e") and groups[-1][-1].startswith("e"):
            try:
                if int(identity[1:]) == int(groups[-1][-1][1:]) + 1:
                    groups[-1].append(identity)
                    continue
            except ValueError:
                pass
        groups.append([identity])
    return " → ".join(group[0] if len(group) == 1 else f"{group[0]}…{group[-1]}" for group in groups)


def markdown(report):
    ready = report["readiness"]
    strict_count = len(ready["routes_with_strict_alternatives"])
    lines = ["# 当前观测子图：路径与绕行就绪审计", "", report["scope"], "",
             f"保持既有模型首末边时，{len(report['routes'])} 条路线中 {strict_count} 条有已证实的不同可行路径。"
             + ("目前不能在既有车辆出发后构造有意义的动态绕行。" if not strict_count else "存在路径备选，但通行收益仍需独立验证。"), "",
             "| 调查路线 | 固定首末边的路径数 | 同 OD 已观测锚点的路径数 | 基准模型长度（m） |",
             "|---|---:|---:|---:|"]
    for route in report["routes"]:
        fixed, grouped = route["fixed_model_edges"], route["observed_od_anchors"]
        count = str(grouped["candidate_count"]) if grouped["count_complete"] else f"≥{grouped['candidate_count']}（搜索上限 2）"
        fixed_count = str(fixed["candidate_count"]) if fixed["count_complete"] else "≥2（搜索上限 2）"
        lines.append(f"| {route['label']}（{route['origin_id']}→{route['destination_id']}） | {fixed_count} | {count} | {fixed['paths'][0]['model_length_m']:.2f} |")
    lines += ["", "这里的备选必须逐段遵守 XML 已有有向连接；相邻、相交或属于同一路口都不会自动增加可通行转向。两条候选仅证明“多条”，不是全部路径枚举。固定首末边的唯一性已用逐转向禁用后的可达性检查证明。", ""]
    for route in report["routes"]:
        if route["observed_od_anchors"]["classification"] != "multiple":
            continue
        first, alt = route["observed_od_anchors"]["paths"][:2]
        lines += [f"**条件性出发前候选：{route['label']}。** 原路径 {first['edge_count']} 段、{first['model_length_m']:.2f} m；备选示例 {alt['edge_count']} 段、{alt['model_length_m']:.2f} m。",
                  f"备选序列：`{compact_path(alt['edge_ids'])}`。连续编号仅为显示缩写，完整序列见 JSON。",
                  f"原首边 `{first['edge_ids'][0]}`，备选首边 `{alt['edge_ids'][0]}`。二者属于同一调查起点标识；此处只允许出发前选择已有首边，没有添加两起点之间的连接。不能据此宣称原首边上的车辆可以切换到备选首边。备选也没有新采样 ETA 或已证实的时间收益。", ""]
        anchor = next(a for a in report["origin_anchors"] if a["origin_id"] == route["origin_id"])
        for distance in anchor["pair_distances"]:
            lines.append(f"对应首边起点的模型平面距离为 {distance['distance_m']:.3f} m；接近不等于合法连通，出发边选择的现实可达性仍需核验。")
        lines.append("")
    network = report["network"]
    lines += [f"网络含 {network['directed_edge_count']} 个有向分段、{network['explicit_connection_count']} 个显式连接、{len(network['branch_edges'])} 个有多个后继的分段。分段和几何节点数不代表真实道路或交叉口数。精确端点配对的反向边为 {network['opposite_node_pair_count']} 对，这只描述子图；不能推断现实道路均单行。另有 {len(network['node_compatible_but_unobserved_turns'])} 个同节点但未许可的边对，审计未把它们补为转向。", "",
              "共用分段是结构性暴露，尚未证明实际拥堵或容量瓶颈：", "",
              "| 共用路线 | 共用分段数 | 总长度（m，不保证连续） |", "|---|---:|---:|"]
    for group in report["shared_segment_groups"]:
        names = "、".join(LABELS.get(name, name) for name in group["route_ids"])
        lines.append(f"| {names} | {group['edge_count']} | {group['model_length_m']:.2f} |")
    common = report["hospital_common_mandatory"]
    lines += ["", f"两条医院外围接驳 OD 共同必经 {common['edge_count']} 个分段，合计 {common['model_length_m']:.2f} m；即使纳入条件性出发前候选，其中任一分段失效仍会切断两条 OD 在当前子图中的通路。该结论由逐边删除后重新检查可达性得出，不是只取两条示例路径交集。RJ_YJ 是医院附近公共道路接驳点，急救入口未核验。", "",
              "下一步：先补上述必经段周围替代走廊的实际方向、转向与连续几何证据，建立新的网络版本，再复审备选路径和开展同条件仿真。当前可继续积累样本、完成标定验证与策略设计；等待更多 ETA 本身不会自动补齐缺失拓扑。", "",
              "复现：`python3 scripts/audit_network.py`；可通过 `--run-dir`、`--survey`、`--output-dir` 指定工作区内路径。全程离线，标准库实现，不读取私密配置，不修改基准模型。JSON 保存输入 SHA-256、所有候选完整边序列、必经边和连接缺口。", "",
              f"生成时间：{report['generated_at']}。AI-assisted: OpenAI Codex, 2026-10-17；具体模型快照／发布日期未提供，结论待队员复核。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线审计当前观测有向子图的路径备选、共同必经段与动态绕行就绪状态")
    parser.add_argument("--run-dir", type=Path, default=ROOT / "outputs/baseline/natural_surface_baseline_v1")
    parser.add_argument("--survey", type=Path, default=ROOT / "data/processed/baidu/survey_routes.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/tables")
    args = parser.parse_args(argv)
    try:
        run_dir, survey, output = map(workspace_path, (args.run_dir, args.survey, args.output_dir))
        if output == run_dir or output.is_relative_to(run_dir):
            raise ValueError("审计输出不能写入基准留档目录")
        report = build_report(run_dir, survey)
        hashes = report["source_sha256"]
        # Finish all input validation before creating an output directory.
        output.mkdir(parents=True, exist_ok=True)
        targets = [workspace_path(output / name) for name in ("network_readiness.json", "network_readiness.md")]
        if any(str(path.relative_to(ROOT)) in hashes for path in targets):
            raise ValueError("输出路径不能覆盖输入")
        targets[0].write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        targets[1].write_text(markdown(report), encoding="utf-8")
        print(json.dumps({"outputs": [str(p.relative_to(ROOT)) for p in targets],
                          "readiness": report["readiness"]}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        print(f"审计未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
