#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Freeze stopped collection evidence and explicit, disjoint ETA partitions.

Offline, standard library only. No credential/config.local access. The output is
content-addressed and reproducible; all original responses remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import json
import math
from pathlib import Path
import sys

import prepare_calibration as prep

ROOT = Path(__file__).resolve().parents[1]
FIELDS = prep.CSV_FIELDS + ["evaluation_eligible", "phase_slot", "request_seconds_from_window_start"]


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def timestamp(value: str) -> float:
    parsed = prep.parse_time(value)
    if parsed is None:
        raise ValueError("Expected an explicit timezone in every partition time")
    return parsed.timestamp()


def file_record(path: Path) -> dict:
    path = prep.workspace_path(path)
    content = path.read_bytes()
    return {"path": str(path.relative_to(ROOT)), "sha256": prep.digest(content), "bytes": len(content)}


def partition_rows(rows: list[dict], config: dict, od_ids: list[str]) -> dict:
    """Keep every record, select first successes, then enforce path/time gates."""
    width = config["bucket_minutes"] * 60
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("Invalid bucket duration")
    intervals = {key: (timestamp(v["start"]), timestamp(v["end"])) for key, v in config["windows"].items()}
    spans = sorted(intervals.values())
    if any(a >= b for a, b in spans) or any(b > c for (_, b), (c, _) in zip(spans, spans[1:])):
        raise ValueError("Partition windows must be positive and disjoint")
    ids, first_success = set(), {}
    rows.sort(key=lambda r: (r["_requested_ts"] if r["_requested_ts"] is not None else math.inf, r["sample_id"]))
    for row in rows:
        if row["sample_id"] in ids:
            raise ValueError("Duplicate sample identity")
        ids.add(row["sample_id"])
        requested, received = row["_requested_ts"], row["_received_ts"]
        row.update(selected_in_bucket=False, duplicate_of=None, fit_eligible=False,
                   validation_eligible=False, evaluation_eligible=False, phase="outside_windows",
                   phase_slot=None, request_seconds_from_window_start=None)
        reasons = list(row["_reasons"])
        bucket = math.floor(requested / width) * width if requested is not None else None
        row["bucket_start_at"] = prep.time_text(bucket)
        if row["api_success"] and row["od_matches_current"] and requested is not None:
            key = (row["route_pair_id"], bucket)
            if key in first_success:
                row["duplicate_of"] = first_success[key]
                reasons.append("duplicate_od_time_bucket")
            else:
                first_success[key] = row["sample_id"]
                row["selected_in_bucket"] = True
        row["observation_usable"] = bool(row["selected_in_bucket"] and row["estimated_duration_s"] is not None and
            row["geometry_signature"] and row["outside_model_bounds"] is False and
            requested is not None and received is not None and requested <= received)
        row["baseline_comparison_eligible"] = bool(row["observation_usable"] and
            row["whether_matches_baseline"] is True and row["_baseline_selected"])
        for phase, (start, end) in intervals.items():
            if requested is not None and start <= requested < end:
                row["phase"] = phase
                row["request_seconds_from_window_start"] = round(requested - start, 6)
                row["phase_slot"] = math.floor((requested - start) / width)
                if received is None or not requested <= received < end:
                    reasons.append("response_crosses_partition_boundary")
                else:
                    valid = row["baseline_comparison_eligible"]
                    row["fit_eligible"] = valid and phase == "training"
                    row["evaluation_eligible"] = valid and phase != "training"
                    row["validation_eligible"] = row["evaluation_eligible"]
                break
        if row["phase"] == "outside_windows":
            reasons.append("outside_declared_windows")
        row["exclusion_reasons"] = ";".join(sorted(set(reasons)))
    per_phase = {}
    for phase, window in config["windows"].items():
        per_od = {}
        for identity in od_ids:
            candidates = [r for r in rows if r["phase"] == phase and r["route_pair_id"] == identity]
            eligible = [r for r in candidates if r["fit_eligible"] or r["evaluation_eligible"]]
            expected = window["expected_samples_per_od"]
            if expected != round((intervals[phase][1] - intervals[phase][0]) / width):
                raise ValueError("Expected cadence sample count disagrees with partition duration")
            slots = sorted({r["phase_slot"] for r in eligible})
            request_times = [r["_requested_ts"] for r in eligible]
            per_od[identity] = {
                "raw_count": len(candidates), "eligible_count": len(eligible), "expected_count": expected,
                "missing_count": max(0, expected - len(eligible)),
                "observed_cadence_slots": slots, "missing_cadence_slots": sorted(set(range(expected)) - set(slots)),
                "duplicate_count": sum(bool(r["duplicate_of"]) for r in candidates),
                "failure_count": sum(not r["api_success"] for r in candidates),
                "changed_path_count": sum(r["whether_matches_baseline"] is False for r in candidates),
                "outside_bounds_count": sum(r["outside_model_bounds"] is True for r in candidates),
                "path_group_counts": dict(sorted(Counter(r["geometry_signature"] for r in candidates if r["geometry_signature"]).items())),
                "first_request_at": prep.time_text(min(request_times)) if request_times else None,
                "last_request_at": prep.time_text(max(request_times)) if request_times else None,
                "request_span_minutes": (max(request_times) - min(request_times)) / 60 if request_times else None,
                "ready": len(eligible) == expected and len(slots) == expected,
            }
        per_phase[phase] = {"window": window, "per_od": per_od,
            "eligible_count": sum(v["eligible_count"] for v in per_od.values()),
            "ready": all(v["ready"] for v in per_od.values())}
    return {"raw_direction_count": len(rows), "selected_direction_count": sum(r["selected_in_bucket"] for r in rows),
            "duplicate_count": sum(bool(r["duplicate_of"]) for r in rows),
            "failure_count": sum(not r["api_success"] for r in rows),
            "changed_path_count": sum(r["whether_matches_baseline"] is False for r in rows),
            "outside_bounds_count": sum(r["outside_model_bounds"] is True for r in rows),
            "missing_duration_count": sum(r["estimated_duration_s"] is None for r in rows),
            "source_observation_time_present_count": sum(bool(r["source_observation_time"]) for r in rows),
            "phases": per_phase}


