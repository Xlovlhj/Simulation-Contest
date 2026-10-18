"""阶段一产出流水线；AI辅助代码与复核说明见records/ai_usage.md。"""
from __future__ import annotations

import importlib.metadata
import platform
import time
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

from .data import audit_data, read_reference_cycle, read_trips
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import InitialState, load_parameters, simulate


def setup_figures() -> None:
    fonts = {entry.name for entry in font_manager.fontManager.ttflist}
    for preferred in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]:
        if preferred in fonts:
            plt.rcParams["font.sans-serif"] = [preferred]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 130


def plot_audit() -> None:
    setup_figures()
    frame = read_trips()
    trip_ids = frame.trip_id.unique()
    fig, axes = plt.subplots(4, len(trip_ids), figsize=(15, 9), sharex="col")
    for column, trip_id in enumerate(trip_ids):
        trip = frame[frame.trip_id.eq(trip_id)]
        time_s = trip.time_s - trip.time_s.iloc[0]
        for row, (key, label) in enumerate([("speed_kmh", "车速 / km/h"), ("discharge_current_a", "放电为正电流 / A"),
                                          ("voltage_v", "端电压 / V"), ("soc_pct", "SOC / %")]):
            axes[row, column].plot(time_s, trip[key], lw=0.8)
            axes[row, column].set_ylabel(label)
            axes[row, column].grid(alpha=0.25)
        axes[0, column].set_title(f"路程 {trip_id}")
        axes[-1, column].set_xlabel("时间 / s")
    fig.suptitle("原始记录质量审计：只检查，不进行参数拟合", fontsize=14)
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/data_audit.png")
    plt.close(fig)


def run_reference(*, include_sensitivity: bool = True) -> dict:
    cycle = read_reference_cycle()
    trace, summary = simulate(cycle)
    trace.to_csv(PROJECT_ROOT / "results/timeseries/reference_E_25C.csv", index=False, encoding="utf-8-sig")
    write_json(PROJECT_ROOT / "results/tables/reference_E_summary.json", summary)
    parameters = load_parameters()
    _, finer = simulate(cycle, max_step_s=0.25)
    convergence = {"scenario": summary["scenario"], "coarse_step_s": 0.5, "fine_step_s": 0.25,
                   "net_energy_relative_difference": abs(summary["net_energy_kwh"]-finer["net_energy_kwh"])/abs(finer["net_energy_kwh"]),
                   "battery_max_abs_difference_c": abs(summary["battery_max_c"]-finer["battery_max_c"]),
                   "soc_final_abs_difference": abs(summary["soc_final"]-finer["soc_final"]),
                   "energy_tolerance": 0.01, "temperature_tolerance_c": 0.1}
    convergence["passed"] = convergence["net_energy_relative_difference"] < convergence["energy_tolerance"] and convergence["battery_max_abs_difference_c"] < convergence["temperature_tolerance_c"]
    write_json(PROJECT_ROOT / "results/tables/convergence.json", convergence)
    if not convergence["passed"]:
        raise RuntimeError("步长减半核查未通过，请先修正数值设置")
    setup_figures()
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes[0, 0].plot(cycle.time_s, cycle.speed_m_s*3.6, color="#2864a5")
    axes[0, 0].set_ylabel("车速 / km/h")
    axes[0, 1].plot(trace.time_s, trace.soc*100, color="#247a52")
    axes[0, 1].set_ylabel("SOC / %")
    axes[1, 0].plot(trace.time_s, trace.battery_c, label="电池")
    axes[1, 0].plot(trace.time_s, trace.cabin_c, label="座舱")
    axes[1, 0].set_ylabel("温度 / ℃")
    axes[1, 0].legend()
    axes[1, 1].plot(trace.time_s, trace.battery_w/1000, lw=0.7, label="电池端净功率")
    axes[1, 1].axhline(0, color="grey", lw=0.7)
    axes[1, 1].set_ylabel("功率 / kW")
    axes[1, 1].legend()
    for ax in axes.flat:
        ax.set_xlabel("时间 / s")
        ax.grid(alpha=0.25)
    fig.suptitle("场景E参考工况，25℃；未校准；未核实为CLTC", fontsize=14)
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/reference_E_overview.png")
    plt.close(fig)
    branches = [{"branch": "牵引DC输入", "energy_kwh": summary["traction_dc_energy_kwh"], "level": "DC_bus"},
                {"branch": "电机回流DC", "energy_kwh": -summary["regen_dc_energy_kwh"], "level": "DC_bus"}]
    for key, label in [("low_voltage_w", "低压附件"), ("ac_w", "空调"), ("heater_w", "加热"), ("cooler_w", "电池冷却")]:
        branches.append({"branch": label, "energy_kwh": summary["energy_kwh"][key], "level": "DC_bus"})
    pd.DataFrame(branches).to_csv(PROJECT_ROOT / "results/tables/reference_E_dc_energy.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.barh([b["branch"] for b in branches], [b["energy_kwh"] for b in branches],
            color=["#d28736" if b["energy_kwh"] >= 0 else "#25977e" for b in branches])
    ax.axvline(0, color="grey", lw=0.8)
    ax.set_xlabel("能量 / kWh（DC母线边界，回流为负）")
    ax.set_title("参考工况能量分支；净电池电能为各支路之和")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/reference_E_dc_energy.png")
    plt.close(fig)
    if include_sensitivity:
        keys = [("drag_coefficient", "空气阻力系数"), ("rolling_coefficient", "滚动阻力系数"),
                ("resistance_ref_ohm", "电池参考内阻"), ("mass_kg", "整车质量"),
                ("low_voltage_w", "低压附件功率"), ("cooling_cop", "制冷COP"),
                ("battery_heat_capacity_j_k", "电池热容")]
        rows = []
        eps = 0.1
        for key, label in keys:
            results = []
            for sign in (-1, 1):
                perturbed = {**parameters, key: parameters[key]*(1+sign*eps)}
                _, output = simulate(cycle, perturbed)
                results.append(output)
            lower, upper = results
            rows.append({"parameter": key, "label": label, "reference": parameters[key], "fractional_perturbation": eps,
                         "energy_lower_kwh_100km": lower["consumption_kwh_100km"], "energy_upper_kwh_100km": upper["consumption_kwh_100km"],
                         "normalized_energy_sensitivity": (upper["consumption_kwh_100km"]-lower["consumption_kwh_100km"])/(2*eps*summary["consumption_kwh_100km"]),
                         "normalized_ocv_electric_sensitivity": (upper["ocv_electric_energy_kwh"]-lower["ocv_electric_energy_kwh"])/(2*eps*summary["ocv_electric_energy_kwh"]),
                         "normalized_soc_draw_sensitivity": (lower["soc_final"]-upper["soc_final"])/(2*eps*(summary["soc_initial"]-summary["soc_final"])),
                         "battery_max_lower_c": lower["battery_max_c"], "battery_max_upper_c": upper["battery_max_c"],
                         "battery_temperature_slope_c_per_fraction": (upper["battery_max_c"]-lower["battery_max_c"])/(2*eps),
                         "soc_final_lower": lower["soc_final"], "soc_final_upper": upper["soc_final"]})
        sensitivity = pd.DataFrame(rows)
        sensitivity["energy_rank"] = sensitivity.normalized_energy_sensitivity.abs().rank(ascending=False, method="min").astype(int)
        sensitivity = sensitivity.sort_values("energy_rank")
        sensitivity.to_csv(PROJECT_ROOT / "results/tables/reference_E_sensitivity.csv", index=False, encoding="utf-8-sig")
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.barh(sensitivity.label[::-1], sensitivity.normalized_energy_sensitivity[::-1], color="#2864a5")
        ax.set_xlabel("百公里能耗的标准化灵敏度（±10%单因素扰动）")
        ax.set_title("场景E参考模型：七参数能耗灵敏度排序")
        fig.tight_layout()
        fig.savefig(PROJECT_ROOT / "results/figures/reference_E_sensitivity.png")
        plt.close(fig)
    return summary


