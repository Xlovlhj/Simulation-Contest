#!/usr/bin/env python3
"""Small, explicitly invoked survey of Baidu road junctions and driving paths.

AI-assisted implementation: OpenAI Codex, 2026-10-17. This is evidence
collection, not proof of current hospital ambulance access or complete topology.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from baidu_probe import ROOT, HOST, Redactor, NoRedirects, now, signed_query, write_json

PLACES = [
    ("RJ_FX", "瑞金二路与复兴中路交叉口"),
    ("RJ_JG", "瑞金二路与建国中路交叉口"),
    ("RJ_YJ", "瑞金二路与永嘉路交叉口"),
    ("SN_FX", "思南路与复兴中路交叉口"),
    ("SN_JG", "思南路与建国中路交叉口"),
    ("SX_JG", "陕西南路与建国西路交叉口"),
    ("MM_FX", "茂名南路与复兴中路交叉口"),
]
PAIRS = [
    ("north_to_south", "RJ_FX", "RJ_JG", None),
    ("south_to_north", "RJ_JG", "RJ_FX", None),
    ("west_to_east", "SX_JG", "SN_FX", None),
    ("east_to_west", "SN_FX", "SX_JG", None),
    ("west_to_hospital_road", "SX_JG", "RJ_YJ", None),
    ("east_to_hospital_road", "SN_FX", "RJ_YJ", None),
    ("west_to_east_via_maoming", "SX_JG", "SN_FX", "MM_FX"),
    ("east_to_west_via_jianguo", "SN_FX", "SX_JG", "SN_JG"),
]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


class Survey:
    def __init__(self, phase):
        keys = json.loads((ROOT / "config/baidu.local.json").read_text())
        self.ak, self.sk = keys["ak"], keys["sk"]
        self.redactor = Redactor(self.ak, self.sk)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.directory = ROOT / "data/raw/baidu" / ("survey_" + stamp)
        self.directory.mkdir(parents=True)
        self.manifest = {
            "schema_version": 1, "purpose": "junction_and_route_survey", "phase": phase,
            "study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll",
            "started_at": now(), "max_requests": 10, "requests": [],
            "formal_experiment": False, "billing_status": "unverified",
            "source_data_time": None,
        }
        self.persist()

    def persist(self):
        write_json(self.directory / "manifest.json", self.manifest, self.redactor)

    def query(self, endpoint, params, label):
        if endpoint not in ("/geocoding/v3/", "/directionlite/v1/driving"):
            raise ValueError("endpoint_not_allowed")
        if len(self.manifest["requests"]) >= self.manifest["max_requests"]:
            raise ValueError("request_limit")
        if self.manifest["requests"]:
            time.sleep(1.1)
        query, signature = signed_query(endpoint, params + [("ak", self.ak)], self.sk)
        self.redactor.add(signature)
        record = {"endpoint": endpoint, "params": dict(params), "label": label,
                  "requested_at": now(), "http_status": None, "api_status": None,
                  "source_data_time": None}
        payload = None
        try:
            with build_opener(NoRedirects()).open(Request(HOST+query), timeout=15) as response:
                record["http_status"] = response.status
                body = response.read(4*1024*1024+1)
                if len(body) > 4*1024*1024:
                    raise ValueError("size_limit")
                payload = json.loads(body)
                record["api_status"] = payload.get("status") if isinstance(payload, dict) else None
                record["transport_status"] = "received"
        except HTTPError as exc:
            record["http_status"] = exc.code
            record["transport_status"] = "http_error"
            exc.close()
        except (URLError, OSError, ValueError):
            record["transport_status"] = "network_or_format_error"
        record["received_at"] = now()
        if payload is not None:
            filename = f"{len(self.manifest['requests'])+1:02d}_{label}.json"
            write_json(self.directory / filename, payload, self.redactor)
            record["response_file"] = filename
        self.manifest["requests"].append(record)
        self.persist()
        print(json.dumps({k: record.get(k) for k in ("label", "http_status", "api_status", "transport_status")}), flush=True)
        if record["http_status"] != 200 or record["api_status"] not in (0, "0"):
            raise ValueError("request_failed_no_retry")
        return payload, record

    def finish(self):
        self.manifest["finished_at"] = now()
        self.persist()


def geocode():
    survey = Survey("geocode")
    result = {"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll", "nodes": []}
    try:
        for ident, address in PLACES:
            payload, record = survey.query("/geocoding/v3/", [
                ("address", address), ("city", "上海市"), ("output", "json"), ("ret_coordtype", "bd09ll")], ident)
            item = payload["result"]
            point = [float(item["location"]["lng"]), float(item["location"]["lat"])]
            accepted = (121.45 <= point[0] <= 121.49 and 31.195 <= point[1] <= 31.235
                        and item.get("level") in ("交叉路口", "路口", "道路", "门址", "医疗"))
            duplicate = [x["id"] for x in result["nodes"] if distance(point, x["point"]) < 20]
            result["nodes"].append({"id": ident, "label": address, "point": point,
                "geocode_level": item.get("level"), "confidence": item.get("confidence"),
                "accepted_for_route_probe": accepted and not duplicate,
                "near_duplicate_node_ids": duplicate, "topology_verified": False,
                "source_response": str((survey.directory/record["response_file"]).relative_to(ROOT)),
                "requested_at": record["requested_at"]})
        result["generated_at"] = now()
        save(ROOT / "data/processed/baidu/survey_nodes.json", result)
        print(json.dumps({"nodes": len(result["nodes"]), "accepted": sum(n["accepted_for_route_probe"] for n in result["nodes"])}))
    finally:
        survey.finish()


def reprocess_geocodes():
    """Rebuild junction candidates from saved raw evidence, without a request."""
    candidates = []
    for path in (ROOT / "data/raw/baidu").glob("survey_*/manifest.json"):
        manifest = json.loads(path.read_text())
        if manifest.get("phase") == "geocode" and manifest.get("study_area_id") == "shanghai_ruijin":
            candidates.append((path, manifest))
    path, manifest = max(candidates, key=lambda x: str(x[0]))
    labels = dict(PLACES)
    nodes = []
    for record in manifest["requests"]:
        if record.get("api_status") != 0 or "response_file" not in record:
            continue
        source = path.parent / record["response_file"]
        item = json.loads(source.read_text())["result"]
        point = [float(item["location"]["lng"]), float(item["location"]["lat"])]
        accepted = (121.45 <= point[0] <= 121.49 and 31.195 <= point[1] <= 31.235
                    and item.get("level") in ("交叉路口", "路口", "道路", "门址", "医疗"))
        duplicate = [x["id"] for x in nodes if distance(point, x["point"]) < 20]
        ident = record["label"]
        nodes.append({"id": ident, "label": labels[ident], "point": point,
            "geocode_level": item.get("level"), "confidence": item.get("confidence"),
            "accepted_for_route_probe": accepted and not duplicate, "near_duplicate_node_ids": duplicate,
            "topology_verified": False, "source_response": str(source.relative_to(ROOT)),
            "requested_at": record["requested_at"]})
    save(ROOT / "data/processed/baidu/survey_nodes.json", {
        "study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll", "nodes": nodes, "generated_at": now()})
    print(json.dumps({"offline_reprocessed_nodes": len(nodes), "accepted": sum(n["accepted_for_route_probe"] for n in nodes)}))


def routes():
    nodes = json.loads((ROOT / "data/processed/baidu/survey_nodes.json").read_text())["nodes"]
    by_id = {x["id"]: x for x in nodes if x["accepted_for_route_probe"]}
    needed = {n for _, a, b, via in PAIRS for n in (a, b, via) if n}
    if not needed.issubset(by_id):
        print(json.dumps({"status": "nodes_require_review", "missing_node_ids": sorted(needed-set(by_id))}))
        return 1
    survey = Survey("driving")
    bounds = {"south": 31.2105, "north": 31.2230, "west": 121.4660, "east": 121.4800}
    survey.manifest["bounds"] = bounds
    survey.persist()
    result = {"study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll", "routes": [],
              "collection_bounds": bounds, "hospital_endpoint_note": "RJ_YJ is a public-road interchange near the hospital; emergency access is not verified."}
    def coordinate(key):
        lng, lat = by_id[key]["point"]
        return f"{lat:.6f},{lng:.6f}"
    try:
        for ident, origin, dest, via in PAIRS:
            params = [("origin", coordinate(origin)), ("destination", coordinate(dest)),
                      ("coord_type", "bd09ll"), ("ret_coordtype", "bd09ll"),
                      ("tactics", "1"), ("origin_road_type", "0"), ("steps_info", "1")]
            if via:
                params.append(("waypoints", coordinate(via)))
            params.append(("timestamp", str(int(time.time()))))
            payload, record = survey.query("/directionlite/v1/driving", params, ident)
            route = payload["result"]["routes"][0]
            points, types, instructions = [], set(), []
            for step in route["steps"]:
                types.update(str(step.get("road_types", step.get("road_type", ""))).split(","))
                instructions.append(re.sub("<[^>]+>", "", step.get("instruction", "")))
                for token in step["path"].split(";"):
                    if token:
                        point = list(map(float, token.split(",")))
                        if not points or point != points[-1]:
                            points.append(point)
            valid = bool(points) and all(len(p)==2 and 121.43<p[0]<121.51 and 31.18<p[1]<31.25 for p in points)
            elevated = bool(types & {"0", "1"}) or any(any(word in text for word in ("高架", "匝道", "高速", "立交")) for text in instructions)
            result["routes"].append({"id": ident, "origin_id": origin, "destination_id": dest,
                "waypoint_id": via, "origin": by_id[origin]["point"][::-1],
                "destination": by_id[dest]["point"][::-1], "distance_m": route["distance"],
                "duration_s": route["duration"], "points": points, "road_types": sorted(types),
                "instructions": instructions, "accepted_surface": valid and not elevated,
                "use_for_baseline": valid and not elevated and via is None,
                "baseline_selection_note": "Unforced route" if via is None else "Waypoint-forced candidate retained for audit; not used as the baseline or as evidence of policy improvement.",
                "surface_classification_note": "No highway/elevated indication in current route response; independent full topology verification pending.",
                "source_response": str((survey.directory/record["response_file"]).relative_to(ROOT)),
                "requested_at": record["requested_at"],
                "geometry_sha256": hashlib.sha256(json.dumps(points).encode()).hexdigest()})
            save(ROOT / "data/processed/baidu/survey_routes.json", result)
        all_points = [p for r in result["routes"] if r["accepted_surface"] for p in r["points"]]
        if not all_points:
            raise ValueError("no_surface_candidates")
        result["model_bounds"] = {"west": min(p[0] for p in all_points)-0.0001,
            "east": max(p[0] for p in all_points)+0.0001, "south": min(p[1] for p in all_points)-0.0001,
            "north": max(p[1] for p in all_points)+0.0001}
        model = result["model_bounds"]
        collection = {"west": model["west"]-0.00025, "east": model["east"]+0.00025,
                      "south": model["south"]-0.00025, "north": model["north"]+0.00025}
        diagonal = distance((collection["west"], collection["south"]), (collection["east"], collection["north"]))
        if diagonal >= 2000:
            raise ValueError("surface_candidates_exceed_single_collection_rectangle")
        result["collection_bounds"] = collection
        result["collection_diagonal_m"] = diagonal
        result["generated_at"] = now()
        result["full_topology_verified"] = False
        save(ROOT / "data/processed/baidu/survey_routes.json", result)
        print(json.dumps({"routes": len(result["routes"]), "surface_candidates": sum(r["accepted_surface"] for r in result["routes"]), "model_bounds": result["model_bounds"]}))
    finally:
        survey.finish()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--geocode", action="store_true", help="At most 7 signed requests; save junction candidates.")
    group.add_argument("--routes", action="store_true", help="At most 8 signed requests; save observed candidate paths.")
    group.add_argument("--reprocess-geocodes", action="store_true", help="Rebuild junction candidates from saved responses, offline.")
    args = parser.parse_args()
    try:
        if args.geocode:
            geocode()
            return 0
        if args.routes:
            return routes()
        if args.reprocess_geocodes:
            reprocess_geocodes()
            return 0
        parser.print_help()
        return 0
    except Exception:
        # Errors must never disclose signed URLs or credentials.
        print('{"status":"survey_stopped_check_saved_manifest"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
