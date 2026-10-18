"""必选场景A与参数灵敏度，统一调用物理正向模型。"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .cycles import load_cltc, verify_cltc
from .io import PROJECT_ROOT, read_json, write_json
from .model import InitialState, load_parameters, simulate
from .workflow import setup_figures


def run_scenario_a() -> dict:
    source = verify_cltc()
    cycle, _ = load_cltc()
    parameters = load_parameters()
    trace, out = simulate(cycle, parameters, scenario="A_CLTC_P_25C_reference", verified_cltc=True)
    out["cltc_verification_level"] = source["verification_level"]
    out["cltc_official_file_directly_checked"] = False
    trace.to_csv(PROJECT_ROOT / "results/timeseries/scenario_A_reference.csv", index=False, encoding="utf-8-sig")
    write_json(PROJECT_ROOT / "results/tables/scenario_A_summary.json", out)
    _, finer = simulate(cycle, parameters, max_step_s=.25, scenario=out["scenario"], verified_cltc=True)
    convergence = {"coarse_step_s": .5, "fine_step_s": .25,
                   "energy_relative_difference": abs(out["net_energy_kwh"]-finer["net_energy_kwh"])/abs(finer["net_energy_kwh"]),
                   "soc_final_difference_pp": 100*abs(out["soc_final"]-finer["soc_final"]),
                   "battery_max_difference_c": abs(out["battery_max_c"]-finer["battery_max_c"])}
    convergence["passed"] = convergence["energy_relative_difference"]<.01 and convergence["battery_max_difference_c"]<.1
    write_json(PROJECT_ROOT / "results/tables/scenario_A_convergence.json", convergence)
    if not convergence["passed"]:
        raise RuntimeError("CLTC步长核查失败")
    setup_figures()
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, x, y, ylabel in [
        (axes[0, 0], cycle.time_s, cycle.speed_m_s*3.6, "车速 / km/h"),
        (axes[0, 1], trace.time_s, trace.soc*100, "SOC / %"),
        (axes[1, 1], trace.time_s, trace.battery_w/1000, "电池端功率 / kW")]:
        ax.plot(x, y, lw=1)
        ax.set_ylabel(ylabel)
    axes[1, 0].plot(trace.time_s, trace.battery_c, label="电池")
    axes[1, 0].plot(trace.time_s, trace.cabin_c, label="座舱")
    axes[1, 0].set_ylabel("温度 / ℃")
    axes[1, 0].legend()
    for ax in axes.flat:
        ax.set_xlabel("时间 / s")
        ax.grid(alpha=.25)
    fig.suptitle("场景A：25℃，公开CLTC-P复现序列（两来源逐点核对）；参考参数160Ah")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/scenario_A_overview.png")
    plt.close(fig)
    branches = [{"branch": "牵引DC输入", "energy_kwh": out["traction_dc_energy_kwh"], "level": "DC_bus"},
                {"branch": "电机回流DC", "energy_kwh": -out["regen_dc_energy_kwh"], "level": "DC_bus"}]
    for key, label in [("low_voltage_w", "低压附件"), ("ac_w", "空调"), ("heater_w", "加热"), ("cooler_w", "电池冷却")]:
        branches.append({"branch": label, "energy_kwh": out["energy_kwh"][key], "level": "DC_bus"})
    for row in branches:
        row["share_of_net_terminal_pct"] = 100*row["energy_kwh"]/out["net_energy_kwh"]
    pd.DataFrame(branches).to_csv(PROJECT_ROOT / "results/tables/scenario_A_dc_energy.csv", index=False, encoding="utf-8-sig")
    losses = [{"component": key, "energy_kwh": out["energy_kwh"][key],
               "accounting": "Internal loss breakdown; do not add again to DC bus branches."}
              for key in ["gear_loss_w", "motor_loss_w", "inverter_loss_w", "joule_heat_w", "friction_brake_w"]]
    pd.DataFrame(losses).to_csv(PROJECT_ROOT / "results/tables/scenario_A_losses.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.barh([r["branch"] for r in branches], [r["energy_kwh"] for r in branches], color="#287aa0")
    ax.axvline(0, color="grey", lw=.8)
    ax.set_xlabel("能量 / kWh；回流为负，支路之和=净端电能")
    ax.set_title("场景A的DC母线能量分解；内部损耗另表列示")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/scenario_A_energy.png")
    plt.close(fig)
    keys = [("drag_coefficient", "空气阻力系数"), ("rolling_coefficient", "滚动阻力系数"),
            ("resistance_ref_ohm", "电池参考内阻"), ("mass_kg", "整车质量"),
            ("low_voltage_w", "低压附件功率"), ("cooling_cop", "制冷COP"),
            ("battery_heat_capacity_j_k", "电池热容")]
    rows = []
    for eps in [.05, .1]:
        for key, label in keys:
            outputs = [simulate(cycle, {**parameters, key: parameters[key]*(1+sign*eps)},
                                scenario=out["scenario"], verified_cltc=True)[1] for sign in [-1, 1]]
            lo, hi = outputs
            rows.append({"parameter": key, "label": label, "fractional_perturbation": eps,
                         "normalized_energy_sensitivity": (hi["net_energy_kwh"]-lo["net_energy_kwh"])/(2*eps*out["net_energy_kwh"]),
                         "normalized_ocv_energy_sensitivity": (hi["ocv_electric_energy_kwh"]-lo["ocv_electric_energy_kwh"])/(2*eps*out["ocv_electric_energy_kwh"]),
                         "normalized_soc_draw_sensitivity": (lo["soc_final"]-hi["soc_final"])/(2*eps*(out["soc_initial"]-out["soc_final"])),
                         "battery_max_slope_c_per_fraction": (hi["battery_max_c"]-lo["battery_max_c"])/(2*eps)})
    sensitivity = pd.DataFrame(rows)
    sensitivity["energy_rank"] = sensitivity.groupby("fractional_perturbation").normalized_energy_sensitivity.transform(
        lambda column: column.abs().rank(ascending=False, method="min")).astype(int)
    sensitivity.to_csv(PROJECT_ROOT / "results/tables/scenario_A_sensitivity.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(9, 5))
    for index, eps in enumerate([.05, .1]):
        selected = sensitivity[sensitivity.fractional_perturbation.eq(eps)].set_index("parameter").loc[[k for k, _ in keys]]
        ax.barh(np.arange(len(keys))+(index-.5)*.32, selected.normalized_energy_sensitivity,
                height=.3, label=f"±{eps*100:.0f}%")
    ax.set_yticks(np.arange(len(keys)), [label for _, label in keys])
    ax.set_xlabel("净端电耗标准化局部灵敏度")
    ax.set_title("CLTC七参数灵敏度：同时检查扰动幅度稳定性")
    ax.legend()
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/scenario_A_sensitivity.png")
    plt.close(fig)
    # 明确保留容量争议的另一个口径；不可据此称附件笔误已纠正。
    _, capacity80 = simulate(cycle, {**parameters, "capacity_ah": 80.0}, scenario="A_80Ah_energy_equivalent_comparison", verified_cltc=True)
    write_json(PROJECT_ROOT / "results/tables/scenario_A_capacity80_comparison.json", capacity80)
    calibrated_path = PROJECT_ROOT / "configs/calibrated.json"
    if calibrated_path.exists():
        calibrated_trace, calibrated = simulate(cycle, read_json(calibrated_path), scenario="A_training_calibrated_extrapolation", verified_cltc=True)
        calibrated["limitation"] = "25C is outside measured ambient range; thermal and this transfer accuracy are unvalidated."
        write_json(PROJECT_ROOT / "results/tables/scenario_A_calibrated_summary.json", calibrated)
        calibrated_trace.to_csv(PROJECT_ROOT / "results/timeseries/scenario_A_calibrated.csv", index=False, encoding="utf-8-sig")
    return {"summary": out, "convergence": convergence, "source": source}