def run_checks() -> dict:
    suite = unittest.defaultTestLoader.discover(str(PROJECT_ROOT / "checks"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful()}
    convergence_path = PROJECT_ROOT / "results/tables/convergence.json"
    if convergence_path.exists():
        report["reference_convergence"] = read_json(convergence_path)
    write_json(PROJECT_ROOT / "results/tables/verification.json", report)
    if not result.wasSuccessful():
        raise RuntimeError("物理核查失败")
    return report


def write_status(audit: dict, summary: dict, verification: dict, elapsed_s: float, *, write_latest: bool = True) -> dict:
    code_files = sorted((PROJECT_ROOT / "src").rglob("*.py")) + sorted((PROJECT_ROOT / "configs").glob("*.json")) + sorted((PROJECT_ROOT / "checks").glob("*.py")) + [PROJECT_ROOT / "run.py"]
    manifest = {"session_date_beijing": "2026-10-17", "date_source": "client_context_not_host_clock",
                "stage": "initial_reference_model", "elapsed_s": elapsed_s, "python": platform.python_version(),
                "dependencies": {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scipy", "matplotlib", "openpyxl"]},
                "code_and_config": {p.relative_to(PROJECT_ROOT).as_posix(): sha256(p) for p in code_files},
                "source_manifest_sha256": sha256(PROJECT_ROOT / "records/source_manifest.json")}
    write_json(PROJECT_ROOT / "records/run_manifest.json", manifest)
    (PROJECT_ROOT / "records/requirements-lock.txt").write_text(
        "\n".join(f"{name}=={version}" for name, version in manifest["dependencies"].items())+"\n", encoding="utf-8")
    state = {"date_beijing": "2026-10-17", "status": "initial_milestone_passed",
             "completed": ["C-specific document and raw-data archive", "source SHA256 verification", "four-trip quality audit and split",
                           "coupled mechanical/electrical/two-node thermal reference model", "reference scenario E simulation",
                           "seven-parameter local sensitivity", "physical checks and step convergence"],
             "not_completed": ["verified CLTC and mandatory scenario A", "three-parameter calibration and confidence intervals",
                               "independent measurement validation", "optimized control and MPC", "final paper and submission video"],
             "reference_E_consumption_kwh_100km": summary["consumption_kwh_100km"],
             "verification": verification, "audit_records": audit["records"],
             "next": ["Resolve or obtain traceable CLTC sequence", "Inspect training-only residuals and capacity/OCV consistency",
                      "Implement three-parameter estimation and uncertainty without test leakage"],
             "important": ["Reference E is not CLTC A", "160Ah reference conflicts with 30kWh label", "No measured battery/cabin temperature"]}
    if write_latest:
        write_json(PROJECT_ROOT / "records/latest_state.json", state)
    table_rows = "\n".join(f"| {r['trip_id']} | {r['role']} | {r['records']} | {r['distance_km']:.3f} | {r['dt_max_s']:.1f} | {r['diagnostic_capacity_ah']:.2f} |" for r in audit["trip_summary"])
    report = f"""# C题阶段一实际进展

日期：2026年10月17日（北京时间，会话日期）。下列数值由`python run.py all`生成，不是最终比赛结果。

## 已完成

资料归档与哈希核验、6944条记录的数据质量审计、三问可复用的动力/电机/Rint电池/双热节点模型、参考工况试算、七参数灵敏度和物理/数值检查。

## 数据质量摘要

| 路程 | 用途 | 记录数 | 里程km | 最大间隔s | 粗算等效容量Ah |
|---|---|---:|---:|---:|---:|
{table_rows}

等效容量仅用于一致性诊断，不能据此宣称真实容量。原始时间间隔不固定，长缺口已在CSV计数；积分尚按梯形跨越观测缺口，未声称缺口内有真实测量。

## 首次参考工况结果

- 场景：E，附件3参考工况，25℃；**未核实CLTC，不代表必选场景A已完成**。
- 参数：未校准，容量{summary['capacity_ah']:.0f}Ah，初始SOC80%，电池/座舱25℃；平路、无太阳项，规则控制设定见配置与假设说明。
- 里程：{summary['distance_km']:.6f}km；净电池端耗电：{summary['net_energy_kwh']:.6f}kWh；折算百公里：{summary['consumption_kwh_100km']:.4f}kWh/100km。
- 终点SOC：{100*summary['soc_final']:.4f}%；电池最高温度：{summary['battery_max_c']:.4f}℃。
- 母线能量收支残差：{summary['dc_balance_residual_kwh']:.3e}kWh；电机功率收支最大残差：{summary['max_motor_balance_residual_w']:.3e}W。
- 步长0.5s→0.25s的能耗相对差：{verification['reference_convergence']['net_energy_relative_difference']*100:.6f}%；物理测试{verification['tests_run']}项全部通过。

这些结果验证的是参考模型实现和局部数值一致性，不是实车精度，也不是节能优化结果。热容对温度更明显，须结合灵敏度数据解释。注意主能耗采用电池端UI边界；本场景电池冷却未开启且端口负载固定，因此参考内阻对端电耗灵敏度为零，但会影响内阻热、OCV电功率积分与SOC下降，相关列已同时输出，不能得出“内阻不影响电池损耗”的结论。

## 查看文件

- [原始数据审计图](figures/data_audit.png)
- [参考工况综合图](figures/reference_E_overview.png)
- [DC能量分解](figures/reference_E_dc_energy.png)
- [七参数排序](figures/reference_E_sensitivity.png)
- [路程数据表](tables/data_audit_trips.csv)
- [参考模型数值摘要](tables/reference_E_summary.json)
- [物理与数值检查](tables/verification.json)

## 下一步

核实CLTC来源；只使用训练路程诊断容量/OCV及功率残差；实现Cd、Cr、Rref辨识与置信区间，之后冻结模型再推进节能策略。最终论文、正式视频和提交包尚未生成。
"""
    (PROJECT_ROOT / "results/阶段一进展.md").write_text(report, encoding="utf-8")
    return state


def run_all() -> dict:
    started = time.perf_counter()
    audit = audit_data()
    plot_audit()
    summary = run_reference()
    verification = run_checks()
    verify_sources()
    write_status(audit, summary, verification, time.perf_counter()-started, write_latest=False)
    from .progress import run_stage_two
    run_stage_two(checks=verification)
    from .stage3 import run_stage_three
    run_stage_three(checks=verification)
    from .stage4 import run_stage_four
    run_stage_four(checks=verification)
    from .stage5 import run_stage_five
    return run_stage_five(checks=verification)
