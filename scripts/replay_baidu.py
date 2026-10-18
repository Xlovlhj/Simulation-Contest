#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17.
# 具体模型快照／发布日期未提供；实现与研究使用待队员复核。
"""Offline, standard-library-only audit and SVG replay of saved Baidu responses.

No network, credentials, GIS tiles, vehicle simulation or interpolation is used.
Only accepted Shanghai manifests and whitelisted response fields are exported.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import html
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "百度地图接口／平台估计"
COORDINATE = "BD09LL"
CHINA_TIME = timezone(timedelta(hours=8))
UNKNOWN_NAMES = {"", "UNKNOW", "UNKNOWN", "NULL", "NONE", "未知", "无名道路"}
TIME_KEYS = {"data_time", "update_time", "updatetime", "source_time", "timestamp", "time", "datetime"}
ROUTE_LABELS = {"north_to_south": "北端至南端", "south_to_north": "南端至北端", "west_to_east": "西端至东端", "east_to_west": "东端至西端", "west_to_hospital_road": "西端至医院外围接驳点", "east_to_hospital_road": "东端至医院外围接驳点", "west_to_east_via_maoming": "西至东 · 经茂名南路", "east_to_west_via_jianguo": "东至西 · 经建国中路"}


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def nonnegative(value):
    value = number(value)
    return value if value is not None and value >= 0 else None


def clean_text(value, limit=500):
    """Export ordinary display text, not URLs, credentials, or response blobs."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    value = html.unescape(re.sub(r"<[^>]*>", "", str(value)))
    value = re.sub(r"https?://\S+", "[链接已省略]", value, flags=re.I)
    value = re.sub(r"(?i)\b(?:ak|sk|sn|api[_-]?key|secret|token|signature)\s*[:=]\s*[^\s,;]+", "[凭据已省略]", value)
    return "".join(c for c in value[:limit] if c >= " " or c in "\n\t")


def road_name(value):
    value = clean_text(value)
    return "" if value.upper() in UNKNOWN_NAMES else value


