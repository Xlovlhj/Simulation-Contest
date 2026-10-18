#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Model snapshot/release date was not
# provided; route interpretation and research claims require team review.
"""Collect four bounded hospital-route checks, or rebuild their saved evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from survey_shanghai import Survey, ROOT, save
from prepare_calibration import normalized_points, geometry_signature

CASES = [
    ("west_reference", "west_to_hospital_road", "SX_JG", "RJ_YJ", None),
    ("west_via_sinnan", "west_to_hospital_road", "SX_JG", "RJ_YJ", "SN_JG"),
    ("east_reference", "east_to_hospital_road", "SN_FX", "RJ_YJ", None),
    ("east_via_maoming", "east_to_hospital_road", "SN_FX", "RJ_YJ", "MM_FX"),
]


def reprocess(folder: Path) -> Path:
    folder = folder.resolve()
    if not folder.is_relative_to(ROOT / "data/raw/baidu"):
        raise ValueError("Raw evidence must be inside workspace Baidu data")
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("phase") != "hospital_alternatives":
        raise ValueError("Incorrect survey phase")
    source = ROOT / "data/processed/baidu/survey_routes.json"
    original = json.loads(source.read_text())
    bounds = original["model_bounds"]
    by_case = {case[0]: case for case in CASES}
    results = []
    for record in manifest["requests"]:
        if record.get("api_status") not in (0, "0") or not record.get("response_file"):
            continue
        label, od, origin, destination, waypoint = by_case[record["label"]]
        response_path = (folder / record["response_file"]).resolve()
        if not response_path.is_relative_to(folder):
            raise ValueError("Invalid response path")
        payload = json.loads(response_path.read_text())
        for index, route in enumerate(payload.get("result", {}).get("routes", [])):
            points, types, instructions, turns = [], set(), [], []
            for step in route.get("steps", []):
                types.update(str(step.get("road_types", step.get("road_type", ""))).split(","))
                instructions.append(re.sub("<[^>]+>", "", step.get("instruction", "")))
                turns.append(step.get("turn"))
                for token in step.get("path", "").split(";"):
                    if token.strip():
                        point = list(map(float, token.split(",")))
                        if not points or point != points[-1]:
                            points.append(point)
            canonical = normalized_points(points)
            directed = list(zip(canonical, canonical[1:]))
            repeated_edges = len(directed) - len(set(directed))
            repeated_nodes = len(canonical) - len(set(canonical))
            inside = all(bounds["west"] <= p[0] <= bounds["east"] and bounds["south"] <= p[1] <= bounds["north"] for p in points)
            elevated = bool(types & {"0", "1", "8", "9"}) or any(
                any(word in text for word in ("高速", "高架", "匝道", "立交", "轮渡")) for text in instructions)
            uturn = 5 in turns or any("调头" in text or "掉头" in text for text in instructions)
            restriction = (route.get("restriction_info") or {}).get("status")
            reasons = []
            if not inside: reasons.append("outside_existing_model_bounds")
            if elevated: reasons.append("non_surface_or_non_driving_indication")
            if restriction in (2, "2"): reasons.append("illegal_restriction_status")
            if uturn: reasons.append("uturn_present")
            if repeated_edges or repeated_nodes: reasons.append("repeated_geometry_loop")
            results.append({
                "id": f"{label}_{index}", "route_index": index, "od_id": od, "origin_id": origin, "destination_id": destination,
                "waypoint_id": waypoint, "params": record["params"],
                "origin": list(map(float, record["params"]["origin"].split(","))),
                "destination": list(map(float, record["params"]["destination"].split(","))),
                "distance_m": route.get("distance"), "duration_s": route.get("duration"),
                "points": points, "geometry_sha256": geometry_signature(canonical),
                "road_types": sorted(types), "instructions": instructions, "turn_codes": turns,
                "within_existing_model_bounds": inside, "repeated_directed_edges": repeated_edges,
                "repeated_nodes": repeated_nodes, "uturn_present": uturn, "restriction_status": restriction,
                "accepted_surface": inside and not elevated and restriction not in (2, "2"),
                "simple_route_candidate": not reasons, "screening_reasons": reasons,
                "use_for_baseline": False, "new_demand": False,
                "requested_at": record["requested_at"], "received_at": record["received_at"],
                "source_observation_time": None,
                "source_response": str(response_path.relative_to(ROOT)),
                "source_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
            })
    output = ROOT / "data/processed/baidu" / f"route_candidates_{folder.name}.json"
    save(output, {
        "study_area_id": "shanghai_ruijin", "coordinate_system": "bd09ll", "routes": results,
        "model_bounds": bounds, "collection_bounds": original["collection_bounds"],
        "source_manifest": str(manifest_path.relative_to(ROOT)),
        "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "base_survey_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "note": "Candidates add no demand. Query ETA is a platform estimate at its own timestamp, not a policy benefit or a measured vehicle trip.",
    })
    print(json.dumps({"output": str(output.relative_to(ROOT)), "routes": len(results),
                      "simple_candidates": sum(r["simple_route_candidate"] for r in results)}, ensure_ascii=False))
    return output


def query() -> None:
    nodes = json.loads((ROOT / "data/processed/baidu/survey_nodes.json").read_text())["nodes"]
    by_id = {node["id"]: node for node in nodes if node["accepted_for_route_probe"]}
    survey = Survey("hospital_alternatives")
    survey.manifest.update(max_requests=4, purpose="bounded_hospital_alternative_evidence",
                           budget_scope="Separate four-request survey; not charged to continuous collector ledger",
                           origin_heading_known=False, no_purchase=True)
    survey.persist()
    def coordinate(identity):
        lng, lat = by_id[identity]["point"]
        return f"{lat:.6f},{lng:.6f}"
    try:
        for label, _, origin, destination, via in CASES:
            params = [("origin", coordinate(origin)), ("destination", coordinate(destination)),
                      ("coord_type", "bd09ll"), ("ret_coordtype", "bd09ll"),
                      ("tactics", "1"), ("origin_road_type", "0"), ("steps_info", "1")]
            if via:
                params.append(("waypoints", coordinate(via)))
            params.append(("timestamp", str(int(time.time()))))
            survey.query("/directionlite/v1/driving", params, label)
    finally:
        survey.finish()
    reprocess(survey.directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--query", action="store_true", help="Exactly four maximum signed requests; stops on the first error")
    group.add_argument("--reprocess", help="Workspace raw survey directory; offline only")
    args = parser.parse_args(argv)
    if not args.query and not args.reprocess:
        parser.print_help()
        return 0
    try:
        if args.query:
            query()
        else:
            path = Path(args.reprocess)
            reprocess(path if path.is_absolute() else ROOT / path)
        return 0
    except Exception:
        print('{"status":"alternative_survey_stopped_check_saved_evidence"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