def freeze(config_path: Path) -> dict:
    config = prep.load_json(config_path)
    collection_paths = [prep.workspace_path(p) for p in config["collections"]]
    if len(collection_paths) != len(set(collection_paths)) or not collection_paths:
        raise ValueError("Explicit collection list must be nonempty and unique")
    survey_path = prep.workspace_path(config["survey_routes"])
    reference_config = prep.load_json(collection_paths[0] / "manifest.json")["config"]
    reference = prep.prepare_reference(reference_config, prep.load_json(survey_path))
    rows, files, collections, request_audit = [], [], [], []
    for folder in collection_paths:
        manifest = prep.load_json(folder / "manifest.json")
        summary = prep.load_json(folder / "run_summary.json")
        if summary.get("status") != "stopped":
            raise ValueError(f"Collection has not stopped: {folder.name}")
        current = prep.prepare_reference(manifest["config"], prep.load_json(survey_path))
        if current != reference:
            raise ValueError("Collection fixed-OD configuration changed")
        for filename in ("manifest.json", "requests.jsonl", "run_summary.json"):
            files.append(file_record(folder / filename))
        counts, successes, times = Counter(), Counter(), []
        for lineno, line in enumerate((folder / "requests.jsonl").read_bytes().splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            kind = record["kind"]
            counts[kind] += 1
            payload, response_path, source_hash, error = None, None, None, None
            try:
                response_path = prep.workspace_path(folder / record["response_file"])
                if not response_path.is_relative_to(folder) or response_path.suffix != ".json":
                    raise ValueError("Response must belong to its collection")
                file = file_record(response_path)
                files.append(file)
                source_hash = file["sha256"]
                payload = prep.load_json(response_path)
            except (OSError, ValueError, KeyError):
                error = "response_unavailable_or_invalid"
            success = bool(payload is not None and record.get("http_status") == 200 and
                payload.get("status") in (0, "0") and not isinstance(payload.get("status"), bool) and
                str(record.get("api_status")) == str(payload.get("status")))
            successes[kind] += success
            requested = prep.parse_time(record.get("requested_at"))
            if requested:
                times.append(requested.timestamp())
            sid = f"{folder.name}/line_{lineno:06d}"
            request_audit.append({"sample_id": sid, "kind": kind, "route_pair_id": record.get("route_pair_id"),
                "requested_at": record.get("requested_at"), "received_at": record.get("received_at"),
                "http_status": record.get("http_status"), "api_status": payload.get("status") if payload else None,
                "api_success": success, "response_file": str(response_path.relative_to(ROOT)) if response_path else None,
                "response_sha256": source_hash, "source_observation_time": record.get("source_observation_time")})
            if kind == "direction" and record.get("route_pair_id") in reference["pairs"]:
                row = prep.parse_sample(record, payload, reference, sid, error)
                row.update(response_file=request_audit[-1]["response_file"], response_sha256=source_hash)
                rows.append(row)
        if dict(counts) != summary.get("session_counts") or dict(successes) != summary.get("success_counts"):
            raise ValueError("Raw request counts disagree with stopped collection summary")
        collections.append({"directory": str(folder.relative_to(ROOT)), "started_at": summary.get("started_at"),
            "finished_at": summary.get("finished_at"), "stop_reason": summary.get("stop_reason"),
            "counts": dict(counts), "successes": dict(successes),
            "first_request_at": prep.time_text(min(times)) if times else None,
            "last_request_at": prep.time_text(max(times)) if times else None})
    audit = partition_rows(rows, config, sorted(reference["pairs"]))
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    samples = out.getvalue().encode()
    source_files = sorted({entry["path"]: entry for entry in files}.values(), key=lambda item: item["path"])
    protocol = {k: config[k] for k in ("protocol_id", "study_area_id", "coordinate_system", "bucket_minutes", "windows", "protocol_notes")}
    sources = source_files + [file_record(survey_path), file_record(Path(prep.__file__)), file_record(Path(__file__))]
    snapshot_id = "DATA_" + prep.digest(canonical({"protocol": protocol, "sources": sources, "samples_sha256": prep.digest(samples)}))[:16]
    report = {"schema_version": 1, "snapshot_id": snapshot_id, "protocol": protocol,
        "samples_sha256": prep.digest(samples), "source_files": sources, "collections": collections,
        "od_ids": sorted(reference["pairs"]), "baseline_geometry_signatures": {k: v["signature"] for k, v in reference["pairs"].items()},
        "model_bounds": reference["bounds"], "data_audit": audit,
        "source_files_hash_checked_at_freeze": True,
        "source_clock": "Request/receive timestamps only; source observation timestamps are absent unless individually present.",
        "repeated_observation_note": "Successive platform estimates are serially dependent, not independent vehicle observations.",
        "time_mapping": {"data_time": "Actual +08:00 request and receive clock; eligibility uses request plus receipt before window end.",
            "simulation_time": "SUMO scenario seconds are a separate synthetic clock. No observed incident or vehicle is reconstructed.",
            "conditional_display_only": "t_display = declared_real_anchor + t_sim is a visualization convention, not an observed trajectory alignment."}}
    target = prep.workspace_path(config["output_data"])
    if not target.is_relative_to(ROOT / "data/processed/final"):
        raise ValueError("Final data outputs must stay under data/processed/final")
    target.mkdir(parents=True, exist_ok=True)
    artifacts = {"samples.csv": samples, "data_manifest.json": json_bytes(report), "requests_audit.json": json_bytes(request_audit)}
    for name, content in artifacts.items():
        path = target / name
        if path.exists() and path.read_bytes() != content:
            raise ValueError(f"Frozen artifact differs; retain it and choose a new output directory: {path.relative_to(ROOT)}")
    for name, content in artifacts.items():
        (target / name).write_bytes(content)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/final_validation.json")
    parser.add_argument("--freeze", action="store_true", help="Audit stopped sessions and write a deterministic snapshot")
    args = parser.parse_args(argv)
    if not args.freeze:
        parser.print_help()
        return 0
    try:
        report = freeze(prep.workspace_path(args.config))
        print(json.dumps({"status": "frozen", "snapshot_id": report["snapshot_id"],
            "raw_direction_count": report["data_audit"]["raw_direction_count"],
            "duplicates": report["data_audit"]["duplicate_count"],
            "phases": {k: {"n": v["eligible_count"], "ready": v["ready"]} for k, v in report["data_audit"]["phases"].items()}}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
