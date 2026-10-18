#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot/release date was
# not provided; implementation and research interpretation await team review.
"""Fit a frozen ETA observation layer and evaluate separate temporal holdouts.

The physical SUMO mechanism is not calibrated by this script. Its fixed OD
times provide one predictor for Baidu platform ETA; training medians provide
the default route state and a simple comparison. Offline and deterministic.
"""
from __future__ import annotations

import argparse
import csv
from html import escape
import io
import json
import math
from pathlib import Path
import statistics
import sys

import finalize_data as freeze
import prepare_calibration as prep

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("physical_sumo", "shared_affine_eta", "per_od_training_median")
PHASES = ("training", "same_day_exploratory", "cross_day_holdout")


def flag(value: object) -> bool:
    if value is True or value in ("True", "true"):
        return True
    if value is False or value in (None, "", "False", "false"):
        return False
    raise ValueError("Invalid Boolean field")


def finite_positive(value: object) -> float:
    number = float(value)
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError("Expected a positive finite duration")
    return number


def select_rows(rows: list[dict], manifest: dict, phase: str) -> list[dict]:
    """Validate raw eligibility, unique buckets and temporal separation again."""
    window = manifest["protocol"]["windows"][phase]
    start, end = (freeze.timestamp(window[k]) for k in ("start", "end"))
    width = manifest["protocol"]["bucket_minutes"] * 60
    used_ids, used_buckets, selected = set(), set(), []
    for row in rows:
        sid = row["sample_id"]
        if not sid or sid in used_ids:
            raise ValueError("Repeated or missing sample ID")
        used_ids.add(sid)
        if row["phase"] != phase:
            continue
        if not flag(row["fit_eligible"] if phase == "training" else row["evaluation_eligible"]):
            continue
        if flag(row["fit_eligible"]) != (phase == "training") or flag(row["evaluation_eligible"]) != (phase != "training"):
            raise ValueError("Training/evaluation flags leak across partitions")
        identity = row["route_pair_id"]
        if identity not in manifest["od_ids"] or not all(flag(row[k]) for k in (
                "api_success", "od_matches_current", "selected_in_bucket", "whether_matches_baseline", "baseline_comparison_eligible")):
            raise ValueError("Eligible row fails API, OD, selection or path requirements")
        if row["outside_model_bounds"] not in (False, "false", "False") or row["duplicate_of"]:
            raise ValueError("Outside-bounds or duplicate row is eligible")
        if row["geometry_signature"] != manifest["baseline_geometry_signatures"][identity] or row["geometry_signature"] != row["baseline_geometry_signature"]:
            raise ValueError("Path changed from the frozen baseline")
        requested, received = (freeze.timestamp(row[k]) for k in ("requested_at", "received_at"))
        if not start <= requested <= received < end:
            raise ValueError("Eligible request/response is outside its declared partition")
        bucket = math.floor(requested / width) * width
        if freeze.timestamp(row["bucket_start_at"]) != bucket or (identity, bucket) in used_buckets:
            raise ValueError("Incorrect or duplicated OD/time bucket")
        used_buckets.add((identity, bucket))
        selected.append({"sample_id": sid, "route_pair_id": identity, "phase": phase,
            "requested_at": row["requested_at"], "received_at": row["received_at"],
            "bucket_start_at": row["bucket_start_at"], "geometry_signature": row["geometry_signature"],
            "estimated_duration_s": finite_positive(row["estimated_duration_s"]),
            "response_sha256": row["response_sha256"]})
    selected.sort(key=lambda row: (row["requested_at"], row["sample_id"]))
    expected = manifest["data_audit"]["phases"][phase]
    for identity in manifest["od_ids"]:
        if sum(s["route_pair_id"] == identity for s in selected) != expected["per_od"][identity]["eligible_count"]:
            raise ValueError("Manifest counts differ from selected rows")
    return selected


