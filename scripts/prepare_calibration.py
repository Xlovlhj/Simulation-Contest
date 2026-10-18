#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Prepare auditable ETA samples and assess data readiness, without fitting.

Reads only workspace collection manifests, sanitized responses and public project
configuration. No credentials, API calls, dependency installation or simulation.
The default 60-minute calibration / 30-minute validation split is a project
sampling gate, not a competition rule or evidence that a model is accurate.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
CSV_FIELDS = [
    "sample_id", "collection_id", "route_pair_id", "requested_at", "received_at",
    "source_observation_time", "bucket_start_at", "phase", "http_status", "api_status",
    "metadata_api_status", "api_success", "od_matches_current", "estimated_duration_s",
    "platform_distance_m", "point_count", "geometry_signature", "baseline_geometry_signature",
    "whether_matches_baseline", "outside_model_bounds", "selected_in_bucket", "duplicate_of",
    "observation_usable", "baseline_comparison_eligible", "fit_eligible", "validation_eligible",
    "exclusion_reasons", "response_file", "response_sha256",
]


def workspace_path(value: str | Path) -> Path:
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT) or path.name.endswith(".local.json"):
        raise ValueError("All inputs and outputs must be non-private paths inside this workspace")
    return path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object: {path.relative_to(ROOT)}")
    return result


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def time_text(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, LOCAL_TIMEZONE).isoformat() if timestamp is not None else None


def positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def normalized_points(points: list) -> list[tuple[float, float]]:
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Invalid coordinate pair")
        lng, lat = (float(value) for value in point)
        if not math.isfinite(lng) or not math.isfinite(lat) or not -180 <= lng <= 180 or not -90 <= lat <= 90:
            raise ValueError("Invalid longitude or latitude")
        pair = (round(lng, 6), round(lat, 6))
        if not result or result[-1] != pair:
            result.append(pair)
    if len(result) < 2:
        raise ValueError("Route requires at least two nonidentical points")
    return result


def geometry_signature(points: list[tuple[float, float]]) -> str:
    canonical = ";".join(f"{lng:.6f},{lat:.6f}" for lng, lat in points)
    return digest(canonical.encode("ascii"))


