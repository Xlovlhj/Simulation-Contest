#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；实现与研究使用待队员复核。
"""Keep observed topology, eligible paths and the six demand ODs separate."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

try:
    from .audit_network import ROOT, LABELS, classify_paths, mandatory_edges, sha256, workspace_path
except ImportError:
    from audit_network import ROOT, LABELS, classify_paths, mandatory_edges, sha256, workspace_path


def points(raw):
    out = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("无效坐标对")
        pair = tuple(float(value) for value in item)
        if not all(math.isfinite(v) for v in pair) or not (-180 <= pair[0] <= 180 and -90 <= pair[1] <= 90):
            raise ValueError("无效经纬度")
        pair = tuple(round(v, 6) for v in pair)
        if not out or out[-1] != pair:
            out.append(pair)
    if len(out) < 2:
        raise ValueError("路线缺少有效折线")
    return out


def projected(point, center):
    return (6371008.8 * math.radians(point[0] - center[0]) * math.cos(math.radians(center[1])),
            6371008.8 * math.radians(point[1] - center[1]))


def source_rows(path):
    content = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(content, list):
        return content
    for key in ("routes", "route_candidates", "candidates"):
        if isinstance(content.get(key), list):
            return content[key]
    raise ValueError("候选文件须包含 routes/route_candidates/candidates 列表")


def road_name(instruction):
    plain = re.sub(r"<[^>]*>", "", instruction)
    match = re.search(r"沿([^,，]+?)行驶", plain)
    return match.group(1) if match else None


def raw_evidence(row):
    path = workspace_path(ROOT / row["source_response"])
    if row.get("source_sha256") and row["source_sha256"] != sha256(path):
        raise ValueError("原始响应与处理后清单 source_sha256 不符")
    response = json.loads(path.read_text(encoding="utf-8"))
    if response.get("status") != 0:
        raise ValueError("候选源接口响应未成功")
    route = response["result"]["routes"][int(row.get("route_index", 0))]
    all_points, roadnames, uturns, previous_edges, instructions = [], defaultdict(set), set(), None, []
    previous_uturn = False
    for step in route["steps"]:
        pp = points([[float(v) for v in token.split(",")] for token in step["path"].split(";") if token])
        edges = list(zip(pp, pp[1:]))
        if all_points and all_points[-1] != pp[0]:
            raise ValueError("原始 step 边界不连续，不能自动增加连接")
        if previous_edges and previous_uturn:
            uturns.add((previous_edges[-1], edges[0]))
        name = road_name(step.get("instruction", ""))
        if name:
            for edge in edges:
                roadnames[edge].add(name)
        instructions.append(re.sub(r"<[^>]*>", "", step.get("instruction", "")))
        all_points.extend(pp[1:] if all_points else pp)
        previous_edges = edges
        previous_uturn = str(step.get("turn")) == "5" or "调头" in instructions[-1] or "掉头" in instructions[-1]
    if points(row["points"]) != points(all_points):
        raise ValueError("处理后几何与所引用原始路线不符")
    explicit_uturns = sum("调头" in text or "掉头" in text for text in instructions)
    return path, points(all_points), roadnames, uturns, instructions, explicit_uturns


def build_catalog(survey_path, candidate_paths=()):
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    if survey.get("coordinate_system") != "bd09ll":
        raise ValueError("只处理明确标记为 bd09ll 的调查")
    bounds = survey.get("model_bounds", survey["collection_bounds"])
    center = [(bounds["west"] + bounds["east"]) / 2, (bounds["south"] + bounds["north"]) / 2]
    inputs = [survey_path, *candidate_paths]
    hashes = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    rows = [(row, True) for row in survey["routes"]]
    for path in candidate_paths:
        content = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(content, dict):
            if content.get("base_survey_sha256") and content["base_survey_sha256"] != sha256(survey_path):
                raise ValueError("新增候选所绑定的原调查 SHA-256 不一致")
            if content.get("coordinate_system", "bd09ll") != "bd09ll" or content.get("study_area_id", survey["study_area_id"]) != survey["study_area_id"]:
                raise ValueError("新增候选的坐标系或研究区不一致")
        rows.extend((row, False) for row in source_rows(path))
    nodes, edges, turns, evidence, excluded = {}, {}, defaultdict(set), [], []
    prohibited_uturns = set()
    demand_ods = []
    seen_ids = set()
    for row, original in rows:
        rid = str(row["id"])
        if rid in seen_ids:
            raise ValueError("来源路线 ID 重复；请给新增调查使用独立 ID")
        seen_ids.add(rid)
        if row.get("accepted_surface") is not True:
            excluded.append({"id": rid, "reason": "surface_route_not_verified"})
            continue
        path, pp, names, source_uturns, instructions, uturn_count = raw_evidence(row)
        hashes[str(path.relative_to(ROOT))] = sha256(path)
        outside = [i for i, (lng, lat) in enumerate(pp)
                   if not (bounds["west"] <= lng <= bounds["east"] and bounds["south"] <= lat <= bounds["north"])]
        if outside:
            excluded.append({"id": rid, "reason": "outside_existing_model_bounds", "outside_point_indices": outside})
            continue
        chain = []
        for a, b in zip(pp, pp[1:]):
            for p in (a, b):
                if p not in nodes:
                    nodes[p] = {"id": f"n{len(nodes):06d}", "point": list(p), "xy_m": list(projected(p, center))}
            key = (a, b)
            if key not in edges:
                edges[key] = {"id": f"e{len(edges):06d}", "from": nodes[a]["id"], "to": nodes[b]["id"],
                              "length_m": math.dist(nodes[a]["xy_m"], nodes[b]["xy_m"]),
                              "road_names": set(), "source_routes": set()}
            edge = edges[key]
            edge["source_routes"].add(rid)
            edge["road_names"].update(names.get(key, set()))
            chain.append(edge["id"])
        for pair in zip(chain, chain[1:]):
            turns[pair].add(rid)
        for a, b in source_uturns:
            prohibited_uturns.add((edges[a]["id"], edges[b]["id"]))
        repeated_edges = sorted(edge for edge, count in Counter(chain).items() if count > 1)
        repeated_nodes = sorted(nodes[p]["id"] for p, count in Counter(pp).items() if count > 1)
        reasons = []
        if repeated_edges:
            reasons.append("repeated_directed_edges")
        if repeated_nodes:
            reasons.append("repeated_nodes")
        if uturn_count or source_uturns:
            reasons.append("explicit_uturn")
        item = {"id": rid, "origin_id": row["origin_id"], "destination_id": row["destination_id"],
                "source_response": str(path.relative_to(ROOT)), "source_route_index": int(row.get("route_index", 0)),
                "requested_at": row.get("requested_at"), "distance_m": row.get("distance_m"),
                "platform_duration_s": row.get("duration_s"), "waypoint_id": row.get("waypoint_id"),
                "edge_ids": chain, "instructions": instructions,
                "complete_route_eligible": not reasons, "complete_route_rejection_reasons": reasons,
                "repeated_edge_ids": repeated_edges, "repeated_node_ids": repeated_nodes,
                "explicit_uturn_count": uturn_count, "adds_demand": False}
        evidence.append(item)
        if original and row.get("use_for_baseline") is True:
            if reasons:
                raise ValueError("原基准需求路线含循环或调头，需人工复核")
            demand_ods.append({"id": rid, "label": LABELS.get(rid, rid),
                "origin_id": row["origin_id"], "destination_id": row["destination_id"],
                "baseline_evidence_id": rid, "baseline_edge_ids": chain})
    expected_demands = {r["id"] for r in survey["routes"] if r.get("use_for_baseline") is True}
    if {d["id"] for d in demand_ods} != expected_demands:
        raise ValueError("原有需求路线被排除，不能静默改变需求集合")
    by_id = {edge["id"]: edge for edge in edges.values()}
    graph = {e: set() for e in by_id}
    for a, b in turns:
        if (a, b) not in prohibited_uturns:
            graph[a].add(b)
    for item in evidence:
        if any(pair in prohibited_uturns for pair in zip(item["edge_ids"], item["edge_ids"][1:])):
            item["complete_route_eligible"] = False
            if "explicit_uturn" not in item["complete_route_rejection_reasons"]:
                item["complete_route_rejection_reasons"].append("uturn_identified_by_other_source")
    candidates, queries = [], []
    for demand in demand_ods:
        baseline = demand["baseline_edge_ids"]
        selected = set()
        for item in evidence:
            if (item["origin_id"], item["destination_id"]) != (demand["origin_id"], demand["destination_id"]) or not item["complete_route_eligible"]:
                continue
            chain = item["edge_ids"]
            key = tuple(chain)
            if key in selected:
                continue
            selected.add(key)
            candidates.append({"id": f"{demand['id']}:observed:{item['id']}", "demand_od_id": demand["id"],
                "kind": "directly_observed", "evidence_route_ids": [e["id"] for e in evidence if e["edge_ids"] == chain and e["complete_route_eligible"]],
                "edge_ids": chain, "model_length_m": round(sum(by_id[e]["length_m"] for e in chain), 3),
                "same_baseline_start_end_edges": (chain[0], chain[-1]) == (baseline[0], baseline[-1]),
                "platform_eta_samples": [{"evidence_route_id": e["id"], "requested_at": e["requested_at"], "duration_s": e["platform_duration_s"]}
                    for e in evidence if e["edge_ids"] == chain and e["complete_route_eligible"]],
                "adds_demand": False})
        query = classify_paths(graph, {baseline[0]}, {baseline[-1]}, baseline)
        for chain in query["paths"]:
            if tuple(chain) in selected:
                continue
            # Edge-simple BFS paths can revisit a junction. Do not label them
            # as clean policy candidates unless the node path is simple too.
            node_chain = [by_id[chain[0]]["from"]] + [by_id[e]["to"] for e in chain]
            if len(node_chain) != len(set(node_chain)):
                continue
            selected.add(tuple(chain))
            candidates.append({"id": f"{demand['id']}:derived:{len(selected)}", "demand_od_id": demand["id"],
                "kind": "derived_from_observed_connections", "edge_ids": chain,
                "model_length_m": round(sum(by_id[e]["length_m"] for e in chain), 3),
                "same_baseline_start_end_edges": True, "platform_eta_samples": [], "adds_demand": False})
        queries.append({"demand_od_id": demand["id"], "fixed_baseline_edges": query,
            "mandatory_edge_ids": mandatory_edges(graph, {baseline[0]}, {baseline[-1]}, baseline),
            "eligible_catalog_candidate_ids": [c["id"] for c in candidates if c["demand_od_id"] == demand["id"]]})
    serialized_edges = [{**edge, "length_m": round(edge["length_m"], 6),
                         "road_names": sorted(edge["road_names"]), "source_routes": sorted(edge["source_routes"])} for edge in edges.values()]
    hospital_queries = [q for q in queries if next(d for d in demand_ods if d["id"] == q["demand_od_id"])["destination_id"] == "RJ_YJ"]
    common = set.intersection(*(set(q["mandatory_edge_ids"]) for q in hospital_queries)) if hospital_queries else set()
    return {"schema_version": 1, "purpose": "topology_and_route_candidates_only_not_a_demand_dataset",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "study_area_id": survey["study_area_id"], "coordinate_system": "bd09ll",
        "scope": "当前观测子图；完整上海路网拓扑未核验。循环调查仅提供合法方向/转向证据，不能作为策略完整路线。",
        "model_bounds": bounds, "projection_center": center,
        "normalization": "沿用基准经纬度6位小数；仅同坐标同方向分段复用，不吸附邻近点、不补交叉转向、不新增逆向边。",
        "source_sha256": hashes, "builder_sha256": sha256(Path(__file__)),
        "demand_ods": demand_ods, "demand_note": "仅保持原调查选中的需求OD；本目录不设车辆数或发车时刻。额外证据和候选不增加车辆需求。",
        "network": {"nodes": list(nodes.values()), "edges": serialized_edges,
            "connections": [{"from": a, "to": b, "source_routes": sorted(source),
                "explicit_uturn": (a, b) in prohibited_uturns, "eligible_for_policy_path": (a, b) not in prohibited_uturns}
                for (a, b), source in sorted(turns.items())]},
        "topology_evidence_routes": evidence, "excluded_evidence_routes": excluded,
        "route_candidates": candidates, "path_audit": queries,
        "audit_note": "固定原首末边，仅搜索最多2条边简单路径；multiple为至少2条非完整枚举。逐边删除确定必经边；策略候选额外要求节点不重复、排除显式调头。",
        "hospital_common_mandatory": {"edge_ids": sorted(common), "edge_count": len(common),
            "model_length_m": round(sum(by_id[e]["length_m"] for e in common), 3),
            "road_names": sorted({name for e in common for name in by_id[e]["road_names"]}),
            "note": "RJ_YJ 为医院外围道路接驳点，急救入口未核验；共同必经段无旁路时应报告不可达。"}}


def report_text(catalog):
    network = catalog["network"]
    lines = ["# 独立路线目录与扩展子图", "", catalog["scope"], "",
        f"保留 {len(catalog['demand_ods'])} 个既有需求 OD；{len(catalog['topology_evidence_routes'])} 条调查仅作为网络证据。"
        f"精确合并得到 {len(network['edges'])} 个有向分段和 {len(network['connections'])} 个已观测转向；"
        f"其中 {sum(c['explicit_uturn'] for c in network['connections'])} 个显式调头转向不进入策略候选搜索。", "",
        "| 需求 OD | 固定既有首末边的路径数 | 目录中的完整候选 |", "|---|---:|---:|"]
    for query in catalog["path_audit"]:
        count = query["fixed_baseline_edges"]["candidate_count"]
        label = str(count) if query["fixed_baseline_edges"]["count_complete"] else f"≥{count}（搜索上限2）"
        lines.append(f"| {LABELS.get(query['demand_od_id'], query['demand_od_id'])} | {label} | {len(query['eligible_catalog_candidate_ids'])} |")
    lines += ["", "候选列表可包含同一 OD 不同吸附出发边的完整接口路线；是否保持原首末边由 JSON 中 same_baseline_start_end_edges 单独标记。额外候选不产生额外车辆。派生路径仅复用逐段已观测转向，不填造全程 ETA。", ""]
    for item in catalog["topology_evidence_routes"]:
        if not item["complete_route_eligible"]:
            lines.append(f"- `{item['id']}` 完整路线排除：显式调头 {item['explicit_uturn_count']} 次，重复有向分段 {len(item['repeated_edge_ids'])} 个，重复节点 {len(item['repeated_node_ids'])} 个；保留来源可追溯的道路与转向证据。")
    common = catalog["hospital_common_mandatory"]
    lines += ["", f"医院两 OD 共同必经 {common['edge_count']} 段，约 {common['model_length_m']:.2f} m，原始分步指令道路名：{'、'.join(common['road_names']) or '未知'}。"
        "事件试验可优先选择已证明可绕行的上游段；目标附近必经段受阻应报告不可达，不为展示效果补造旁路。", "",
        "目录仅用于独立扩展网络与路线选择；不是可直接交给原基准脚本按路线生成需求的输入文件。当前未开展新仿真或证明治理收益。", "",
        "AI-assisted: OpenAI Codex, 2026-10-17；具体模型快照／发布日期未提供，证据解释与策略使用待队员复核。", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线构建独立拓扑/路线候选目录，保持原6个需求OD不变")
    parser.add_argument("--survey", type=Path, default=ROOT / "data/processed/baidu/survey_routes.json")
    parser.add_argument("--candidates", type=Path, action="append", default=[], help="可重复指定新增处理后候选JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/baidu/route_catalog_v1.json")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "outputs/tables")
    args = parser.parse_args(argv)
    try:
        survey, output, report_dir = map(workspace_path, (args.survey, args.output, args.report_dir))
        candidates = [workspace_path(path) for path in args.candidates]
        catalog = build_catalog(survey, candidates)
        report = workspace_path(report_dir / "route_catalog_readiness.md")
        if any(str(path.relative_to(ROOT)) in catalog["source_sha256"] for path in (output, report)):
            raise ValueError("不能覆盖输入证据")
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.write_text(report_text(catalog), encoding="utf-8")
        print(json.dumps({"catalog": str(output.relative_to(ROOT)), "report": str(report.relative_to(ROOT)),
            "demand_od_count": len(catalog["demand_ods"]), "candidate_count": len(catalog["route_candidates"]),
            "edge_count": len(catalog["network"]["edges"]), "connection_count": len(catalog["network"]["connections"])}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print(f"目录未生成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