def fit_nonnegative_affine(points: list[tuple[float, float]]) -> dict:
    """Exact two-variable nonnegative least squares by active-set enumeration."""
    if len(points) < 2 or any(not math.isfinite(x) or x <= 0 or not math.isfinite(y) or y <= 0 for x, y in points):
        raise ValueError("At least two finite positive training pairs are required")
    mean_x, mean_y = (statistics.fmean(p[i] for p in points) for i in (0, 1))
    centered_xx = sum((x - mean_x) ** 2 for x, _ in points)
    if centered_xx <= 1e-12 * sum(x * x for x, _ in points):
        raise ValueError("Shared slope and intercept are not identifiable: fixed OD times have no variation")
    alpha = sum((x - mean_x) * (y - mean_y) for x, y in points) / centered_xx
    beta = mean_y - alpha * mean_x
    candidates = [(0.0, mean_y, "alpha_zero"),
        (sum(x*y for x, y in points) / sum(x*x for x, _ in points), 0.0, "beta_zero"),
        (0.0, 0.0, "both_zero")]
    if alpha >= 0 and beta >= 0:
        candidates.append((alpha, beta, "interior"))
    scored = [(sum((a*x+b-y)**2 for x, y in points), a, b, boundary) for a, b, boundary in candidates]
    sse, alpha, beta, boundary = min(scored)
    return {"alpha": alpha, "beta_s": beta, "active_constraint": boundary, "training_sse_s2": sse,
        "training_count": len(points), "design_rank": 2, "unique_physical_times": len({x for x, _ in points}),
        "physical_time_centered_sum_of_squares_s2": centered_xx,
        "uncertainty_note": "Four fixed OD predictor levels; repeated ETA samples are serially dependent. No IID confidence interval or physical-parameter identification is claimed."}


def fit_models(training: list[dict], physical: dict[str, float]) -> dict:
    if not training or set(physical) != {row["route_pair_id"] for row in training}:
        raise ValueError("Training must cover every fixed OD feature")
    if any(row["phase"] != "training" for row in training):
        raise ValueError("Only training samples may enter fitting")
    parameters = fit_nonnegative_affine([(physical[r["route_pair_id"]], r["estimated_duration_s"]) for r in training])
    per_od = {}
    for identity, physical_time in sorted(physical.items()):
        part = [r["estimated_duration_s"] for r in training if r["route_pair_id"] == identity]
        median = statistics.median(part)
        per_od[identity] = {"physical_sumo_s": physical_time, "training_median_s": median,
            "equivalent_route_residual_s": median - physical_time,
            "shared_affine_eta_s": parameters["alpha"] * physical_time + parameters["beta_s"],
            "training_count": len(part)}
    return {"shared_affine": parameters, "per_od": per_od,
        "default_eta_state": "per_od_training_median", "validation_used_for_fitting": False,
        "model_selection": "Predeclared median ETA state plus shared affine research comparator; holdout does not select or tune either.",
        "equivalent_residual_interpretation": "Median platform ETA minus fixed hypothetical SUMO time. It aggregates unresolved route costs, signal/geometry/flow assumptions and provider estimation; it is not measured queue delay."}


def metric_rows(predictions: list[dict]) -> list[dict]:
    result = []
    for phase in PHASES:
        identities = sorted({r["route_pair_id"] for r in predictions if r["phase"] == phase})
        for identity in ["ALL"] + identities:
            for method in METHODS:
                part = [r for r in predictions if r["phase"] == phase and r["method"] == method and (identity == "ALL" or r["route_pair_id"] == identity)]
                if not part:
                    continue
                errors = [r["predicted_duration_s"] - r["observed_platform_eta_s"] for r in part]
                absolute = sorted(abs(e) for e in errors)
                result.append({"phase": phase, "route_pair_id": identity, "method": method, "sample_count": len(part),
                    "mae_s": statistics.fmean(absolute), "rmse_s": math.sqrt(statistics.fmean(e*e for e in errors)),
                    "bias_s": statistics.fmean(errors), "max_absolute_error_s": max(absolute),
                    "wape_percent": 100 * sum(absolute) / sum(r["observed_platform_eta_s"] for r in part)})
    return result


def predict(model: dict, samples: list[dict]) -> list[dict]:
    result = []
    for row in samples:
        params = model["models"]["per_od"][row["route_pair_id"]]
        predictions = {"physical_sumo": params["physical_sumo_s"], "shared_affine_eta": params["shared_affine_eta_s"],
                       "per_od_training_median": params["training_median_s"]}
        for method, value in predictions.items():
            result.append({**row, "method": method, "observed_platform_eta_s": row["estimated_duration_s"],
                "predicted_duration_s": value, "absolute_error_s": abs(value - row["estimated_duration_s"])})
    return result


def csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("Refusing to write an empty result table")
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def snapshot(config: dict) -> tuple[list[dict], dict]:
    folder = prep.workspace_path(config["output_data"])
    manifest = prep.load_json(folder / "data_manifest.json")
    raw = (folder / "samples.csv").read_bytes()
    if prep.digest(raw) != manifest["samples_sha256"]:
        raise ValueError("Frozen samples do not match their manifest")
    for item in manifest["source_files"]:
        if prep.digest(prep.workspace_path(item["path"]).read_bytes()) != item["sha256"]:
            raise ValueError("Frozen source has changed: " + item["path"])
    for key in ("protocol_id", "study_area_id", "coordinate_system", "bucket_minutes", "windows", "protocol_notes"):
        if config[key] != manifest["protocol"][key]:
            raise ValueError("Configured protocol differs from frozen data")
    return list(csv.DictReader(io.StringIO(raw.decode()))), manifest


def physical_features(config: dict, data: dict) -> dict:
    if not config.get("physical_baseline_directory"):
        raise ValueError("Final 307-edge S1 baseline directory has not yet been frozen")
    folder = prep.workspace_path(config["physical_baseline_directory"])
    metrics = prep.load_json(folder / "metrics.json")
    manifest_path = folder / "run_manifest.json"
    manifest = prep.load_json(manifest_path)
    if metrics.get("physics_valid", manifest.get("physics_valid")) is not True:
        raise ValueError("Physical baseline did not pass collision/teleport/conservation checks")
    if manifest.get("status") != "completed" or manifest.get("scenario_family") != "S1" or manifest.get("policy") != "fixed":
        raise ValueError("Physical features require the completed no-event fixed-route S1")
    if manifest.get("network", {}).get("edge_count") != 307 or manifest["network"].get("connection_count") != 310:
        raise ValueError("Physical features require the frozen 307-edge / 310-turn network")
    if manifest.get("config", {}).get("departure_interval_s") != 30:
        raise ValueError("Physical feature demand interval must remain at 30 seconds")
    survey = prep.workspace_path(config["survey_routes"])
    if manifest.get("inputs_sha256", {}).get(config["survey_routes"]) != prep.digest(survey.read_bytes()):
        raise ValueError("S1 and ETA observations refer to different surveyed routes")
    field = config["model"]["physical_time_field"]
    values = {}
    for identity in data["od_ids"]:
        part = metrics["per_od"][identity]
        if part["planned"] <= 0 or part["planned"] != part["arrived"]:
            raise ValueError("Baseline OD has unfinished planned vehicles")
        values[identity] = finite_positive(part[field])
    files = [freeze.file_record(folder / name) for name in ("metrics.json", "run_manifest.json")]
    # Where the runner supplies artifact hashes, verify rather than silently trust.
    for name, digest in manifest.get("artifacts", {}).items():
        if name in ("metrics.json", "tripinfo.xml", "demand.rou.xml", "route_manifest.json", "network.net.xml"):
            path = prep.workspace_path(folder / name)
            if prep.digest(path.read_bytes()) != digest:
                raise ValueError("Physical baseline artifact hash mismatch: " + name)
            if name != "metrics.json":
                files.append(freeze.file_record(path))
    return {"directory": str(folder.relative_to(ROOT)), "field": field, "values_s": values,
        "source_files": files, "physics_valid": True,
        "network": manifest["network"], "simulation_config": manifest["config"],
        "time_definition": "Actual vehicle departure to arrival; scheduled-release queue delay excluded.",
        "assumptions": "Fixed 307-edge hypothetical SUMO S1 network, six OD demands at 30-second intervals. Four matching OD means are predictors, not observed travel times."}