def route_points(route: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    raw = []
    steps = route.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Route geometry is missing")
    for step in steps:
        path = step.get("path") if isinstance(step, dict) else None
        if not isinstance(path, str) or not path.strip():
            raise ValueError("A route step has no geometry")
        for token in path.split(";"):
            if token.strip():
                raw.append(tuple(float(part) for part in token.split(",")))
    return normalized_points(raw), raw


def normalized_endpoint(value: object) -> tuple[float, float] | None:
    try:
        coordinates = value.split(",") if isinstance(value, str) else value
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 2:
            return None
        lat, lng = (float(v) for v in coordinates)
        if not math.isfinite(lat) or not math.isfinite(lng):
            return None
        return round(lat, 6), round(lng, 6)
    except (TypeError, ValueError):
        return None


def prepare_reference(config: dict, survey: dict) -> dict:
    if config.get("study_area_id") != "shanghai_ruijin" or survey.get("study_area_id") != config["study_area_id"]:
        raise ValueError("Current Shanghai study area must match both input configurations")
    if config.get("coordinate_system") != "bd09ll" or survey.get("coordinate_system") != "bd09ll":
        raise ValueError("Expected explicit bd09ll coordinates")
    bounds = {key: float(survey["model_bounds"][key]) for key in ("west", "south", "east", "north")}
    if not all(math.isfinite(v) for v in bounds.values()) or not bounds["west"] < bounds["east"] or not bounds["south"] < bounds["north"]:
        raise ValueError("Invalid model bounds")
    pairs = {}
    for pair in config["route_pairs"]:
        identity = pair["id"]
        if identity in pairs:
            raise ValueError("Duplicate fixed OD id")
        origin, destination = normalized_endpoint(pair.get("origin")), normalized_endpoint(pair.get("destination"))
        if origin is None or destination is None:
            raise ValueError("Invalid fixed OD coordinates")
        pairs[identity] = {"origin": origin, "destination": destination, "signature": None, "baseline_selected": False}
    if not pairs:
        raise ValueError("No configured fixed OD pairs")
    for route in survey.get("routes", []):
        if route.get("id") not in pairs:
            continue
        reference = pairs[route["id"]]
        if reference["signature"] is not None:
            raise ValueError("Duplicate survey route id")
        reference["signature"] = geometry_signature(normalized_points(route["points"]))
        reference["baseline_selected"] = (route.get("accepted_surface") is True and route.get("use_for_baseline", True) is True and
                                          normalized_endpoint(route.get("origin")) == reference["origin"] and
                                          normalized_endpoint(route.get("destination")) == reference["destination"])
    return {"study_area_id": config["study_area_id"], "bounds": bounds, "pairs": pairs}


def parse_sample(record: dict, payload: dict | None, reference: dict, sample_id: str,
                 response_error: str | None = None) -> dict:
    identity = record["route_pair_id"]
    baseline = reference["pairs"][identity]
    requested, received = parse_time(record.get("requested_at")), parse_time(record.get("received_at"))
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    od_matches = (normalized_endpoint(params.get("origin")) == baseline["origin"] and
                  normalized_endpoint(params.get("destination")) == baseline["destination"] and
                  record.get("study_area_id") == reference["study_area_id"] and
                  record.get("coordinate_system") == "bd09ll")
    api_status = payload.get("status") if isinstance(payload, dict) else record.get("api_status")
    status_success = api_status in (0, "0") and not isinstance(api_status, bool)
    metadata_status = record.get("api_status")
    metadata_consistent = metadata_status is None or str(metadata_status) == str(api_status)
    api_success = record.get("http_status") == 200 and status_success and metadata_consistent and payload is not None
    row = {key: None for key in CSV_FIELDS}
    row.update(sample_id=sample_id, collection_id=sample_id.split("/")[0], route_pair_id=identity,
               requested_at=record.get("requested_at"), received_at=record.get("received_at"),
               source_observation_time=record.get("source_observation_time"),
               http_status=record.get("http_status"), api_status=api_status, metadata_api_status=metadata_status,
               api_success=api_success, od_matches_current=od_matches,
               baseline_geometry_signature=baseline["signature"], selected_in_bucket=False,
               observation_usable=False, fit_eligible=False, _reasons=[],
               _requested_ts=requested.timestamp() if requested else None,
               _received_ts=received.timestamp() if received else None,
               _baseline_selected=baseline["baseline_selected"])
    if not od_matches:
        row["_reasons"].append("fixed_od_or_coordinate_system_mismatch")
    if requested is None or received is None or received < requested:
        row["_reasons"].append("missing_or_invalid_request_receive_time")
    if not api_success:
        row["_reasons"].append("http_or_api_failure_or_missing_response")
    if not metadata_consistent:
        row["_reasons"].append("api_status_conflicts_with_metadata")
    if response_error:
        row["_reasons"].append(response_error)
    routes = (payload.get("result") or {}).get("routes") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else None
    route = routes[0] if isinstance(routes, list) and routes and isinstance(routes[0], dict) else None
    if route is not None:
        row["estimated_duration_s"] = positive_number(route.get("duration"))
        row["platform_distance_m"] = positive_number(route.get("distance"))
        try:
            points, raw_points = route_points(route)
            row["point_count"] = len(points)
            row["geometry_signature"] = geometry_signature(points)
            row["whether_matches_baseline"] = row["geometry_signature"] == baseline["signature"] if baseline["signature"] else None
            bounds = reference["bounds"]
            row["outside_model_bounds"] = any(not (bounds["west"] <= lng <= bounds["east"] and
                                                      bounds["south"] <= lat <= bounds["north"]) for lng, lat in raw_points)
        except (ValueError, TypeError, KeyError):
            row["_reasons"].append("missing_or_invalid_geometry")
    else:
        row["_reasons"].append("missing_route")
    if row["estimated_duration_s"] is None:
        row["_reasons"].append("missing_or_invalid_duration")
    if row["outside_model_bounds"] is True:
        row["_reasons"].append("outside_model_bounds")
    if row["whether_matches_baseline"] is False:
        row["_reasons"].append("new_path_group_requires_separate_model")
    if not baseline["baseline_selected"] or baseline["signature"] is None:
        row["_reasons"].append("no_selected_baseline_route")
    return row


def read_collections(raw_root: Path, reference: dict) -> tuple[list[dict], list[dict], list[str]]:
    rows, sources, warnings = [], [], []
    for manifest_path in sorted(raw_root.glob("collection_*/manifest.json")):
        folder = workspace_path(manifest_path.parent)
        try:
            manifest_path = workspace_path(manifest_path)
            manifest = load_json(manifest_path)
        except (OSError, ValueError) as error:
            warnings.append(f"Skipped unreadable manifest {manifest_path.relative_to(ROOT)}: {type(error).__name__}")
            continue
        if manifest.get("study_area_id") != reference["study_area_id"] or manifest.get("coordinate_system") != "bd09ll":
            continue
        index = workspace_path(folder / "requests.jsonl")
        if not index.is_file():
            warnings.append(f"No requests index in {folder.name}")
            continue
        snapshot = index.read_bytes()
        sources.append({"manifest": str(manifest_path.relative_to(ROOT)), "manifest_sha256": digest(manifest_path.read_bytes()),
                        "requests_index": str(index.relative_to(ROOT)), "index_snapshot_sha256": digest(snapshot),
                        "index_snapshot_bytes": len(snapshot)})
        for line_number, line in enumerate(snapshot.splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                warnings.append(f"Skipped incomplete or invalid JSONL line {folder.name}:{line_number}; rerun after collection appends it")
                continue
            if not isinstance(record, dict) or record.get("kind") != "direction" or record.get("route_pair_id") not in reference["pairs"]:
                continue
            payload, response_hash, response_path, error = None, None, None, None
            try:
                filename = record.get("response_file")
                if not isinstance(filename, str) or not filename:
                    raise ValueError("Missing response path")
                response_path = workspace_path(folder / filename)
                if not response_path.is_relative_to(folder) or response_path.suffix != ".json":
                    raise ValueError("Response path must remain inside its collection")
                content = response_path.read_bytes()
                response_hash = digest(content)
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError("Invalid response object")
            except (OSError, ValueError):
                payload = None
                error = "response_unavailable_or_invalid"
            row = parse_sample(record, payload, reference, f"{folder.name}/line_{line_number:06d}", error)
            row["response_file"] = str(response_path.relative_to(ROOT)) if response_path and response_path.is_relative_to(folder) else None
            row["response_sha256"] = response_hash
            rows.append(row)
    return rows, sources, warnings


def assess(rows: list[dict], reference: dict, calibration_minutes: int = 60, validation_minutes: int = 30,
           bucket_minutes: int = 5, window_start: str | None = None) -> dict:
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in
           (calibration_minutes, validation_minutes, bucket_minutes)):
        raise ValueError("Window and bucket lengths must be positive integer minutes")
    if calibration_minutes % bucket_minutes or validation_minutes % bucket_minutes:
        raise ValueError("Calibration and validation lengths must be whole numbers of buckets")
    bucket_s = bucket_minutes * 60
    rows.sort(key=lambda row: (row["_requested_ts"] if row["_requested_ts"] is not None else math.inf, row["sample_id"]))
    first_success = {}
    current_times = []
    for row in rows:
        timestamp = row["_requested_ts"]
        row["_bucket_ts"] = math.floor(timestamp / bucket_s) * bucket_s if timestamp is not None else None
        row["bucket_start_at"] = time_text(row["_bucket_ts"])
        if row["od_matches_current"] and timestamp is not None:
            current_times.append(timestamp)
        if row["api_success"] and row["od_matches_current"] and timestamp is not None:
            key = (row["route_pair_id"], row["_bucket_ts"])
            if key in first_success:
                row["duplicate_of"] = first_success[key]
                row["_reasons"].append("duplicate_od_time_bucket")
            else:
                row["selected_in_bucket"] = True
                first_success[key] = row["sample_id"]
        row["observation_usable"] = bool(row["selected_in_bucket"] and row["estimated_duration_s"] is not None and
                                          row["geometry_signature"] and row["outside_model_bounds"] is False and
                                          row["_received_ts"] is not None and row["_received_ts"] >= timestamp)
        row["baseline_comparison_eligible"] = bool(row["observation_usable"] and row["whether_matches_baseline"] is True and row["_baseline_selected"])
    if window_start is not None:
        parsed = parse_time(window_start)
        if parsed is None or parsed.timestamp() % bucket_s:
            raise ValueError("--window-start must include a timezone and align to a bucket boundary")
        start = parsed.timestamp()
    else:
        start = math.floor(min(current_times) / bucket_s) * bucket_s if current_times else None
    calibration_end = start + calibration_minutes * 60 if start is not None else None
    validation_end = calibration_end + validation_minutes * 60 if calibration_end is not None else None
    intervals = {"calibration": (start, calibration_end), "validation": (calibration_end, validation_end)}
    for row in rows:
        timestamp = row["_requested_ts"]
        row["phase"] = "unassigned"
        if start is not None and timestamp is not None:
            row["phase"] = ("before_window" if timestamp < start else "calibration" if timestamp < calibration_end
                            else "validation" if timestamp < validation_end else "later_holdout")
        row["fit_eligible"] = row["baseline_comparison_eligible"] and row["phase"] == "calibration"
        row["validation_eligible"] = row["baseline_comparison_eligible"] and row["phase"] == "validation"
        if row["phase"] in intervals and row["_received_ts"] is not None:
            phase_end = intervals[row["phase"]][1]
            if row["_received_ts"] >= phase_end:
                row["fit_eligible"] = row["validation_eligible"] = False
                row["_reasons"].append("response_crosses_partition_boundary")
        row["exclusion_reasons"] = ";".join(dict.fromkeys(row["_reasons"]))
    current_received = [row["_received_ts"] for row in rows if row["od_matches_current"] and row["_received_ts"] is not None
                        and row["_requested_ts"] is not None and row["_received_ts"] >= row["_requested_ts"]]
    latest = max(current_received) if current_received else None
    per_od = {}
    total_missing = {"calibration": 0, "validation": 0}
    for identity in reference["pairs"]:
        od_rows = [row for row in rows if row["route_pair_id"] == identity]
        usable = [row for row in od_rows if row["observation_usable"]]
        compatible = [row for row in od_rows if row["baseline_comparison_eligible"]]
        times = [row["_requested_ts"] for row in usable]
        groups = {}
        for row in od_rows:
            signature = row["geometry_signature"]
            if signature is None:
                continue
            group = groups.setdefault(signature, {"record_count": 0, "selected_count": 0, "usable_count": 0,
                                                   "matches_baseline": row["whether_matches_baseline"]})
            group["record_count"] += 1
            group["selected_count"] += int(row["selected_in_bucket"])
            group["usable_count"] += int(row["observation_usable"])
        stats = {"record_count": len(od_rows), "selected_success_count": sum(row["selected_in_bucket"] for row in od_rows),
                 "duplicate_count": sum(row["duplicate_of"] is not None for row in od_rows),
                 "valid_sample_count": len(usable), "baseline_compatible_sample_count": len(compatible),
                 "valid_sample_span_minutes": (max(times) - min(times)) / 60 if times else None,
                 "first_valid_request_at": time_text(min(times)) if times else None,
                 "last_valid_request_at": time_text(max(times)) if times else None,
                 "outside_model_bounds_record_count": sum(row["outside_model_bounds"] is True for row in od_rows),
                 "path_group_count": len(groups), "path_groups": groups}
        for phase, minutes in (("calibration", calibration_minutes), ("validation", validation_minutes)):
            required = minutes // bucket_minutes
            eligibility = "fit_eligible" if phase == "calibration" else "validation_eligible"
            phase_rows = [row for row in compatible if row[eligibility]]
            actual = {row["_bucket_ts"] for row in phase_rows}
            begin, end = intervals[phase]
            expected = list(range(int(begin), int(end), bucket_s)) if begin is not None else []
            missing = [timestamp for timestamp in expected if timestamp not in actual]
            missing_count = required - len(actual)
            total_missing[phase] += missing_count
            stats[phase] = {"required_samples": required, "available_samples": len(actual), "missing_samples": missing_count,
                            "missing_bucket_coverage_minutes": missing_count * bucket_minutes,
                            "missing_bucket_starts": [time_text(timestamp) for timestamp in missing],
                            "ready": missing_count == 0}
        per_od[identity] = stats
    window_finished = latest is not None and validation_end is not None and latest >= validation_end
    calibration_finished = latest is not None and calibration_end is not None and latest >= calibration_end
    calibration_ready = calibration_finished and total_missing["calibration"] == 0
    ready = window_finished and sum(total_missing.values()) == 0
    return {
        "schema_version": 1, "ready": ready, "calibration_ready": calibration_ready,
        "status": "data_gate_met_not_model_validated" if ready else "insufficient_compatible_data",
        "model_fitted": False, "model_validated": False, "study_area_id": reference["study_area_id"],
        "coordinate_system": "bd09ll", "threshold_authority": "Project sampling gate; not a competition requirement",
        "thresholds": {"bucket_minutes": bucket_minutes, "calibration_minutes": calibration_minutes,
                       "validation_minutes": validation_minutes, "required_fixed_od_count": len(reference["pairs"]),
                       "required_calibration_samples_per_od": calibration_minutes // bucket_minutes,
                       "required_validation_samples_per_od": validation_minutes // bucket_minutes},
        "windows": {"start_at": time_text(start), "calibration_end_at": time_text(calibration_end),
                    "validation_end_at": time_text(validation_end), "latest_received_at": time_text(latest),
                    "validation_window_elapsed_by_received_data": window_finished,
                    "remaining_until_calibration_end_minutes": max(0, (calibration_end - latest) / 60) if latest is not None and calibration_end is not None else calibration_minutes,
                    "remaining_until_validation_end_minutes": max(0, (validation_end - latest) / 60) if latest is not None and validation_end is not None else calibration_minutes + validation_minutes,
                    "boundary_rule": "[start, calibration_end), [calibration_end, validation_end); responses received on or after their partition end are excluded; later samples remain holdout"},
        "missing_samples": total_missing, "raw_record_count": len(rows),
        "selected_success_count": sum(row["selected_in_bucket"] for row in rows),
        "duplicate_count": sum(row["duplicate_of"] is not None for row in rows), "per_od": per_od,
        "notes": [
            "ETA values are platform estimates, not measured vehicle travel times. Request/receive timestamps are not independent source observation times.",
            "Within each fixed OD and five-minute bucket (or configured bucket), only the earliest API-success response is selected. Later duplicates remain in the CSV and are not independent observations.",
            "A selected success lacking duration or geometry remains unusable; later same-bucket records do not replace it.",
            "Only exact directed geometry matches after six-decimal rounding and removal of consecutive duplicates can be compared to the current baseline path. Changed paths require separate model groups.",
            "Missing/failed/outside-bounds samples are not filled with zero. CSV empty cells represent missing values.",
            "Bucket deduplication does not make temporally correlated platform estimates independent. Validation is held out in time; repeated model selection against it would invalidate that holdout.",
            "Meeting this gate only means enough compatible sampling buckets were collected. It proves neither calibration identifiability nor model accuracy or S1 validation.",
        ]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", default="data/raw/baidu")
    parser.add_argument("--collection-config", default="config/collection.json")
    parser.add_argument("--survey-routes", default="data/processed/baidu/survey_routes.json")
    parser.add_argument("--output-dir", default="data/processed/baidu")
    parser.add_argument("--calibration-minutes", type=int, default=60, help="Project gate, not a competition requirement")
    parser.add_argument("--validation-minutes", type=int, default=30, help="Subsequent held-out period; no fitting is performed")
    parser.add_argument("--bucket-minutes", type=int, default=5)
    parser.add_argument("--window-start", help="Optional ISO timestamp with timezone, aligned to a bucket; otherwise first current-OD request bucket")
    args = parser.parse_args(argv)
    try:
        config_path, survey_path = workspace_path(args.collection_config), workspace_path(args.survey_routes)
        reference = prepare_reference(load_json(config_path), load_json(survey_path))
        rows, sources, warnings = read_collections(workspace_path(args.raw_root), reference)
        report = assess(rows, reference, args.calibration_minutes, args.validation_minutes, args.bucket_minutes, args.window_start)
        report.update(generated_at=datetime.now(LOCAL_TIMEZONE).isoformat(), collection_sources=sources, warnings=warnings,
                      input_hashes={str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in (config_path, survey_path)},
                      script_sha256=digest(Path(__file__).read_bytes()))
        output = workspace_path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        samples = csv_buffer.getvalue().encode("utf-8")
        report["samples_sha256"] = digest(samples)
        (output / "calibration_samples.csv").write_bytes(samples)
        (output / "calibration_readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        missing = report["missing_samples"]
        print(f"数据就绪 ready={str(report['ready']).lower()}；原始请求 {len(rows)}，首次成功桶样本 {report['selected_success_count']}，重复 {report['duplicate_count']}。")
        print(f"项目门槛：标定 {args.calibration_minutes} 分钟 + 后续验证 {args.validation_minutes} 分钟；仍缺标定 {missing['calibration']} 样本、验证 {missing['validation']} 样本。")
        print(f"按最新收到的数据，距验证窗口结束还差 {report['windows']['remaining_until_validation_end_minutes']:.1f} 分钟；未执行拟合或模型验证。")
        print(f"输出：{output.relative_to(ROOT)}/calibration_samples.csv、calibration_readiness.json")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Calibration preparation error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
