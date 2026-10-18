#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-17. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Freeze an OD-median ETA reference and separately evaluate its time holdout.

No network or credentials. No action defaults to help. Data gates come from
prepare_calibration.py and are project thresholds, not competition requirements.
This predicts platform ETA estimates, not observed vehicle travel times.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
METHOD = "frozen_per_od_training_median"
WINDOW_KEYS = ("start_at", "calibration_end_at", "validation_end_at")


class InsufficientData(Exception):
    def __init__(self, details: dict):
        self.details = details
        super().__init__("The requested data partition is not ready")


def workspace_path(value: str | Path) -> Path:
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT) or path.name.endswith(".local.json"):
        raise ValueError("Inputs and outputs must remain in non-private workspace paths")
    return path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def timestamp(value: str) -> float:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Sample and window times must include a timezone")
    return result.timestamp()


def flag(value: object) -> bool:
    if value is True or value == "True" or value == "true":
        return True
    if value is False or value in ("False", "false", "", None):
        return False
    raise ValueError("Invalid sample eligibility flag")


def context(report: dict) -> dict:
    times = [timestamp(report["windows"][key]) for key in WINDOW_KEYS]
    if not times[0] < times[1] < times[2]:
        raise ValueError("Invalid calibration / validation windows")
    thresholds = report["thresholds"]
    for key in ("bucket_minutes", "calibration_minutes", "validation_minutes", "required_fixed_od_count"):
        if type(thresholds.get(key)) is not int or thresholds[key] <= 0:
            raise ValueError("Invalid project sampling thresholds")
    if thresholds["required_fixed_od_count"] != len(report["per_od"]):
        raise ValueError("Fixed OD count differs from sampling thresholds")
    for phase, duration in (("calibration", times[1] - times[0]), ("validation", times[2] - times[1])):
        minutes = thresholds[phase + "_minutes"]
        if duration != minutes * 60 or minutes % thresholds["bucket_minutes"]:
            raise ValueError("Partition duration differs from sampling thresholds")
        expected = minutes // thresholds["bucket_minutes"]
        if thresholds["required_" + phase + "_samples_per_od"] != expected or any(
                stats[phase]["required_samples"] != expected for stats in report["per_od"].values()):
            raise ValueError("Required sample counts differ from partition duration")
    return {"study_area_id": report["study_area_id"], "coordinate_system": report["coordinate_system"],
            "thresholds": report["thresholds"], "windows": {key: report["windows"][key] for key in WINDOW_KEYS},
            "od_ids": sorted(report["per_od"])}


def require_gate(report: dict, phase: str) -> None:
    top_key = "calibration_ready" if phase == "calibration" else "ready"
    missing = {}
    for identity, stats in report["per_od"].items():
        part = stats[phase]
        missing[identity] = {"required": part["required_samples"], "available": part["available_samples"],
                             "missing": part["missing_samples"], "ready": part["ready"] is True}
    if not missing or report.get(top_key) is not True or any(not item["ready"] or item["missing"] != 0 for item in missing.values()):
        remaining_key = "remaining_until_calibration_end_minutes" if phase == "calibration" else "remaining_until_validation_end_minutes"
        raise InsufficientData({"status": "insufficient_data", "action": "fit" if phase == "calibration" else "evaluate",
                                "partition": phase, "missing_by_od": missing,
                                "remaining_window_minutes_by_latest_data": report.get("windows", {}).get(remaining_key),
                                "fit_performed": False, "evaluation_performed": False,
                                "note": "Project sampling gate only; no model accuracy claim."})


