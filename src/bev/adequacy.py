"""训练内的容量/OCV与目标权重诊断；不改冻结模型，不使用600选择参数。"""
from __future__ import annotations

import copy
import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid
from scipy.optimize import least_squares

from .calibration import CalibrationProblem, forward, prepare_trip, protocol
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import load_curves


def run_adequacy(*, recompute: bool = False) -> dict:
    verify_sources()
    cfg = protocol()
    frozen = read_json(PROJECT_ROOT/"records/calibration_freeze.json")
    identity = {"protocol_sha256": frozen["protocol_sha256"], "parameter_sha256": frozen["calibrated_sha256"],
                "code_sha256": sha256(PROJECT_ROOT/"src/bev/adequacy.py"),
                "model_sha256": sha256(PROJECT_ROOT/"src/bev/model.py")}
    target = PROJECT_ROOT/"results/tables/model_adequacy.json"
    if target.exists() and not recompute:
        cached = read_json(target)
        if cached.get("fingerprint") == identity:
            return cached
    variations = [{"label": "frozen", "field": None, "factor": 1.},
                  {"label": "voltage_weight_half", "field": "voltage_scale_v", "factor": 2.},
                  {"label": "voltage_weight_double", "field": "voltage_scale_v", "factor": .5},
                  {"label": "soc_weight_half", "field": "soc_scale_fraction", "factor": 2.},
                  {"label": "soc_weight_double", "field": "soc_scale_fraction", "factor": .5},
                  {"label": "energy_weight_half", "field": "net_energy_relative_scale", "factor": 2.},
                  {"label": "energy_weight_double", "field": "net_energy_relative_scale", "factor": .5}]
    write_json(PROJECT_ROOT/"records/adequacy_protocol.json",
               {"train_only": True, "trips": cfg["fit_trip_ids"], "variations": variations,
                "selection_rule": "Diagnostic only. No replacement of frozen parameters or test-specific correction.",
                "interpretation": "Residual scale is divided by weight; halving scale doubles residual weight and quadruples squared weight."})
    curves = load_curves()
    diagnostic = []
    for tid in cfg["fit_trip_ids"]:
        trip = prepare_trip(tid, cfg)
        obs = trip.observation
        current = obs.discharge_current_a.to_numpy()
        z = obs.soc.to_numpy()
        ah = cumulative_trapezoid(current, obs.time_s, initial=0)/3600
        dz = z[0]-z
        inverse_q = float(ah@dz/(ah@ah))
        factor = np.interp(z, curves["resistance_soc"], curves["resistance_soc_factor"])/np.interp(.5, curves["resistance_soc"], curves["resistance_soc_factor"])
        factor *= 1+curves["resistance_temp_slope_per_c"]*(25-obs.ambient_c.to_numpy())
        # 仅诊断：此处观测SOC/I作为回归自变量，并非正向预测。
        design = np.column_stack([np.ones(len(obs)), z-.95, -factor*current])
        fit, _, _, _ = np.linalg.lstsq(design, obs.voltage_v, rcond=None)
        near_rest = np.abs(current)<2
        diagnostic.append({"trip_id": tid, "soc_min": float(z.min()), "soc_max": float(z.max()),
                           "coulomb_least_squares_effective_capacity_ah": 1/inverse_q,
                           "capacity_observation_rmse_pp": float(100*np.sqrt(np.mean((inverse_q*ah-dz)**2))),
                           "diagnostic_ocv_at_soc095_v": float(fit[0]), "diagnostic_ocv_slope_v_per_soc": float(fit[1]),
                           "diagnostic_ols_Rref_ohm": float(fit[2]), "near_zero_current_points": int(near_rest.sum()),
                           "near_zero_current_SOC_span": float(np.ptp(z[near_rest])) if near_rest.any() else None,
                           "warning": "Uses measured current/SOC and ambient as a proxy for battery temperature. Diagnostic regression only, not a forward validation or a true OCV measurement."})
    rows = []
    initial = np.array([frozen["parameters"][name] for name in cfg["parameters"]])
    for variation in variations:
        custom = copy.deepcopy(cfg)
        if variation["field"]:
            custom[variation["field"]] *= variation["factor"]
        problem = CalibrationProblem(custom)
        if variation["field"] is None:
            estimate, success = initial, True
        else:
            fit = least_squares(problem.residual, initial, bounds=(custom["lower_bounds"],custom["upper_bounds"]),
                                x_scale="jac", max_nfev=40, ftol=2e-5, xtol=2e-5, gtol=2e-5)
            estimate, success = fit.x, bool(fit.success)
        metrics = []
        for trip in problem.trips:
            _, out, pred = forward(trip, problem.parameters(estimate), custom["fit_max_step_s"])
            from scipy.integrate import trapezoid
            measured = trapezoid(trip.observation.battery_power_w, trip.observation.time_s)/3.6e6
            metrics.append((float(100*np.sqrt(np.mean((pred["soc"]-trip.observation.soc)**2))),
                            float(100*abs(out["net_energy_kwh"]-measured)/abs(measured))))
        rows.append({"variation": variation["label"], **dict(zip(cfg["parameters"], map(float, estimate))),
                     "train_soc_rmse_macro_pp": float(np.mean([v[0] for v in metrics])),
                     "train_energy_mape_pct": float(np.mean([v[1] for v in metrics])), "optimizer_success": success})
        print(f"Train weight diagnostic: {variation['label']}, Cr={estimate[1]:.6g}, R={estimate[2]:.6g}", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(PROJECT_ROOT/"results/tables/training_weight_stability.csv", index=False, encoding="utf-8-sig")
    from .workflow import setup_figures
    import matplotlib.pyplot as plt
    setup_figures()
    fig, axes = plt.subplots(1, 3, figsize=(13, 5))
    for ax, name, title in zip(axes, cfg["parameters"][:3], ["Cd", "Cr", "Rref / Ω"]):
        ax.plot(frame[name], np.arange(len(frame)), "o")
        ax.axvline(frozen["parameters"][name], color="grey", linestyle="--")
        ax.set_yticks(np.arange(len(frame)), frame.variation, fontsize=8)
        ax.set_xlabel(title)
        ax.grid(alpha=.25)
    fig.suptitle("仅训练路程的通道权重稳定性；各组重新拟合，不替换冻结模型")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/training_weight_stability.png")
    plt.close(fig)
    result = {"fingerprint": identity, "train_only": True, "trips": cfg["fit_trip_ids"],
              "battery_diagnostics": diagnostic, "weight_variations": rows,
              "frozen_unchanged": sha256(PROJECT_ROOT/"configs/calibrated.json")==frozen["calibrated_sha256"],
              "conclusion": "Only high-SOC observations constrain the OCV correction; low-SOC transfer cannot be repaired or certified from these training data. Weight sensitivity is structural evidence, not another confidence interval."}
    write_json(target, result)
    verify_sources()
    return result
