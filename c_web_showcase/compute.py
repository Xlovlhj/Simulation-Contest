"""Run the submitted Q1 model without modifying the submission package.

Only this child process imports scientific dependencies. The website server itself
uses the Python standard library. Output and dependency files stay in .runtime/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT.parent / "CCSF2605207fj"
RUNTIME = ROOT / ".runtime"
sys.dont_write_bytecode = True

SOURCE_PATHS = (
    "src/bev/model.py",
    "src/bev/io.py",
    "configs/reference.json",
    "data/processed/cltc_p.csv",
    "data/processed/component_curves.json",
    "results/tables/cltc_verification.json",
    "results/tables/scenario_A_summary.json",
)
TRACE_FIELDS = (
    "time_s", "dt_s", "speed_m_s", "soc", "battery_c", "cabin_c",
    "battery_w", "motor_dc_w", "low_voltage_w", "ac_w", "heater_w",
    "cooler_w", "joule_heat_w", "gear_loss_w", "motor_loss_w",
    "inverter_loss_w", "wheel_w", "regen_wheel_w", "friction_brake_w",
    "regen_fraction_requested", "regen_fraction_effective", "cooler_on",
    "current_a", "voltage_v", "ambient_c", "cumulative_energy_kwh",
)


def import_dependencies():
    local = RUNTIME / "deps"
    if local.is_dir():
        sys.path.insert(0, str(local))
    sys.path.insert(0, str(SUPPORT / "src"))
    import numpy
    import pandas
    from bev.model import InitialState, simulate
    return numpy, pandas, InitialState, simulate


def source_hashes() -> dict:
    return {
        name: hashlib.sha256((SUPPORT / name).read_bytes()).hexdigest()
        for name in SOURCE_PATHS
    }


def probe() -> dict:
    missing = [name for name in SOURCE_PATHS if not (SUPPORT / name).is_file()]
    if missing:
        return {"status": "unavailable", "error": "原支撑材料中的 Q1 输入文件不完整。", "missing": missing}
    try:
        numpy, pandas, _, _ = import_dependencies()
    except ImportError as exc:
        return {
            "status": "unavailable",
            "error": "真实复算需要工作区中的 NumPy 和 pandas；归档结果仍可浏览。",
            "missing_dependency": exc.name,
        }
    return {
        "status": "idle",
        "available": True,
        "dependencies": {"numpy": numpy.__version__, "pandas": pandas.__version__},
        "model": "CCSF2605207fj/src/bev/model.py::simulate",
    }


def run_baseline(run_id: str) -> dict:
    started = time.perf_counter()
    numpy, pandas, InitialState, simulate = import_dependencies()
    before = source_hashes()
    parameters = json.loads((SUPPORT / "configs/reference.json").read_text(encoding="utf-8"))
    curves = json.loads((SUPPORT / "data/processed/component_curves.json").read_text(encoding="utf-8"))
    cycle = pandas.read_csv(SUPPORT / "data/processed/cltc_p.csv", encoding="utf-8-sig")
    verification = json.loads(
        (SUPPORT / "results/tables/cltc_verification.json").read_text(encoding="utf-8")
    )
    trace, summary = simulate(
        cycle,
        parameters=parameters,
        initial_state=InitialState(soc=0.8, battery_c=25.0, cabin_c=25.0),
        max_step_s=0.5,
        scenario="A_CLTC_P_25C_reference",
        curves=curves,
        verified_cltc=True,
    )
    summary["cltc_verification_level"] = verification["verification_level"]
    summary["cltc_official_file_directly_checked"] = False
    # Integrate the original 3600 steps before selecting display rows.
    trace["cumulative_energy_kwh"] = (trace.battery_w * trace.dt_s).cumsum() / 3.6e6
    expected = json.loads(
        (SUPPORT / "results/tables/scenario_A_summary.json").read_text(encoding="utf-8")
    )
    checked_keys = (
        "distance_km", "net_energy_kwh", "consumption_kwh_100km",
        "soc_final", "battery_max_c", "cabin_final_c",
    )
    comparison = {
        key: {
            "computed": summary[key],
            "archived": expected[key],
            "absolute_difference": abs(summary[key] - expected[key]),
            "matches": math.isclose(summary[key], expected[key], rel_tol=1e-9, abs_tol=1e-10),
        }
        for key in checked_keys
    }
    after = source_hashes()
    if before != after:
        raise RuntimeError("计算期间原始模型或输入文件发生变化，本次结果未通过来源检查。")
    count = min(900, len(trace))
    indices = numpy.rint(numpy.linspace(0, len(trace) - 1, count)).astype(int)
    selected = trace.iloc[indices][list(TRACE_FIELDS)]
    rows = [
        [float(value) if isinstance(value, (float, numpy.floating)) else int(value) for value in row]
        for row in selected.itertuples(index=False, name=None)
    ]
    return {
        "status": "completed",
        "run_id": run_id,
        "elapsed_s": time.perf_counter() - started,
        "computed_now": True,
        "scenario": "A_CLTC_P_25C_reference",
        "label": "本次真实复算 · Q1 参考参数 · 25 ℃ · 初始 SOC 80%",
        "model": "CCSF2605207fj/src/bev/model.py::simulate",
        "dependencies": {"numpy": numpy.__version__, "pandas": pandas.__version__},
        "summary": summary,
        "trace": {
            "fields": list(TRACE_FIELDS),
            "rows": rows,
            "source_rows": len(trace),
            "sampled_rows": len(rows),
            "sampling": "uniform row sampling including first and last; summary uses all original steps",
        },
        "source_hashes": before,
        "source_files_unchanged": True,
        "archive_comparison": comparison,
        "matches_archive": all(row["matches"] for row in comparison.values()),
        "limitations": [
            "实时调用论文支撑材料中的原始 Rint 求解器；没有重新标定或优化控制。",
            "CLTC-P 为两份公开复现序列互核，未直接核查官方标准原件。",
            "温度是模型仿真结果；并非独立实车温度测量。",
            "图形回放最多保留 900 行；汇总能耗使用全部 3600 个积分步计算。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="manual")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), ensure_ascii=False))
        return 0
    if args.output is None:
        parser.error("--output is required for a real calculation")
    output = args.output.resolve()
    if not output.is_relative_to((RUNTIME / "runs").resolve()):
        parser.error("output must be inside this website's .runtime/runs directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run_baseline(args.run_id)
        exit_code = 0
    except Exception as exc:
        result = {
            "status": "failed",
            "run_id": args.run_id,
            "error": f"{type(exc).__name__}: {exc}",
            "computed_now": False,
        }
        exit_code = 1
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