def save_immutable(path: Path, content: bytes) -> None:
    if path.exists() and path.read_bytes() != content:
        raise ValueError("Refusing to overwrite a different frozen artifact: " + str(path.relative_to(ROOT)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def fit(config: dict, rows: list[dict], data: dict) -> Path:
    gate = data["data_audit"]["phases"]["training"]
    if not gate["ready"]:
        raise ValueError("Training sampling gate is incomplete: " + json.dumps(gate["per_od"]))
    training = select_rows(rows, data, "training")
    physical = physical_features(config, data)
    model = {"schema_version": 1, "protocol_id": config["protocol_id"], "method": config["model"],
        "models": fit_models(training, physical["values_s"]), "physical_baseline": physical,
        "training_samples": training, "training_sample_ids": [r["sample_id"] for r in training],
        "training_sha256": prep.digest(freeze.canonical(training)), "data_snapshot_id": data["snapshot_id"],
        "samples_sha256": data["samples_sha256"], "windows": data["protocol"]["windows"],
        "source_code": freeze.file_record(Path(__file__)), "physical_mechanism_calibrated": False,
        "target": "Route-level platform ETA, not measured vehicle travel time"}
    model_id = "ETA_" + prep.digest(freeze.canonical(model))[:16]
    model["model_id"] = model_id
    folder = prep.workspace_path(config["output_validation"]) / model_id
    if not folder.is_relative_to(ROOT / "outputs/validation"):
        raise ValueError("Validation output must remain under outputs/validation")
    content = freeze.json_bytes(model)
    save_immutable(folder / "model.json", content)
    save_immutable(folder / "model_manifest.json", freeze.json_bytes({"model_id": model_id,
        "model_sha256": prep.digest(content), "training_sha256": model["training_sha256"],
        "validation_used_for_fit": False, "created_after_collection": True}))
    return folder / "model.json"


def mae_svg(metrics: list[dict]) -> bytes:
    colors = {"physical_sumo": "#94a3b8", "shared_affine_eta": "#d97706", "per_od_training_median": "#2563eb"}
    phases = ("same_day_exploratory", "cross_day_holdout")
    relevant = [m for m in metrics if m["phase"] in phases and m["route_pair_id"] == "ALL"]
    max_value = max((m["mae_s"] for m in relevant), default=1) * 1.15
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="440" viewBox="0 0 1000 440">',
        '<rect width="1000" height="440" fill="white"/>',
        '<g font-family="DejaVu Sans,Arial,sans-serif" fill="#172033">',
        '<text x="40" y="32" font-size="21">Held-out error against Baidu route ETA (seconds)</text>',
        '<text x="40" y="57" font-size="13">Lower is better. ETA estimates are not measured vehicle travel times.</text>']
    for i in range(6):
        y = 340 - i * 48
        parts += [f'<line x1="80" y1="{y}" x2="955" y2="{y}" stroke="#e2e8f0"/>',
                  f'<text x="69" y="{y+5}" text-anchor="end" font-size="12">{max_value*i/5:.0f}</text>']
    for index, phase in enumerate(phases):
        for j, method in enumerate(METHODS):
            found = [m for m in relevant if m["phase"] == phase and m["method"] == method]
            if not found:
                continue
            value = found[0]["mae_s"]
            x, height = 130 + index*430 + j*105, 240*value/max_value
            parts += [f'<rect x="{x}" y="{340-height:.3f}" width="75" height="{height:.3f}" fill="{colors[method]}"/>',
                f'<text x="{x+37.5}" y="{330-height:.3f}" text-anchor="middle" font-size="14">{value:.2f}</text>']
        parts.append(f'<text x="{272+index*430}" y="365" text-anchor="middle" font-size="15">{escape(phase)}</text>')
    for i, method in enumerate(METHODS):
        x = 55 + i*310
        parts += [f'<rect x="{x}" y="400" width="15" height="15" fill="{colors[method]}"/>',
            f'<text x="{x+23}" y="413" font-size="14">{method}</text>']
    return ("\n".join(parts) + '</g></svg>\n').encode()


def time_svg(predictions: list[dict], model: dict) -> bytes:
    ids = sorted(model["models"]["per_od"])
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1160" height="820" viewBox="0 0 1160 820">',
        '<rect width="1160" height="820" fill="white"/><g font-family="DejaVu Sans,Arial,sans-serif" fill="#172033">',
        '<text x="30" y="30" font-size="21">Frozen predictions and held-out platform ETA</text>',
        '<text x="30" y="54" font-size="13">Dots: platform ETA; blue: training median; orange: shared affine. Actual request time, UTC+08.</text>']
    for panel, identity in enumerate(ids):
        for col, phase in enumerate(("same_day_exploratory", "cross_day_holdout")):
            observed = [r for r in predictions if r["method"] == "per_od_training_median" and r["phase"] == phase and r["route_pair_id"] == identity]
            left, top, width, height = 75+col*565, 95+panel*175, 455, 120
            window = model["windows"][phase]
            begin, finish = (freeze.timestamp(window[k]) for k in ("start", "end"))
            params = model["models"]["per_od"][identity]
            vals = [r["observed_platform_eta_s"] for r in observed] + [params["training_median_s"], params["shared_affine_eta_s"]]
            low, high = max(0, min(vals)-20), max(vals)+20
            ypos = lambda value: top+height-(value-low)/(high-low)*height
            parts.append(f'<text x="{left}" y="{top-13}" font-size="13">{identity} / {phase}</text>')
            for value in (low, (low+high)/2, high):
                y = ypos(value)
                parts += [f'<line x1="{left}" x2="{left+width}" y1="{y:.3f}" y2="{y:.3f}" stroke="#e2e8f0"/>',
                    f'<text x="{left-8}" y="{y+4:.3f}" text-anchor="end" font-size="11">{value:.0f}s</text>']
            for name, color in (("training_median_s", "#2563eb"), ("shared_affine_eta_s", "#d97706")):
                y = ypos(params[name])
                parts.append(f'<line x1="{left}" x2="{left+width}" y1="{y:.3f}" y2="{y:.3f}" stroke="{color}" stroke-width="2"/>')
            for row in observed:
                x = left+(freeze.timestamp(row["requested_at"])-begin)/(finish-begin)*width
                parts.append(f'<circle cx="{x:.3f}" cy="{ypos(row["observed_platform_eta_s"]):.3f}" r="2.8" fill="#111827"/>')
            for x, key in ((left,"start"),(left+width,"end")):
                label = window[key][5:16].replace("T", " ")
                parts.append(f'<text x="{x}" y="{top+height+18}" text-anchor="middle" font-size="11">{label}</text>')
    return ("\n".join(parts) + '</g></svg>\n').encode()