def select_samples(rows: list[dict], report: dict, phase: str) -> list[dict]:
    """Recheck eligibility and temporal separation instead of trusting flags alone."""
    current = context(report)
    start, middle, end = (timestamp(current["windows"][key]) for key in WINDOW_KEYS)
    begin, finish = (start, middle) if phase == "calibration" else (middle, end)
    key = "fit_eligible" if phase == "calibration" else "validation_eligible"
    opposite = "validation_eligible" if phase == "calibration" else "fit_eligible"
    ids, buckets, selected = set(), set(), []
    bucket_seconds = report["thresholds"]["bucket_minutes"] * 60
    for row in rows:
        sample_id = row["sample_id"]
        if not sample_id or sample_id in ids:
            raise ValueError("Duplicate or empty sample ID in the input snapshot")
        ids.add(sample_id)
        if not flag(row.get(key)):
            continue
        identity = row["route_pair_id"]
        if identity not in current["od_ids"] or row["phase"] != phase or flag(row.get(opposite)):
            raise ValueError("Sample flags leak across partitions or reference an unknown OD")
        if not all(flag(row.get(field)) for field in ("api_success", "od_matches_current", "selected_in_bucket",
                                                     "baseline_comparison_eligible", "whether_matches_baseline")):
            raise ValueError("An eligible sample failed source, selection or geometry checks")
        if row.get("duplicate_of") or row.get("outside_model_bounds") not in (False, "False", "false"):
            raise ValueError("Duplicate or outside-bounds sample marked eligible")
        if not row.get("geometry_signature") or row["geometry_signature"] != row.get("baseline_geometry_signature"):
            raise ValueError("An eligible sample has changed or missing path geometry")
        requested, received, bucket = (timestamp(row[field]) for field in ("requested_at", "received_at", "bucket_start_at"))
        if not begin <= requested <= received < finish:
            raise ValueError("Sample request/receive time crosses its partition boundary")
        if bucket != math.floor(requested / bucket_seconds) * bucket_seconds:
            raise ValueError("Sample bucket does not match its request time")
        unique_bucket = (identity, bucket)
        if unique_bucket in buckets:
            raise ValueError("Multiple eligible samples occupy the same OD/time bucket")
        buckets.add(unique_bucket)
        duration = float(row["estimated_duration_s"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Missing or invalid platform duration in an eligible sample")
        selected.append({"sample_id": sample_id, "route_pair_id": identity, "estimated_duration_s": duration,
                         "requested_at": row["requested_at"], "received_at": row["received_at"],
                         "bucket_start_at": row["bucket_start_at"], "geometry_signature": row["geometry_signature"]})
    for identity in current["od_ids"]:
        actual = sum(sample["route_pair_id"] == identity for sample in selected)
        expected = report["per_od"][identity][phase]
        if actual != expected["available_samples"] or actual != expected["required_samples"]:
            raise ValueError(f"Readiness counts disagree with eligible {phase} samples for {identity}")
    return sorted(selected, key=lambda sample: sample["sample_id"])


def fit_reference(rows: list[dict], report: dict, provenance: dict, model_id: str) -> dict:
    require_gate(report, "calibration")
    samples = select_samples(rows, report, "calibration")
    predictions = {}
    for identity in sorted(report["per_od"]):
        part = [sample for sample in samples if sample["route_pair_id"] == identity]
        signatures = {sample["geometry_signature"] for sample in part}
        if len(signatures) != 1:
            raise ValueError("A fixed-OD reference cannot mix different path groups")
        predictions[identity] = {"predicted_duration_s": statistics.median(sample["estimated_duration_s"] for sample in part),
                                 "training_sample_count": len(part), "geometry_signature": signatures.pop()}
    return {"schema_version": 1, "model_id": model_id, "method": METHOD,
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "eta_reference_frozen",
            "target": "Baidu platform estimated route duration in seconds, not measured vehicle travel time",
            "context": context(report), "input_provenance": provenance,
            "training_samples": samples, "training_sample_ids": [sample["sample_id"] for sample in samples],
            "training_samples_sha256": sha(canonical(samples)), "predictions_by_od": predictions,
            "validation_used_for_fit": False, "traffic_model_calibrated": False, "traffic_model_validated": False,
            "note": "A simple prediction reference for subsequent models to compare against; no accuracy threshold or validation success is asserted."}


def evaluate_reference(model: dict, rows: list[dict], report: dict, provenance: dict) -> dict:
    if model.get("schema_version") != 1 or model.get("method") != METHOD or model.get("validation_used_for_fit") is not False:
        raise ValueError("Unsupported or non-frozen ETA reference")
    require_gate(report, "validation")
    if model["context"] != context(report):
        raise ValueError("Evaluation windows, study area, OD set or sampling thresholds differ from the frozen model")
    # Updated CSV snapshots may append data; they must not silently rewrite the
    # training set on which the frozen reference was based.
    current_training = select_samples(rows, report, "calibration")
    if sha(canonical(current_training)) != model["training_samples_sha256"]:
        raise ValueError("Training samples changed after the reference was frozen")
    samples = select_samples(rows, report, "validation")
    if {sample["sample_id"] for sample in samples} & set(model["training_sample_ids"]):
        raise ValueError("Validation samples overlap frozen training IDs")
    errors, per_od, results = [], {}, []
    for sample in samples:
        identity = sample["route_pair_id"]
        prediction = model["predictions_by_od"][identity]
        if sample["geometry_signature"] != prediction["geometry_signature"]:
            raise ValueError("Validation path differs from its frozen training path")
        estimated = float(prediction["predicted_duration_s"])
        if not math.isfinite(estimated) or estimated <= 0:
            raise ValueError("Invalid frozen prediction")
        error = abs(estimated - sample["estimated_duration_s"])
        errors.append(error)
        part = per_od.setdefault(identity, {"sample_count": 0, "absolute_error_sum_s": 0.0, "predicted_duration_s": estimated})
        part["sample_count"] += 1
        part["absolute_error_sum_s"] += error
        results.append({**sample, "predicted_duration_s": estimated, "absolute_error_s": error})
    for part in per_od.values():
        part["mae_s"] = part["absolute_error_sum_s"] / part["sample_count"]
    return {"schema_version": 1, "status": "eta_reference_evaluated", "model_id": model["model_id"], "method": METHOD,
            "created_at": datetime.now(timezone.utc).isoformat(), "input_provenance": provenance,
            "context": model["context"], "per_od": per_od,
            "overall": {"sample_count": len(errors), "mae_s": sum(errors) / len(errors),
                        "aggregation": "Sample-weighted arithmetic mean of absolute prediction errors, in seconds"},
            "validation_sample_ids": [sample["sample_id"] for sample in samples], "sample_results": results,
            "model_refitted": False, "traffic_model_validated": False,
            "note": "Evaluation of a frozen platform-ETA prediction reference only; this is not proof of real-world traffic-model validation. Repeated model selection on this holdout would compromise its role."}


def read_snapshot(samples_path: Path, readiness_path: Path) -> tuple[list[dict], dict, dict]:
    report_bytes, samples_bytes = readiness_path.read_bytes(), samples_path.read_bytes()
    report = json.loads(report_bytes)
    if report.get("schema_version") != 1 or report.get("samples_sha256") != sha(samples_bytes):
        raise ValueError("CSV and readiness report are not a matching snapshot; rerun preparation and retry")
    rows = list(csv.DictReader(io.StringIO(samples_bytes.decode("utf-8"))))
    provenance = {"samples_path": str(samples_path.relative_to(ROOT)), "samples_sha256": sha(samples_bytes),
                  "readiness_path": str(readiness_path.relative_to(ROOT)), "readiness_sha256": sha(report_bytes),
                  "readiness_generated_at": report.get("generated_at"), "readiness_script_sha256": report.get("script_sha256")}
    return rows, report, provenance


def load_model(path: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    manifest_path = workspace_path(path.parent / "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_name") != path.name or manifest.get("artifact_sha256") != sha(data):
        raise ValueError("Frozen model does not match its saved manifest hash")
    return json.loads(data), sha(data)


def save_artifact(folder: Path, name: str, value: dict, action: str) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    artifact = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    (folder / name).write_bytes(artifact)
    manifest = {"action": action, "artifact_name": name, "artifact_sha256": sha(artifact),
                "script_sha256": sha(Path(__file__).read_bytes()), "saved_at": datetime.now(timezone.utc).isoformat()}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--fit", action="store_true", help="Freeze one median prediction per OD after calibration readiness")
    action.add_argument("--evaluate", action="store_true", help="Evaluate a frozen --model only after validation readiness")
    parser.add_argument("--samples", default="data/processed/baidu/calibration_samples.csv")
    parser.add_argument("--readiness", default="data/processed/baidu/calibration_readiness.json")
    parser.add_argument("--model", help="Frozen model.json path; required for --evaluate")
    parser.add_argument("--output-root", default="outputs/eta_reference")
    parser.add_argument("--run-id", help="Optional unique model/evaluation ID; existing outputs are never overwritten")
    args = parser.parse_args(argv)
    if not args.fit and not args.evaluate:
        parser.print_help()
        return 0
    try:
        if args.evaluate and not args.model:
            raise ValueError("--evaluate requires --model <frozen-model.json>")
        if args.fit and args.model:
            raise ValueError("--fit creates a new model; --model is only for evaluation")
        rows, report, provenance = read_snapshot(workspace_path(args.samples), workspace_path(args.readiness))
        prefix = "eta_model" if args.fit else "eta_evaluation"
        run_id = args.run_id or datetime.now(timezone.utc).strftime(prefix + "_%Y%m%dT%H%M%S_%fZ")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", run_id):
            raise ValueError("Invalid run ID")
        output = workspace_path(args.output_root)
        if args.fit:
            model = fit_reference(rows, report, provenance, run_id)
            folder = workspace_path(output / "models" / run_id)
            save_artifact(folder, "model.json", model, "fit")
            print(f"已冻结 ETA 中位数参照：{folder.relative_to(ROOT)}/model.json")
            print(f"训练 {len(model['training_sample_ids'])} 样本；验证未参与拟合。此结果不代表交通模型已标定。")
        else:
            model_path = workspace_path(args.model)
            model, model_hash = load_model(model_path)
            result = evaluate_reference(model, rows, report, provenance)
            result.update(evaluation_id=run_id, frozen_model_path=str(model_path.relative_to(ROOT)), frozen_model_sha256=model_hash)
            folder = workspace_path(output / "evaluations" / run_id)
            save_artifact(folder, "evaluation.json", result, "evaluate")
            overall = result["overall"]
            print(f"ETA 参照评价：{overall['sample_count']} 样本，MAE={overall['mae_s']:.3f} 秒；冻结模型未修改。")
            print(f"输出：{folder.relative_to(ROOT)}/evaluation.json；这不是实际交通模型验证通过的声明。")
        return 0
    except InsufficientData as error:
        print(json.dumps(error.details, ensure_ascii=False, indent=2))
        return 2
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as error:
        print(f"ETA reference error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