def load_json(path):
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def inside_workspace(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("所有输入和输出路径必须位于当前工作区")
    if path.is_relative_to(ROOT / "config") or ".local." in path.name:
        raise ValueError("离线回放工具不读取或写入私密配置")
    return path


def response_path(batch_dir, value):
    if not isinstance(value, str) or not value or ".local." in value:
        return None
    path = (batch_dir / value).resolve()
    if not path.is_relative_to(batch_dir.resolve()) or path.suffix != ".json":
        return None
    return path


def haversine(first, second):
    """Approximate metres for nearby BD09 points; not survey-grade geodesy."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*first, *second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, a))))


def parse_path(value):
    """Return continuous valid parts; never bridge an invalid point."""
    tokens = value.split(";") if isinstance(value, str) else value if isinstance(value, list) else []
    parts, current, invalid = [], [], 0
    for token in tokens:
        if token == "":
            continue
        if isinstance(token, str):
            pair = token.split(",")
        elif isinstance(token, dict):
            pair = [token.get("lng"), token.get("lat")]
        elif isinstance(token, (list, tuple)):
            pair = token
        else:
            pair = []
        pair = [number(v) for v in pair] if len(pair) == 2 else []
        if len(pair) != 2 or None in pair or not (-180 <= pair[0] <= 180 and -90 <= pair[1] <= 90):
            invalid += 1
            if current:
                parts.append(current)
                current = []
            continue
        current.append(pair)
    if current:
        parts.append(current)
    return parts, invalid


def bounds_from(value):
    if isinstance(value, dict):
        bounds = {k: number(value.get(k)) for k in ("south", "north", "west", "east")}
    elif isinstance(value, str):
        try:
            a, b = value.split(";")
            south, west = a.split(",")
            north, east = b.split(",")
            bounds = {k: number(v) for k, v in zip(("south", "north", "west", "east"), (south, north, west, east))}
        except ValueError:
            return None
    else:
        return None
    if None in bounds.values():
        return None
    if not (-90 <= bounds["south"] < bounds["north"] <= 90 and -180 <= bounds["west"] < bounds["east"] <= 180):
        return None
    return bounds


def in_bounds(point, bounds):
    return bounds["west"] <= point[0] <= bounds["east"] and bounds["south"] <= point[1] <= bounds["north"]


def source_times(payload):
    """Response timestamps only; request signing timestamp is never an observation."""
    found = []
    def visit(value, prefix=""):
        if isinstance(value, dict):
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else key
                if key.lower() in TIME_KEYS and isinstance(item, (int, float, str)):
                    safe = clean_text(item, 100)
                    if safe and len(found) < 20:
                        found.append({"field": clean_text(path, 160), "value": safe})
                elif isinstance(item, (dict, list)):
                    visit(item, path)
        elif isinstance(value, list):
            for item in value:
                visit(item, prefix + "[]")
    visit(payload)
    return found


def request_metadata(request, payload):
    times = source_times(payload)
    return {
        "requested_at": clean_text(request.get("requested_at"), 80),
        "received_at": clean_text(request.get("received_at"), 80),
        "source_data_time": "; ".join(f"{t['field']}={t['value']}" for t in times),
        "source": SOURCE, "coordinate_system": COORDINATE,
    }


def issue(snapshot, code, message, item=""):
    snapshot["issues"].append({"code": code, "message": message, "item": item})


def extract_route(snapshot, request, payload, request_index):
    result = payload.get("result", {})
    if not isinstance(result, dict):
        issue(snapshot, "invalid_result", "路线响应 result 不是对象", str(request_index))
        return
    routes = result.get("routes", [])
    if not isinstance(routes, list):
        issue(snapshot, "invalid_routes", "路线列表格式无效", str(request_index))
        return
    meta = request_metadata(request, payload)
    for i, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        rid = f"request_{request_index}_route_{i + 1}"
        route_label = clean_text(request.get("route_pair_id") or request.get("od_id") or request.get("route_id") or request.get("label")) or f"路线 {len(snapshot['routes']) + 1}"
        parts, invalid, steps = [], 0, []
        raw_steps = route.get("steps", [])
        if not isinstance(raw_steps, list):
            raw_steps = []
        for j, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                continue
            step_parts, bad = parse_path(step.get("path"))
            parts.extend(step_parts)
            invalid += bad
            steps.append({"step": j + 1, "instruction": clean_text(step.get("instruction")), "direction": nonnegative(step.get("direction")), "platform_distance_m": nonnegative(step.get("distance")), "platform_duration_s": nonnegative(step.get("duration"))})
        if not raw_steps:
            parts, invalid = parse_path(route.get("path"))
        points = [p for part in parts for p in part]
        distance = sum(haversine(a, b) for part in parts for a, b in zip(part, part[1:])) if points else None
        platform_distance = nonnegative(route.get("distance"))
        duration = nonnegative(route.get("duration"))
        outside = sum(not any(in_bounds(p, b) for b in snapshot["bounds"]) for p in points) if snapshot["bounds"] else None
        difference = distance - platform_distance if distance is not None and platform_distance is not None else None
        ratio = difference / platform_distance if difference is not None and platform_distance else None
        geometry_hash = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest() if points else ""
        row = {"id": rid, "label": ROUTE_LABELS.get(route_label, route_label), "route_pair_id": route_label, "route_number": i + 1, **meta,
               "platform_distance_m": platform_distance, "platform_eta_s": duration,
               "eta_implied_speed_kmh": platform_distance / duration * 3.6 if platform_distance is not None and duration else None,
               "geometry_distance_m": round(distance, 2) if distance is not None else None,
               "distance_difference_m": round(difference, 2) if difference is not None else None,
               "distance_difference_ratio": round(ratio, 5) if ratio is not None else None,
               "point_count": len(points), "invalid_point_count": invalid, "points_outside_query_bounds": outside,
               "geometry_sha256": geometry_hash, "parts": parts, "steps": steps}
        snapshot["routes"].append(row)
        model_bounds = snapshot.get("model_bounds")
        row["points_outside_model_bounds"] = sum(not in_bounds(p, model_bounds) for p in points) if model_bounds else None
        if invalid:
            issue(snapshot, "invalid_coordinates", f"{invalid} 个坐标无效，断开绘制；几何距离为有效片段之和", rid)
        if not points:
            issue(snapshot, "missing_geometry", "未返回可绘制路线坐标", rid)
        if outside:
            issue(snapshot, "geometry_outside_query_bounds", f"{outside}/{len(points)} 个点在采样矩形之外；整程 ETA 不能用于仅覆盖矩形内的模型", rid)
        if row["points_outside_model_bounds"]:
            issue(snapshot, "geometry_outside_model_bounds", f"{row['points_outside_model_bounds']} 个点在候选模型边界外，需调整模型覆盖或路线", rid)
        if not snapshot["bounds"]:
            issue(snapshot, "no_query_bounds", "没有有效采样矩形，无法检查几何越界", rid)
        if ratio is not None and abs(ratio) > .15 and abs(difference) > 100:
            issue(snapshot, "geometry_distance_difference", "几何近似长度与平台距离差异超过 15% 且 100 米；请核查简化、断点和口径，不自动判错", rid)
        if len(parts) > 1 and any(haversine(a[-1], b[0]) > 30 for a, b in zip(parts, parts[1:])):
            issue(snapshot, "geometry_gaps", "路线片段之间存在超过 30 米的断点；未补造连接线", rid)


def extract_traffic(snapshot, request, payload, request_index):
    meta = request_metadata(request, payload)
    evaluation = payload.get("evaluation", {})
    if isinstance(evaluation, dict):
        snapshot["evaluations"].append({**meta, "description": clean_text(evaluation.get("status_desc")), "status": nonnegative(evaluation.get("status"))})
    roads = payload.get("road_traffic", [])
    if not isinstance(roads, list):
        issue(snapshot, "invalid_road_traffic", "道路路况列表格式无效", str(request_index))
        return
    for i, road in enumerate(roads):
        if not isinstance(road, dict):
            continue
        name = road_name(road.get("road_name"))
        if not name:
            issue(snapshot, "unknown_road_name", "道路名称未知，保持空白，不与其他道路合并", f"request_{request_index}_road_{i + 1}")
        sections = road.get("congestion_sections", [])
        if not isinstance(sections, list):
            sections = []
        # Preserve roads without numeric sections; absence is not zero speed.
        for j, section in enumerate(sections or [{}]):
            if not isinstance(section, dict):
                continue
            snapshot["traffic"].append({"id": f"request_{request_index}_road_{i + 1}_section_{j + 1}", **meta,
                "road_name": name, "section_desc": clean_text(section.get("section_desc")),
                "speed_kmh": nonnegative(section.get("speed")), "congestion_distance_m": nonnegative(section.get("congestion_distance")),
                "status": nonnegative(section.get("status")), "road_type": nonnegative(section.get("road_type")),
                "trend": clean_text(section.get("congestion_trend")), "has_congestion_section": bool(section)})


def extract_junction(snapshot, request, payload, request_index):
    result = payload.get("result", {})
    if not isinstance(result, dict):
        return
    parts, invalid = parse_path([result.get("location")])
    if invalid or not parts:
        issue(snapshot, "invalid_junction_coordinate", "路口定位坐标无效，已跳过", str(request_index))
        return
    params = request.get("params", {})
    identifier = clean_text(request.get("label")) or f"junction_{request_index}"
    snapshot["nodes"].append({"id": identifier, "label": clean_text(params.get("address")) if isinstance(params, dict) else identifier,
        "point": parts[0][0], "level": clean_text(result.get("level")), "confidence": nonnegative(result.get("confidence")),
        "note": "研究设定的医院外围道路接驳点；急救入口未验证" if identifier == "RJ_YJ" else "平台路口定位；连接与转向仍需核验",
        **request_metadata(request, payload)})


def load_requests(manifest, batch_dir, problems):
    requests = manifest.get("requests", [])
    if isinstance(requests, list):
        items = [r for r in requests if isinstance(r, dict)]
    else:
        items = []
    # A collection may append request records without rewriting its manifest.
    # Only well-known local indexes; never follow arbitrary paths from a manifest.
    configured_index = manifest.get("requests_index")
    names = [configured_index] if configured_index in ("requests.jsonl", "records.jsonl") else []
    names.extend(name for name in ("requests.jsonl", "records.jsonl") if name not in names)
    for name in names:
        path = batch_dir / name
        if not path.is_file() or path.is_symlink():
            continue
        with path.open(encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        items.append(row)
                except json.JSONDecodeError:
                    problems.append({"code": "unreadable_request_record", "message": f"{name} 第 {line_number} 行尚未完整或不是 JSON，已跳过", "item": ""})
    deduped, seen = [], set()
    for row in items:
        key = (row.get("response_file"), row.get("requested_at"), row.get("endpoint"))
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def read_snapshots(raw_dir, assessments_path, study_area, survey_context=None):
    context = {}
    if survey_context and survey_context.is_file():
        candidate = load_json(survey_context)
        if isinstance(candidate, dict) and candidate.get("study_area_id") == study_area and str(candidate.get("coordinate_system", "")).lower() in ("bd09", "bd09ll"):
            context = {"model_bounds": bounds_from(candidate.get("model_bounds")), "collection_bounds": bounds_from(candidate.get("collection_bounds"))}
            context["reference_routes"] = []
            for route in candidate.get("routes", []):
                if not isinstance(route, dict) or route.get("use_for_baseline") is not True or route.get("accepted_surface") is not True:
                    continue
                parts, invalid = parse_path(route.get("points"))
                if not invalid and parts:
                    identifier = clean_text(route.get("id"))
                    context["reference_routes"].append({"id": identifier, "label": ROUTE_LABELS.get(identifier, identifier), "parts": parts,
                        "requested_at": clean_text(route.get("requested_at"), 80), "source": "早前调查规划路线，仅作静态几何参考"})
    assessments = {}
    if assessments_path.is_file():
        for row in load_json(assessments_path).get("records", []):
            if isinstance(row, dict):
                assessments[Path(row.get("probe_directory", "")).name] = row
    snapshots, excluded = [], []
    for manifest_path in sorted(raw_dir.rglob("manifest.json")):
        if manifest_path.is_symlink() or not manifest_path.resolve().is_relative_to(raw_dir.resolve()):
            continue
        directory = manifest_path.parent
        relative = directory.relative_to(raw_dir).as_posix()
        try:
            manifest = load_json(manifest_path)
        except (OSError, ValueError):
            excluded.append({"batch": relative, "reason": "manifest 无法读取"})
            continue
        if not isinstance(manifest, dict):
            excluded.append({"batch": relative, "reason": "manifest 格式无效"})
            continue
        area = manifest.get("study_area", {})
        area_id = manifest.get("study_area_id") or (area.get("id") if isinstance(area, dict) else None)
        assessment = assessments.get(directory.name, {})
        verdict = str(assessment.get("assessment", "")).lower()
        if area_id != study_area:
            excluded.append({"batch": relative, "reason": "非当前上海研究区"})
            continue
        if "rejected" in verdict or "historical" in verdict or assessment.get("use_for_current_shanghai_model") is False:
            excluded.append({"batch": relative, "reason": "质量判定已排除／历史样本"})
            continue
        coordinate = str(manifest.get("coordinate_system", "bd09ll")).lower()
        if coordinate not in ("bd09", "bd09ll"):
            excluded.append({"batch": relative, "reason": "非 BD09 坐标，禁止未经转换混绘"})
            continue
        problems = []
        requests = load_requests(manifest, directory, problems)
        grouped = {}
        for request in requests:
            cycle = request.get("cycle_id", request.get("cycle_number", request.get("cycle", "")))
            group_id = str(cycle) if cycle != "" else ""
            grouped.setdefault(group_id, []).append(request)
        if not grouped:
            grouped[""] = []
        for group_id, requests in grouped.items():
            snapshot = {"id": relative + ("/cycle_" + clean_text(group_id, 80) if group_id else ""), "batch": relative,
                "purpose": clean_text(manifest.get("purpose")), "formal_experiment": manifest.get("formal_experiment") is True,
                "assessment": clean_text(assessment.get("assessment")), "study_area_id": study_area,
                "started_at": clean_text(requests[0].get("requested_at") if requests else manifest.get("started_at"), 80),
                "finished_at": clean_text(requests[-1].get("received_at") if requests else manifest.get("finished_at"), 80),
                "bounds": [], "model_bounds": None, "nodes": [], "reference_nodes": [], "reference_routes": context.get("reference_routes", []), "routes": [], "traffic": [], "evaluations": [], "issues": list(problems),
                "source": SOURCE, "coordinate_system": COORDINATE, "processed_request_count": 0}
            for request in requests:
                params = request.get("params", {})
                bound = bounds_from(params.get("bounds")) if isinstance(params, dict) else None
                if bound and bound not in snapshot["bounds"]:
                    snapshot["bounds"].append(bound)
            if not snapshot["bounds"]:
                bound = bounds_from(manifest.get("probe_bounds") or manifest.get("bounds"))
                if bound:
                    snapshot["bounds"].append(bound)
            if snapshot["purpose"] in ("junction_and_route_survey", "observational_collection"):
                snapshot["model_bounds"] = context.get("model_bounds")
                if not snapshot["bounds"] and context.get("collection_bounds"):
                    snapshot["bounds"].append(context["collection_bounds"])
            for index, request in enumerate(requests, 1):
                endpoint = request.get("endpoint", "")
                is_junction = snapshot["purpose"] == "junction_and_route_survey" and isinstance(endpoint, str) and endpoint.startswith("/geocoding/")
                if not isinstance(endpoint, str) or not (endpoint.startswith("/directionlite/") or endpoint.startswith("/direction/") or endpoint.startswith("/traffic/") or is_junction):
                    continue
                params = request.get("params", {})
                req_coord = request.get("coordinate_system") or (params.get("ret_coordtype") or params.get("coord_type_output") if isinstance(params, dict) else None) or coordinate
                if str(req_coord).lower() not in ("bd09", "bd09ll"):
                    issue(snapshot, "coordinate_system_mismatch", "请求坐标系不是 BD09，已跳过", str(index))
                    continue
                path = response_path(directory, request.get("response_file"))
                if path is None:
                    issue(snapshot, "invalid_response_path", "响应文件路径无效或越出批次目录，已跳过", str(index))
                    continue
                try:
                    payload = load_json(path)
                except (OSError, ValueError):
                    issue(snapshot, "unreadable_response", "响应文件缺失或尚未写完，已跳过", str(index))
                    continue
                if not isinstance(payload, dict) or payload.get("status") not in (0, "0"):
                    issue(snapshot, "api_not_successful", "接口未返回成功业务状态，已跳过", str(index))
                    continue
                snapshot["processed_request_count"] += 1
                if not source_times(payload):
                    issue(snapshot, "missing_source_data_time", "没有独立源数据时间；采集时刻不能替代观测时刻", str(index))
                if is_junction:
                    extract_junction(snapshot, request, payload, index)
                elif endpoint.startswith("/traffic/"):
                    extract_traffic(snapshot, request, payload, index)
                else:
                    extract_route(snapshot, request, payload, index)
            snapshots.append(snapshot)
    snapshots.sort(key=lambda s: (s["started_at"], s["id"]))
    reference_nodes = {}
    for snapshot in snapshots:
        for node in snapshot["nodes"]:
            reference_nodes[node["id"]] = node
        # Static survey reference, not simultaneous traffic observations.
        snapshot["reference_nodes"] = list(reference_nodes.values())
    return snapshots, excluded


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, rows, fields):
    inside_workspace(path)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: csv_value(row.get(k)) for k in fields} for row in rows)


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>上海瑞金片区 · 百度观测回放</title>
<style>
:root{--ink:#102a35;--muted:#5d737d;--accent:#087e8b;--paper:#f2f6f5;--line:#dce6e5;--amber:#a45810}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,-apple-system,"Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:30px}header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}h1{font-size:27px;margin:2px 0 5px;letter-spacing:-.4px}h2{font-size:17px;margin:0 0 14px}p{margin:5px 0}.eyebrow{color:var(--accent);font-size:12px;font-weight:750;letter-spacing:2px}.muted{color:var(--muted);font-size:13px}.badge{background:#deeeeb;border:1px solid #c5e1dc;border-radius:30px;padding:5px 12px;color:#21675d;white-space:nowrap}.timeline,.card{border:1px solid var(--line);background:#fff;border-radius:15px;padding:20px}.timeline{margin:24px 0 18px}.controls{display:flex;align-items:center;gap:14px}button,select{font:inherit;border:1px solid #cedbd9;border-radius:8px;background:white;padding:7px 13px;color:var(--ink)}button{cursor:pointer}button:disabled{opacity:.5;cursor:default}input[type=range]{flex:1;accent-color:var(--accent)}.time-label{font-variant-numeric:tabular-nums}.grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(320px,1fr);gap:18px}.map-card{padding:0;overflow:hidden}.map-head{padding:18px 20px 4px}.map-wrap{position:relative}svg{width:100%;height:520px;display:block;background:#f7faf9}.legend{padding:10px 20px 16px;display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.legend span:before{content:"";display:inline-block;width:24px;border-top:3px solid #087e8b;margin-right:7px;vertical-align:middle}.legend .box:before{border-top:2px dashed #a45810}.legend .start:before{width:8px;height:8px;border:0;border-radius:50%;background:#fff;outline:2px solid #087e8b}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}.stat{border-radius:10px;background:#f2f7f6;padding:13px}.stat strong{display:block;font-size:25px;line-height:1.3}.stat small{font-size:12px;color:var(--muted)}.route{padding:12px 0;border-top:1px solid var(--line)}.route strong{font-size:14px}.route p{font-size:13px}.pill{font-size:11px;border-radius:5px;padding:2px 6px;background:#e5f1ee;color:#21675d;margin-left:7px}.warn{color:var(--amber)}.tables{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr);gap:18px;margin-top:18px}.scroll{overflow:auto;max-height:390px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{position:sticky;top:0;background:#f2f7f6;white-space:nowrap;color:var(--muted)}td.num{font-variant-numeric:tabular-nums;white-space:nowrap}ul{padding-left:19px;margin:7px 0}li{margin-bottom:7px;font-size:13px}.foot{margin:18px 2px;font-size:12px;color:var(--muted)}details{margin-top:12px}summary{cursor:pointer;font-size:13px}.empty{padding:35px;color:var(--muted);text-align:center}.route-list{max-height:325px;overflow:auto}.small{font-size:12px}#map text{font-family:system-ui,"Microsoft YaHei",sans-serif}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #80bec3;outline-offset:3px}@media(max-width:900px){main{padding:18px}.grid,.tables{grid-template-columns:1fr}header{display:block}.badge{display:inline-block;margin-top:10px}svg{height:430px}.controls{flex-wrap:wrap}input[type=range]{min-width:170px}}
</style><style>.legend .model:before{border-top:2px dotted #6866af}.legend .reference:before{border-top:2px solid #adb9bc}</style></head><body><main>
<header><div><div class="eyebrow">SHANGHAI / RUIJIN · OBSERVATION REPLAY</div><h1>真实道路数据，逐批核验</h1><p class="muted">百度接口快照 · BD09LL 坐标 · 本地离线浏览</p><a href="../baseline/index.html" style="font-size:13px;color:#087e8b">查看未标定 SUMO 技术基准 →</a></div><span class="badge">数据层 / 静态快照回放</span></header>
<section class="timeline" aria-label="采集时间轴"><div class="controls"><button id="previous" aria-label="上一批">←</button><button id="play">播放快照</button><button id="next" aria-label="下一批">→</button><input id="slider" type="range" min="0" value="0" aria-label="选择采集批次"><span id="position" class="small"></span></div><p id="time" class="time-label"></p><p id="batch" class="muted"></p><p class="muted">播放每 1.5 秒切换一批已有快照，间隔不代表真实时间流逝。批次内各请求异步采集，以各条时间为准；不插值或生成车辆轨迹。</p></section>
<div class="grid"><section class="card map-card"><div class="map-head"><h2>路线几何与采样范围</h2><p class="muted">箭头沿本批规划顺序；起点空心、终点实心。无底图，路线之间不推断路口连接。</p><label class="small" for="routeSelect">显示路线 </label><select id="routeSelect"><option value="">全部路线</option></select></div><div class="map-wrap"><svg id="map" viewBox="0 0 900 520" role="img" aria-label="真实路线折线及查询矩形"></svg></div><div class="legend"><span>当前批次规划路线</span><span class="reference">早前调查参考几何</span><span class="box">路况查询矩形</span><span class="model">候选模型边界</span><span class="start">出发点 → 终点</span></div><p class="muted" style="padding:0 20px 4px" id="referenceNote"></p><p class="muted" style="padding:0 20px 16px" id="nodeNote"></p></section>
<section class="card"><h2>当前批次</h2><div class="stats"><div class="stat"><strong id="routeCount">0</strong><small>规划路线</small></div><div class="stat"><strong id="speedCount">0</strong><small>数值速度记录</small></div><div class="stat"><strong id="pointCount">0</strong><small>路线坐标点</small></div></div><p id="evaluation"></p><p class="muted">ETA 是平台预计时间。路线均速由距离 / ETA 计算，与拥堵段速度口径不同。</p><div id="routes" class="route-list"></div></section></div>
<div class="tables"><section class="card"><h2>道路与拥堵段速度</h2><p class="muted">空白表示未返回 / 未知；没有拥堵段记录不等于车速为零。高架与地面道路保留原条目。</p><div class="scroll"><table><thead><tr><th>道路</th><th>方向与描述</th><th>速度 km/h</th><th>拥堵长度 m</th><th>采集完成时间</th></tr></thead><tbody id="traffic"></tbody></table></div></section>
<section class="card"><h2>质量检查与适用范围</h2><p class="muted">越出的是查询矩形；其是否等于最终模型边界仍需核验。门址没有被标记为已验证急救入口。</p><ul id="issues"></ul><details><summary>来源时间与排除记录</summary><div id="sourceTimes" class="small"></div><div id="excluded" class="small"></div></details></section></div>
<p class="foot" id="footer"></p><p class="foot">路线来自百度驾车规划，拥堵段速度来自平台估计；这不是逐车实测轨迹或仿真结果。几何距离为在 BD09 点上计算的 haversine 近似值，差异提示用于复核采样简化、断点和距离口径。</p>
</main><script id="replay-data" type="application/json">__DATA__</script>
<script>
"use strict";
const data=JSON.parse(document.getElementById('replay-data').textContent), snapshots=data.snapshots;
const el=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg', colors=['#087e8b','#bf6237','#6866af','#3c875b','#be5790','#8d792b'];
let timer=null,index=Math.max(0,snapshots.length-1);
function text(node,value){node.textContent=value==null?'':String(value)}
function node(tag,attrs={},label){const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(label!=null)text(n,label);return n}
function num(v,d=0){return typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:d}):''}
function clock(v){if(!v)return '';const d=new Date(v);return Number.isNaN(d.valueOf())?v:new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(d)}
function draw(s){const svg=el('map');svg.replaceChildren();const visible=s.routes.filter(r=>!el('routeSelect').value||r.id===el('routeSelect').value),references=data.reference_routes||[];let pts=[...s.routes,...references].flatMap(r=>r.parts.flat());for(const b of s.bounds)pts.push([b.west,b.south],[b.east,b.north]);if(s.model_bounds){const b=s.model_bounds;pts.push([b.west,b.south],[b.east,b.north])}for(const n of s.reference_nodes||[])pts.push(n.point);if(!pts.length){svg.append(node('text',{x:450,y:260,'text-anchor':'middle',fill:'#5d737d'},'本批次没有可绘制的 BD09 几何'));return}
let lo=Infinity,hi=-Infinity,bot=Infinity,top=-Infinity;for(const p of pts){lo=Math.min(lo,p[0]);hi=Math.max(hi,p[0]);bot=Math.min(bot,p[1]);top=Math.max(top,p[1])}const mid=(bot+top)/2,c=Math.cos(mid*Math.PI/180),dx=Math.max((hi-lo)*c,0.00001),dy=Math.max(top-bot,0.00001),scale=Math.min(770/dx,400/dy),cx=(lo+hi)/2,cy=(bot+top)/2;const xy=p=>[450+(p[0]-cx)*c*scale,260-(p[1]-cy)*scale];
for(let i=0;i<6;i++){let x=70+i*152;svg.append(node('line',{x1:x,y1:35,x2:x,y2:475,stroke:'#e7eeeb','stroke-width':1}));let y=55+i*80;svg.append(node('line',{x1:55,y1:y,x2:845,y2:y,stroke:'#e7eeeb','stroke-width':1}))}
const defs=node('defs');colors.forEach((color,i)=>{const m=node('marker',{id:'arrow'+i,viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});m.append(node('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color}));defs.append(m)});svg.append(defs);
for(const r of references)for(const part of r.parts){const line=node('polyline',{points:part.map(p=>xy(p).join(',')).join(' '),fill:'none',stroke:'#adb9bc','stroke-width':2.2,'stroke-linecap':'round','stroke-linejoin':'round',opacity:.85});line.append(node('title',{},r.label+'；调查采集 '+clock(r.requested_at)+'；仅作静态几何参考'));svg.append(line)}
s.bounds.forEach((b,i)=>{const a=xy([b.west,b.north]),z=xy([b.east,b.south]);svg.append(node('rect',{x:a[0],y:a[1],width:z[0]-a[0],height:z[1]-a[1],fill:'#f5bc4920',stroke:'#a45810','stroke-width':1.5,'stroke-dasharray':'7 5'}));svg.append(node('text',{x:a[0]+7,y:a[1]+18,fill:'#86500e','font-size':12},'路况查询矩形 '+(i+1)))});
if(s.model_bounds){const b=s.model_bounds,a=xy([b.west,b.north]),z=xy([b.east,b.south]);svg.append(node('rect',{x:a[0],y:a[1],width:z[0]-a[0],height:z[1]-a[1],fill:'none',stroke:'#6866af','stroke-width':1.8,'stroke-dasharray':'2 5'}));svg.append(node('text',{x:a[0]+7,y:a[1]-7,fill:'#6866af','font-size':12},'候选模型边界'))}
visible.forEach(r=>{const i=s.routes.indexOf(r),color=colors[i%colors.length];r.parts.forEach(part=>{if(!part.length)return;svg.append(node('polyline',{points:part.map(p=>xy(p).join(',')).join(' '),fill:'none',stroke:color,'stroke-width':3,'stroke-linejoin':'round','stroke-linecap':'round',opacity:.88}));let accum=0;for(let j=1;j<part.length;j++){const a=xy(part[j-1]),b=xy(part[j]);accum+=Math.hypot(b[0]-a[0],b[1]-a[1]);if(accum>75){svg.append(node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:color,'stroke-width':2,'marker-end':'url(#arrow'+i%colors.length+')'}));accum=0}}});const p=r.parts.flat();if(p.length){const a=xy(p[0]),b=xy(p[p.length-1]);svg.append(node('circle',{cx:a[0],cy:a[1],r:5,fill:'#fff',stroke:color,'stroke-width':2}));svg.append(node('circle',{cx:b[0],cy:b[1],r:4,fill:color}));if(visible.length===1)svg.append(node('text',{x:a[0]+9,y:a[1]-9,fill:color,'font-size':12},r.label))}});
for(const n of s.reference_nodes||[]){const p=xy(n.point),label=n.label.replace('交叉口','').replace('与',' / '),offset=({RJ_FX:[-142,-12],SN_FX:[7,-10],MM_FX:[-145,4]})[n.id]||[7,16],circle=node('circle',{cx:p[0],cy:p[1],r:4,fill:'#fff',stroke:n.id==='RJ_YJ'?'#be5790':'#35545e','stroke-width':2});circle.append(node('title',{},n.label+'；'+n.note+'；定位采集 '+clock(n.received_at)));svg.append(circle);const t=node('text',{x:p[0]+offset[0],y:p[1]+offset[1],fill:'#35545e','font-size':11},label+(n.id==='RJ_YJ'?' · 外围接驳点':''));t.append(node('title',{},n.id+'：'+n.label+'；'+n.note));svg.append(t)}
svg.append(node('path',{d:'M 848 76 L 848 36 M 841 47 L 848 36 L 855 47',fill:'none',stroke:'#35545e','stroke-width':2}));svg.append(node('text',{x:848,y:27,'text-anchor':'middle',fill:'#35545e','font-size':13},'北 N'));
const metresPerPixel=111195/scale,barMeters=[50,100,200,500,1000,2000,5000,10000].filter(v=>v/metresPerPixel<160).pop()||20,bar=barMeters/metresPerPixel;
svg.append(node('path',{d:`M 65 475 v 6 h ${bar} v -6`,fill:'none',stroke:'#35545e','stroke-width':2}));svg.append(node('text',{x:65,y:468,fill:'#35545e','font-size':12},'约 '+barMeters+' m'));svg.append(node('text',{x:840,y:500,'text-anchor':'end',fill:'#5d737d','font-size':11},'BD09LL · 经度 '+lo.toFixed(5)+'—'+hi.toFixed(5)+' / 纬度 '+bot.toFixed(5)+'—'+top.toFixed(5)))}
function render(){const s=snapshots[index];el('slider').value=index;text(el('position'),snapshots.length?(index+1)+' / '+snapshots.length:'0 / 0');el('previous').disabled=index===0;el('next').disabled=index>=snapshots.length-1;el('play').disabled=snapshots.length<2;if(!s){text(el('time'),'暂无符合条件的上海快照');text(el('batch'),'仅处理当前研究区且未被质量判定排除的批次');draw({routes:[],bounds:[]});return}
const selection=el('routeSelect').value;el('routeSelect').replaceChildren();const all=document.createElement('option');all.value='';text(all,'全部路线');el('routeSelect').append(all);for(const r of s.routes){const option=document.createElement('option');option.value=r.id;text(option,r.label);el('routeSelect').append(option)}if(s.routes.some(r=>r.id===selection))el('routeSelect').value=selection;
text(el('time'),clock(s.started_at)+' — '+clock(s.finished_at)+'（北京时间）');text(el('batch'),s.id+' · '+(s.purpose||'观测采集')+(s.formal_experiment?'':' · 未标记为正式实验'));text(el('routeCount'),s.routes.length);text(el('speedCount'),s.traffic.filter(r=>r.speed_kmh!==null).length);text(el('pointCount'),s.routes.reduce((a,r)=>a+r.point_count,0));text(el('evaluation'),s.evaluations.map(e=>e.description).filter(Boolean).join(' / '));text(el('nodeNote'),s.reference_nodes.length?'路口参考点来自早前定位调查（最近 '+clock(s.reference_nodes.at(-1).received_at)+'），不代表该时刻重新测量。RJ_YJ 为外围道路接驳点，急救入口未验证。':'尚无路口调查参考点。');draw(s);
el('routes').replaceChildren();s.routes.forEach((r,i)=>{const box=document.createElement('div');box.className='route';const title=document.createElement('strong');title.style.color=colors[i%colors.length];text(title,r.label);box.append(title);for(const value of ['平台 '+num(r.platform_distance_m)+' m · ETA '+num(r.platform_eta_s)+' s · 路线均速 '+num(r.eta_implied_speed_kmh,2)+' km/h','几何约 '+num(r.geometry_distance_m,1)+' m · 差异 '+(r.distance_difference_ratio==null?'':num(r.distance_difference_ratio*100,1)+'%'),'采集 '+clock(r.received_at)]){const p=document.createElement('p');text(p,value);box.append(p)}if(r.points_outside_query_bounds){const p=document.createElement('p');p.className='warn';text(p,r.points_outside_query_bounds+' 个点越出采样矩形');box.append(p)}el('routes').append(box)});
el('traffic').replaceChildren();s.traffic.forEach(r=>{const tr=document.createElement('tr');[r.road_name,r.section_desc,num(r.speed_kmh,2),num(r.congestion_distance_m),clock(r.received_at)].forEach((v,i)=>{const td=document.createElement('td');if(i>1)td.className='num';text(td,v);tr.append(td)});el('traffic').append(tr)});
el('issues').replaceChildren();const grouped=new Map();for(const p of s.issues){const key=p.code+' '+p.message;grouped.set(key,{message:p.message,count:(grouped.get(key)?.count||0)+1})}for(const p of grouped.values()){const li=document.createElement('li');text(li,p.message+(p.count>1?'（'+p.count+' 项）':''));el('issues').append(li)}if(!s.issues.length){const li=document.createElement('li');text(li,'本轮规则未发现质量提示；仍需人工核验路网方向、层级和入口。');el('issues').append(li)}
const times=[...new Set([...s.routes,...s.traffic,...s.evaluations].map(r=>r.source_data_time).filter(Boolean))];text(el('sourceTimes'),'响应中的时间字段：'+(times.join('；')||'未知（未返回独立源数据时间）'));
}
function stop(){clearInterval(timer);timer=null;text(el('play'),'播放快照')}
el('slider').max=Math.max(0,snapshots.length-1);el('slider').disabled=!snapshots.length;el('slider').addEventListener('input',()=>{stop();index=Number(el('slider').value);render()});el('routeSelect').onchange=()=>{if(snapshots[index])draw(snapshots[index])};el('previous').onclick=()=>{stop();index=Math.max(0,index-1);render()};el('next').onclick=()=>{stop();index=Math.min(snapshots.length-1,index+1);render()};el('play').onclick=()=>{if(timer){stop();return}if(index>=snapshots.length-1){index=0;render()}text(el('play'),'暂停');timer=setInterval(()=>{if(index>=snapshots.length-1){stop();return}index++;render()},1500)};
const referenceTimes=(data.reference_routes||[]).map(r=>r.requested_at).filter(Boolean).sort();text(el('referenceNote'),referenceTimes.length?'灰色道路参考来自 '+clock(referenceTimes[0])+'—'+clock(referenceTimes.at(-1))+' 的调查；本批路线使用主色，当前路况见列表，历史速度与 ETA 没有前填。':'尚无调查参考几何。');text(el('excluded'),'排除 '+data.excluded.length+' 批：'+data.excluded.map(e=>e.batch+'（'+e.reason+'）').join('；'));text(el('footer'),'生成于 '+clock(data.generated_at)+' · 已处理 '+snapshots.length+' 个快照 · 原始响应保持不变 · HTML 无外网依赖');render();
</script></body></html>'''


def write_outputs(snapshots, excluded, output_dir, processed_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(CHINA_TIME).isoformat(timespec="seconds")
    common = ["snapshot_id", "requested_at", "received_at", "source_data_time", "source", "coordinate_system"]
    route_fields = common + ["id", "label", "route_pair_id", "route_number", "platform_distance_m", "platform_eta_s", "eta_implied_speed_kmh", "geometry_distance_m", "distance_difference_m", "distance_difference_ratio", "point_count", "invalid_point_count", "points_outside_query_bounds", "points_outside_model_bounds", "geometry_sha256"]
    traffic_fields = common + ["id", "road_name", "section_desc", "speed_kmh", "congestion_distance_m", "status", "road_type", "trend", "has_congestion_section"]
    batches = {}
    for snapshot in snapshots:
        batches.setdefault(snapshot["batch"], []).append(snapshot)
    for batch, group in batches.items():
        # Flatten names to keep generated paths independent of untrusted IDs.
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", batch)[:100] or "batch"
        suffix = hashlib.sha256(batch.encode()).hexdigest()[:8]
        target = processed_dir / (name + "_" + suffix)
        inside_workspace(target).mkdir(exist_ok=True)
        routes = [{"snapshot_id": s["id"], **r} for s in group for r in s["routes"]]
        traffic = [{"snapshot_id": s["id"], **r} for s in group for r in s["traffic"]]
        points = [{"snapshot_id": s["id"], "route_id": r["id"], "part": k + 1, "point": j + 1, "longitude_bd09": p[0], "latitude_bd09": p[1], "inside_query_bounds": any(in_bounds(p, b) for b in s["bounds"]) if s["bounds"] else None} for s in group for r in s["routes"] for k, part in enumerate(r["parts"]) for j, p in enumerate(part)]
        issues = [{"snapshot_id": s["id"], **r} for s in group for r in s["issues"]]
        nodes = [{"snapshot_id": s["id"], **r, "longitude_bd09": r["point"][0], "latitude_bd09": r["point"][1]} for s in group for r in s["nodes"]]
        write_csv(target / "routes.csv", routes, route_fields)
        write_csv(target / "traffic.csv", traffic, traffic_fields)
        write_csv(target / "route_points.csv", points, ["snapshot_id", "route_id", "part", "point", "longitude_bd09", "latitude_bd09", "inside_query_bounds"])
        write_csv(target / "quality.csv", issues, ["snapshot_id", "code", "item", "message"])
        write_csv(target / "nodes.csv", nodes, common + ["id", "label", "longitude_bd09", "latitude_bd09", "level", "confidence", "note"])
    references = next((s["reference_routes"] for s in snapshots if s.get("reference_routes")), [])
    document = {"schema_version": 1, "generated_at": generated_at, "coordinate_system": COORDINATE, "source": SOURCE, "mode": "observation_snapshot_replay_not_vehicle_simulation", "reference_routes": references,
                "snapshots": [{k: v for k, v in s.items() if k != "reference_routes"} for s in snapshots], "excluded": excluded}
    inside_workspace(output_dir / "index.html").write_text(PAGE.replace("__DATA__", safe_json(document)), encoding="utf-8")
    summary = {k: v for k, v in document.items() if k not in ("snapshots", "reference_routes")}
    summary["reference_geometry_route_count"] = len(references)
    summary["snapshot_count"] = len(snapshots)
    summary["route_count"] = sum(len(s["routes"]) for s in snapshots)
    summary["traffic_record_count"] = sum(len(s["traffic"]) for s in snapshots)
    summary["numeric_speed_count"] = sum(r["speed_kmh"] is not None for s in snapshots for r in s["traffic"])
    summary["snapshots"] = [{k: s[k] for k in ("id", "batch", "purpose", "formal_experiment", "started_at", "finished_at", "bounds", "model_bounds", "processed_request_count", "issues")} | {"route_count": len(s["routes"]), "node_count": len(s["nodes"]), "traffic_record_count": len(s["traffic"]), "point_count": sum(r["point_count"] for r in s["routes"])} for s in snapshots]
    inside_workspace(output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线检查已保存的上海百度接口响应，导出 CSV 和无需外网的 SVG 快照回放。不读取密钥，不发网络请求。")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/baidu", help="工作区内原始 manifest 目录")
    parser.add_argument("--assessments", type=Path, default=ROOT / "data/processed/baidu/probe_assessments.json", help="样本排除判定 JSON")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/replay", help="HTML 和 summary.json 目录")
    parser.add_argument("--processed-dir", type=Path, default=ROOT / "data/processed/baidu/replay", help="每批次 CSV 目录")
    parser.add_argument("--study-area", default="shanghai_ruijin", help="接收的研究区 ID（默认 shanghai_ruijin）")
    parser.add_argument("--survey-context", type=Path, default=ROOT / "data/processed/baidu/survey_routes.json", help="非敏感调查结果，仅使用其中的采样矩形和候选模型边界")
    args = parser.parse_args(argv)
    try:
        raw = inside_workspace(args.raw_dir)
        assessments = inside_workspace(args.assessments)
        out, processed = inside_workspace(args.output_dir), inside_workspace(args.processed_dir)
        if out.is_relative_to(raw) or processed.is_relative_to(raw):
            raise ValueError("输出目录不能位于原始响应目录中")
        if not raw.is_dir():
            raise ValueError("原始响应目录不存在")
        snapshots, excluded = read_snapshots(raw, assessments, args.study_area, inside_workspace(args.survey_context))
        summary = write_outputs(snapshots, excluded, out, processed)
    except (OSError, ValueError, TypeError):
        # Do not print raw payload contents or arbitrary parser exceptions.
        print("离线处理未完成：请检查工作区路径、manifest 和 JSON 格式。", file=sys.stderr)
        return 1
    print(f"完成：{summary['snapshot_count']} 个快照，{summary['route_count']} 条规划路线，{summary['numeric_speed_count']} 条数值速度；排除 {len(excluded)} 批。")
    print(f"离线页面：{out.relative_to(ROOT) / 'index.html'}")
    print(f"结构化数据：{processed.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
