#!/usr/bin/env python3
"""Export existing C-problem evidence for the local CarClear showcase.

Standard library only. All scientific inputs are read-only. No model is rerun.
Run from any directory: python3 c_web_showcase/prepare_data.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import tarfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCIENCE = ROOT / "CCSF2605207fj"
OUTPUT = HERE / "data"
MAX_ROWS = 1000
TEMPERATURES = (25, 40, -10)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Evidence:
    def __init__(self) -> None:
        self.sources: dict[str, dict] = {}

    def read(self, relative: str) -> bytes:
        content = (SCIENCE / relative).read_bytes()
        self.sources[relative] = {
            "id": relative,
            "path": f"CCSF2605207fj/{relative}",
            "sha256": digest(content),
            "bytes": len(content),
        }
        return content

    def json(self, relative: str):
        return json.loads(self.read(relative).decode("utf-8-sig"))

    def csv(self, relative: str) -> list[dict]:
        return table(self.read(relative))


def value(cell: str):
    if cell in ("True", "False"):
        return cell == "True"
    if cell == "":
        return None
    try:
        number = float(cell)
    except ValueError:
        return cell
    if not math.isfinite(number):
        raise ValueError(f"Non-finite CSV value: {cell!r}")
    return int(number) if number.is_integer() else number


def table(content: bytes) -> list[dict]:
    return [
        {key: value(cell) for key, cell in row.items()}
        for row in csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    ]


def numeric_trace(content: bytes) -> tuple[list[str], list[list[float]]]:
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig")))
    fields = next(reader)
    if not fields or len(fields) != len(set(fields)):
        raise ValueError("Empty or duplicate trace columns")
    rows = []
    for line, raw in enumerate(reader, 2):
        if len(raw) != len(fields) or any(v == "" for v in raw):
            raise ValueError(f"Missing trace values at line {line}")
        row = [float(v) for v in raw]
        if not all(math.isfinite(v) for v in row):
            raise ValueError(f"Non-finite trace values at line {line}")
        rows.append(row)
    if not rows:
        raise ValueError("Empty trace")
    ti = fields.index("time_s")
    if any(a[ti] >= b[ti] for a, b in zip(rows, rows[1:])):
        raise ValueError("Trace timestamps are not strictly increasing")
    return fields, rows


def downsample(fields: list[str], rows: list[list[float]]) -> tuple[list[int], dict]:
    """Preserve global extrema, switch boundaries and phase boundaries first.

    Remaining capacity uses bucket extrema of plotting channels, then evenly
    distributed samples. Every exported row is an unmodified source row.
    """
    n = len(rows)
    keep = {0, n - 1}
    # Exact global minima and maxima of every numeric source/exported column.
    for column in range(len(fields)):
        keep.add(min(range(n), key=lambda i: rows[i][column]))
        keep.add(max(range(n), key=lambda i: rows[i][column]))
    discrete = ("cooler_on", "regen_fraction_requested", "resistance_soc_endpoint_hold")
    for field in discrete:
        if field in fields:
            j = fields.index(field)
            for i in range(1, n):
                if rows[i][j] != rows[i - 1][j]:
                    keep.update((i - 1, i))
    # Preserve both sides of active/inactive changes, not every continuous value.
    for field in ("motor_dc_w", "ac_w", "heater_w", "cooler_w", "speed_m_s"):
        if field in fields:
            j = fields.index(field)
            for i in range(1, n):
                before = (rows[i - 1][j] > 1e-9) - (rows[i - 1][j] < -1e-9)
                after = (rows[i][j] > 1e-9) - (rows[i][j] < -1e-9)
                if before != after:
                    keep.update((i - 1, i))
    ti = fields.index("time_s")
    for boundary in (1800, 3600, 5400, 9000, 12600):
        for i in range(1, n):
            if rows[i - 1][ti] < boundary <= rows[i][ti]:
                keep.update((i - 1, i, min(n - 1, i + 1)))
                break
    mandatory_count = len(keep)
    target = min(n, max(MAX_ROWS, mandatory_count))
    if n <= target:
        return list(range(n)), {"mandatory_rows": mandatory_count, "target_rows": target}
    channels = [
        fields.index(f)
        for f in ("speed_m_s", "battery_w", "soc", "battery_c", "cabin_c")
        if f in fields
    ]
    if not channels:
        channels = [fields.index(f) for f in fields if f != "time_s"][:4]
    buckets = max(1, (target - len(keep)) // max(1, 2 * len(channels)))
    for bucket in range(buckets):
        start, stop = bucket * n // buckets, (bucket + 1) * n // buckets
        for j in channels:
            keep.add(min(range(start, stop), key=lambda i: rows[i][j]))
            keep.add(max(range(start, stop), key=lambda i: rows[i][j]))
    # Binary refinement gives broad time coverage without clumping extra samples.
    divisions = 2
    while len(keep) < target:
        for step in range(1, divisions, 2):
            keep.add(round(step * (n - 1) / divisions))
            if len(keep) >= target:
                break
        divisions *= 2
    return sorted(keep), {"mandatory_rows": mandatory_count, "target_rows": target}


def close(actual: float, expected: float, label: str, tolerance: float = 1e-9) -> None:
    if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise ValueError(f"{label}: {actual!r} != expected {expected!r}")


def build_trace(
    trace_id: str,
    content: bytes,
    source: dict,
    ambient: float,
    model: str,
    variant: str,
    initial: dict,
    expected: dict | None = None,
) -> dict:
    fields, rows = numeric_trace(content)
    original_fields = list(fields)
    if "dt_s" in fields:
        pi, di = fields.index("battery_w"), fields.index("dt_s")
        total = 0.0
        for row in rows:
            if row[di] <= 0:
                raise ValueError(f"{trace_id}: dt_s must be positive")
            total += row[pi] * row[di] / 3_600_000.0
            row.append(total)
        fields.append("cumulative_energy_kwh")
    if expected:
        for result_key, trace_key in (
            ("energy_kwh", "cumulative_energy_kwh"),
            ("soc_final", "soc"),
            ("battery_final_c", "battery_c"),
            ("cabin_final_c", "cabin_c"),
        ):
            if result_key in expected:
                close(rows[-1][fields.index(trace_key)], expected[result_key],
                      f"{trace_id} {result_key}")
    indices, sampling = downsample(fields, rows)
    ti = fields.index("time_s")
    initial = dict(initial)
    initial["time_s"] = 0.0
    if "cumulative_energy_kwh" in fields:
        initial["cumulative_energy_kwh"] = 0.0
    labels = {
        "reference": "参考模型",
        "baseline": "基准策略",
        "optimized": "优化策略",
        "same_target": "同24℃目标",
        "validation": "588路程验证",
    }
    scope = "validation_observed_and_predicted" if variant == "validation" else (
        "driving_90min_parking_120min" if trace_id.startswith("long") else "CLTC_1800s"
    )
    return {
        "label": f"{ambient:g}℃ · {labels[variant]}",
        "model": model,
        "ambient_c": ambient,
        "variant": variant,
        "duration_s": rows[-1][ti],
        "initial": initial,
        "fields": fields,
        "rows": [rows[i] for i in indices],
        "source": source,
        "source_fields": original_fields,
        "raw_row_count": len(rows),
        "export_row_count": len(indices),
        "source_row_indices": indices,
        "sampling": sampling,
        "metric_scope": scope,
        "evidence_type": "observed_and_simulated" if variant == "validation" else "simulation",
        "branch_power_available": "motor_dc_w" in fields,
        "driving_end_s": 5400.0 if trace_id.startswith("long") else rows[-1][ti],
        "full_resolution_extrema": {
            field: {"min": min(r[j] for r in rows), "max": max(r[j] for r in rows)}
            for j, field in enumerate(fields)
        },
        "final": dict(zip(fields, rows[-1])),
    }


def main() -> None:
    evidence = Evidence()
    q1 = evidence.json("results/tables/scenario_A_summary.json")
    q1["dc_branches"] = evidence.csv("results/tables/scenario_A_dc_energy.csv")
    q1["losses"] = evidence.csv("results/tables/scenario_A_losses.csv")
    control_evaluation = evidence.json("results/tables/control_evaluation.json")
    revision = evidence.json("records/revision_20261019/results.json")
    comparisons = evidence.csv("results/tables/durability_comparisons.csv")
    protocol = evidence.json("configs/control_protocol.json")
    reference = evidence.json("configs/reference.json")
    calibrated = evidence.json("configs/calibrated.json")
    summaries = {
        "q1": q1,
        "calibration": evidence.csv("results/tables/calibration_metrics.csv"),
        "calibrationGroups": evidence.csv("results/tables/calibration_group_metrics.csv"),
        "intervals": evidence.csv("results/tables/parameter_intervals.csv"),
        "sensitivity": evidence.csv("results/tables/scenario_A_sensitivity.csv"),
        "candidates": evidence.csv("results/tables/control_design_candidates.csv"),
        "controlProtocol": protocol,
        "parameters": {"reference": reference, "calibrated": calibrated},
        "control": [],
        "sameTarget": [],
        "long": [],
        "controlLimitations": control_evaluation["limitations"],
        "all_robustness_feasible": control_evaluation["all_robustness_feasible"],
    }
    for ambient in TEMPERATURES:
        selected = {
            row["strategy"]: row for row in control_evaluation["nominal"]
            if row["ambient_c"] == ambient
        }
        baseline, optimized = selected["baseline"], selected["optimized"]
        summaries["control"].append({
            "ambient_c": ambient,
            "baseline_energy_kwh": baseline["energy_kwh"],
            "optimized_energy_kwh": optimized["energy_kwh"],
            "corrected_saving_pct": optimized["heat_corrected_saving_pct"],
            "raw_saving_pct": optimized["raw_saving_pct"],
            "baseline": baseline,
            "optimized": optimized,
            "ablations": {k: v for k, v in selected.items() if k not in ("baseline", "optimized")},
            "source": "results/tables/control_evaluation.json",
        })
        same = next(row for row in revision["same_target"] if row["ambient_c"] == ambient)
        summaries["sameTarget"].append({
            "ambient_c": ambient,
            "baseline_energy_kwh": same["metrics"]["baseline"]["energy_kwh"],
            "optimized_energy_kwh": same["metrics"]["same_target"]["energy_kwh"],
            "corrected_saving_pct": same["same_target_accounting"]["heat_corrected_saving_pct"],
            "raw_saving_pct": same["same_target_accounting"]["raw_saving_pct"],
            "baseline": same["metrics"]["baseline"],
            "optimized": same["metrics"]["same_target"],
            "accounting": same["same_target_accounting"],
            "source": "records/revision_20261019/results.json",
        })
        long = next(
            row for row in comparisons
            if row["parameter_set"] == "calibrated_v2_provisional"
            and row["ambient_c"] == ambient and row["repetitions"] == 3
            and row["parking_s"] == 7200
        )
        summaries["long"].append({
            **long,
            "corrected_saving_pct": long["heat_corrected_saving_pct"],
            "total_saving_pct": long["raw_saving_pct"],
            "driving_duration_s": 5400,
            "total_duration_s": 12600,
            "source": "results/tables/durability_comparisons.csv",
        })
    manifest = evidence.json("payload/trajectories.json")
    archive_content = evidence.read("payload/trajectories.tar.xz")
    if digest(archive_content) != manifest["archive_sha256"]:
        raise ValueError("Trajectory archive does not match its original manifest")
    members = {item["path"]: item for item in manifest["files"]}
    traces = {}
    with tarfile.open(fileobj=io.BytesIO(archive_content), mode="r:xz") as archive:
        def from_archive(filename: str) -> tuple[bytes, dict]:
            path = f"results/timeseries/{filename}.csv"
            member = archive.extractfile(path)
            if member is None:
                raise ValueError(f"Missing archive member {path}")
            content = member.read()
            if digest(content) != members[path]["sha256"]:
                raise ValueError(f"Archive member checksum mismatch: {path}")
            source = {
                "path": f"CCSF2605207fj/payload/trajectories.tar.xz!/{path}",
                "archive": "CCSF2605207fj/payload/trajectories.tar.xz",
                "member": path,
                "sha256": digest(content),
                "bytes": len(content),
            }
            evidence.sources[path] = {"id": path, **source}
            return content, source

        content, source = from_archive("scenario_A_reference")
        traces["q1"] = build_trace(
            "q1", content, source, 25, "reference_Rint", "reference",
            {"soc": q1["soc_initial"], "battery_c": q1["battery_initial_c"],
             "cabin_c": q1["cabin_initial_c"], "capacity_ah": reference["capacity_ah"]},
            {**q1, "energy_kwh": q1["net_energy_kwh"]},
        )
        for ambient in TEMPERATURES:
            initial = {
                "soc": protocol["initial_soc"], "battery_c": ambient,
                "cabin_c": protocol["initial_cabin_c"], "capacity_ah": calibrated["capacity_ah"],
            }
            short = next(x for x in summaries["control"] if x["ambient_c"] == ambient)
            long = next(x for x in summaries["long"] if x["ambient_c"] == ambient)
            for variant in ("baseline", "optimized"):
                trace_id = f"control{ambient}_{variant}"
                content, source = from_archive(f"control_{ambient}C_{variant}")
                traces[trace_id] = build_trace(
                    trace_id, content, source, ambient, "calibrated_Rint", variant,
                    initial, short[variant],
                )
                trace_id = f"long{ambient}_{variant}"
                content, source = from_archive(f"durability_{ambient}C_{variant}")
                traces[trace_id] = build_trace(
                    trace_id, content, source, ambient, "calibrated_Rint", variant,
                    initial, {
                        "energy_kwh": long[f"{variant}_energy_kwh"],
                        "soc_final": long[f"{variant}_soc_final"],
                        "battery_final_c": long[f"{variant}_battery_final_c"],
                    },
                )
            relative = f"records/revision_20261019/same_target_{ambient}C.csv"
            content = evidence.read(relative)
            same = next(x for x in summaries["sameTarget"] if x["ambient_c"] == ambient)
            traces[f"same{ambient}"] = build_trace(
                f"same{ambient}", content, evidence.sources[relative], ambient,
                "calibrated_Rint", "same_target", initial, same["optimized"],
            )
        content, source = from_archive("trip_588_predictions")
        validation_fields, validation_rows = numeric_trace(content)
        traces["validation588"] = build_trace(
            "validation588", content, source, 7.5, "reference_and_calibrated_Rint",
            "validation", {
                "soc": validation_rows[0][validation_fields.index("soc")],
                "ambient_note": "附件记录环境温度；电池初始温度按环境假设，非测温。",
            },
        )
    result = {
        "meta": {
            "schema_version": "1.0",
            "title": "新能源汽车能量流与热管理系统仿真",
            "date": "2026-10-20",
            "evidence_state": "existing_results_replayed_no_model_rerun",
            "paper": "CCSF2605207.pdf",
            "paper_sha256": digest((ROOT / "CCSF2605207.pdf").read_bytes()),
            "integration": "Full-resolution sum(battery_w * dt_s) / 3600000 before sampling.",
            "sampling": (
                "Original rows only; exact first/last, all-column global extrema, switch "
                "boundaries and driving/parking boundaries; then bucket extrema and "
                "evenly spread samples. ~1000 rows; dt_s remains the original step, "
                "not the gap between exported samples. Do not reintegrate sampled power."
            ),
            "units": {
                "time_s": "s", "dt_s": "s", "speed_m_s": "m/s", "soc": "fraction",
                "battery_c": "degC", "cabin_c": "degC", "*_w": "W",
                "cumulative_energy_kwh": "kWh",
                "soc_rmse_pp": "percentage points", "energy_ape_pct": "%",
            },
            "boundaries": [
                "Temperature and control results are simulations, not real-vehicle thermal measurements.",
                "Q1 reference Rint/160Ah/80% SOC and Q3 calibrated Rint/74.414486Ah/94% SOC are separate baselines.",
                "Single-cycle corrected saving is distinct from raw long-journey total saving.",
                "Same-target and long traces have no branch power fields; missing branches must not be fabricated.",
                "Trip588 is validation; trip600 is retrospective comparison, not a fresh blind test.",
                "The 72-rule finite candidate search is not a global continuous optimum.",
                "Not all robustness cases meet constraints; nominal feasibility is not universal robustness.",
                "Preconditioning to initial cabin24C is assumed; its prior energy is excluded.",
            ],
        },
        "sources": list(evidence.sources.values()),
        "summaries": summaries,
        "traces": traces,
    }
    OUTPUT.mkdir(exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    (OUTPUT / "showcase.json").write_bytes(encoded)
    report = {
        "status": "passed",
        "trace_count": len(traces),
        "source_count": len(evidence.sources),
        "output_sha256": digest(encoded),
        "output_bytes": len(encoded),
        "archive_checksum_verified": True,
        "selected_member_checksums_verified": True,
        "all_numeric_values_finite": True,
        "endpoint_checks": "16 simulation traces agree with original energy/SOC/temperature summaries (1e-9 tolerance).",
        "preservation": "Scientific sources read only; no simulation or calibration was rerun.",
        "traces": {
            key: {
                "raw_rows": trace["raw_row_count"], "export_rows": trace["export_row_count"],
                "duration_s": trace["duration_s"],
                "final_energy_kwh": trace["final"].get("cumulative_energy_kwh"),
                "mandatory_rows": trace["sampling"]["mandatory_rows"],
            }
            for key, trace in traces.items()
        },
    }
    (OUTPUT / "export_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(traces)} traces, {len(evidence.sources)} sources, {len(encoded):,} bytes")
    for key, trace in traces.items():
        print(f"  {key}: {trace['raw_row_count']} -> {trace['export_row_count']} rows; "
              f"t={trace['duration_s']:g}s")


if __name__ == "__main__":
    main()