def evaluate(config: dict, rows: list[dict], data: dict, model_path: Path) -> dict:
    raw = model_path.read_bytes()
    model = json.loads(raw)
    seal = prep.load_json(model_path.parent / "model_manifest.json")
    if prep.digest(raw) != seal["model_sha256"] or model["models"]["validation_used_for_fitting"] is not False:
        raise ValueError("Frozen model integrity check failed")
    if model["samples_sha256"] != data["samples_sha256"] or model["data_snapshot_id"] != data["snapshot_id"]:
        raise ValueError("Evaluation data differs from the frozen snapshot")
    training = select_rows(rows, data, "training")
    if prep.digest(freeze.canonical(training)) != model["training_sha256"]:
        raise ValueError("Training records changed after model freeze")
    samples = list(training)
    for phase in PHASES[1:]:
        if not data["data_audit"]["phases"][phase]["ready"]:
            raise ValueError(f"Evaluation sampling gate incomplete: {phase}")
        selected = select_rows(rows, data, phase)
        if set(model["training_sample_ids"]) & {r["sample_id"] for r in selected}:
            raise ValueError("Validation sample leaks into training")
        samples.extend(selected)
    predictions = predict(model, samples)
    metrics = metric_rows(predictions)
    overall = {phase: {r["method"]: r for r in metrics if r["phase"] == phase and r["route_pair_id"] == "ALL"} for phase in PHASES}
    comparisons = {}
    for phase, values in overall.items():
        affine, median = values["shared_affine_eta"]["mae_s"], values["per_od_training_median"]["mae_s"]
        comparisons[phase] = {"shared_affine_minus_median_mae_s": affine-median,
            "shared_affine_lower_mae": affine < median,
            "relative_mae_reduction_percent": 100*(median-affine)/median if median else None,
            "model_selection_changed": False}
    limitations = [
        "Validation target is provider-estimated route ETA; no observed vehicle trajectories, counts or measured signal plans are available.",
        "The ETA observation layer is fitted; SUMO lane count, demand, speed and priority rules remain uncalibrated assumptions.",
        "Only four fixed routes and two short weekend windows are observed. Cross-day holdout also changes time of day; day and time-of-day effects cannot be separated.",
        "Repeated five-minute estimates are serially dependent; the 96 cross-day rows are not 96 independent traffic experiments.",
        "Both dates were already collected when this final protocol was documented. No holdout response enters the fitting objective or selection rule.",
        "The original 30-minute same-day validation was incomplete. This report explicitly labels the available 20 minutes exploratory.",
        "The source observation timestamp is absent; request/receive times bound API access, not the age of underlying traffic observations.",
        "No accuracy pass threshold is asserted, and a worse-than-median affine result is retained."
    ]
    folder = model_path.parent
    summary = {"schema_version": 1, "status": "eta_layer_evaluated", "run_id": model["model_id"],
        "run_dir": str(folder.relative_to(ROOT)), "protocol": data["protocol"], "data_snapshot_id": data["snapshot_id"],
        "data_audit": data["data_audit"], "collections": data["collections"], "time_mapping": data["time_mapping"],
        "physical_baseline": model["physical_baseline"], "models": model["models"], "metrics": overall,
        "per_od_metrics": [r for r in metrics if r["route_pair_id"] != "ALL"], "comparison": comparisons,
        "physical_mechanism_calibrated": False, "real_vehicle_validation_completed": False,
        "model_refitted_during_evaluation": False, "limitations": limitations,
        "input_hashes": {"model_sha256": prep.digest(raw), "samples_sha256": data["samples_sha256"],
            "data_manifest_sha256": prep.digest((prep.workspace_path(config["output_data"])/"data_manifest.json").read_bytes()),
            "validation_source_sha256": prep.digest(Path(__file__).read_bytes())}}
    report = ["# S1 路线 ETA 状态层：冻结与时间留出评价", "",
        "结论范围：建立了可复现的路线 ETA 预测与留出评价；SUMO 物理机制未经过现场流量、信号或逐车数据标定。", "",
        "公式：共享模型 T_hat = alpha * T_SUMO,OD + beta；非负最小二乘只使用 17 日训练窗。默认状态为每 OD 训练中位数，跨日结果不用于选模型。", "",
        f"alpha={model['models']['shared_affine']['alpha']:.8f}, beta={model['models']['shared_affine']['beta_s']:.8f} s。", "",
        "|分区|样本数|SUMO 原时耗 MAE(s)|共享 ETA 层 MAE(s)|OD 中位数 MAE(s)|", "|---|---:|---:|---:|---:|"]
    for phase, values in overall.items():
        report.append(f"|{phase}|{values['physical_sumo']['sample_count']}|{values['physical_sumo']['mae_s']:.4f}|{values['shared_affine_eta']['mae_s']:.4f}|{values['per_od_training_median']['mae_s']:.4f}|")
    report += ["", "共享模型比中位数更差的结果完整保留。等效路线残差 medianETA−SUMOtime 只表示两层间未解释时耗，不能解释为测得的排队时间。", "", "## 证据边界", ""] + ["- " + item for item in limitations]
    artifacts = {"predictions.csv": csv_bytes(predictions), "metrics.csv": csv_bytes(metrics),
        "eta_mae.svg": mae_svg(metrics), "eta_holdout_timeseries.svg": time_svg(predictions, model),
        "report.md": ("\n".join(report)+"\n").encode()}
    summary["artifacts"] = {name: {"path": str((folder/name).relative_to(ROOT)), "sha256": prep.digest(content)} for name, content in artifacts.items()}
    summary["artifacts"]["model.json"] = {"path": str(model_path.relative_to(ROOT)), "sha256": prep.digest(raw)}
    for name, content in artifacts.items():
        save_immutable(folder/name, content)
    save_immutable(folder/"summary.json", freeze.json_bytes(summary))
    # Stable summary is an index containing the complete report; historical runs
    # remain in content-addressed directories and are never overwritten.
    prep.workspace_path(config["output_validation"]).joinpath("summary.json").write_bytes(freeze.json_bytes(summary))
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/final_validation.json")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--fit", action="store_true")
    actions.add_argument("--evaluate", action="store_true")
    actions.add_argument("--run", action="store_true", help="Freeze a model, then evaluate its saved bytes")
    parser.add_argument("--model", help="Frozen model.json required for --evaluate")
    args = parser.parse_args(argv)
    if not any((args.fit, args.evaluate, args.run)):
        parser.print_help()
        return 0
    try:
        config = prep.load_json(prep.workspace_path(args.config))
        rows, data = snapshot(config)
        model_path = fit(config, rows, data) if args.fit or args.run else prep.workspace_path(args.model or "MISSING_MODEL")
        if args.evaluate or args.run:
            summary = evaluate(config, rows, data, model_path)
            print(json.dumps({"status": summary["status"], "run_id": summary["run_id"], "summary": config["output_validation"]+"/summary.json", "metrics": summary["metrics"]}, ensure_ascii=False))
        else:
            print(json.dumps({"status": "eta_model_frozen", "model": str(model_path.relative_to(ROOT))}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
