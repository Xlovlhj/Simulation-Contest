"""参数辨识与条件性不确定性；AI辅助信息见records/ai_usage.md。

核心正向计算复用model.simulate；不逐时输入测得SOC/I/U。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid, trapezoid
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares

from .data import read_trips
from .io import PROJECT_ROOT, read_json, sha256, verify_sources, write_json
from .model import InitialState, InfeasibleSimulation, load_parameters, simulate


@dataclass
class TripData:
    trip_id: int
    observation: pd.DataFrame
    cycle: pd.DataFrame
    initial: InitialState
    preprocessing: dict


def protocol() -> dict:
    return read_json(PROJECT_ROOT / "configs/calibration_protocol.json")


def prepare_trip(trip_id: int, config: dict | None = None) -> TripData:
    cfg = config or protocol()
    observation = read_trips().query("trip_id == @trip_id").copy()
    if observation.empty:
        raise ValueError(f"不存在路程{trip_id}")
    times = observation.time_s.to_numpy()
    grid = np.r_[np.arange(times[0], times[-1], cfg["input_grid_s"]), times[-1]]
    raw_speed = np.interp(grid, times, observation.speed_m_s)
    speed = gaussian_filter1d(raw_speed, cfg["speed_gaussian_sigma_s"]/cfg["input_grid_s"], mode="nearest")
    cycle = pd.DataFrame({"time_s": grid, "speed_m_s": speed})
    for key in ["ambient_c", "ac_w", "heater_w"]:
        cycle[key] = np.interp(grid, times, observation[key])
    raw_distance = float(trapezoid(observation.speed_m_s, times)/1000)
    conditioned_distance = float(trapezoid(speed, grid)/1000)
    initial = InitialState(soc=float(observation.soc.iloc[0]), battery_c=float(observation.ambient_c.iloc[0]),
                           cabin_c=float(observation.ambient_c.iloc[0]))
    return TripData(trip_id, observation, cycle, initial,
                    {"trip_id": trip_id, "grid_s": cfg["input_grid_s"], "gaussian_sigma_s": cfg["speed_gaussian_sigma_s"],
                     "raw_distance_km": raw_distance, "conditioned_distance_km": conditioned_distance,
                     "distance_relative_change": (conditioned_distance-raw_distance)/raw_distance,
                     "battery_initial_assumed_environment_c": initial.battery_c,
                     "long_gap_policy": "Interpolate for model input; observed long gaps remain flagged in audit."})


def forward(trip: TripData, parameters: dict, step_s: float) -> tuple[pd.DataFrame, dict, dict]:
    trace, summary = simulate(trip.cycle, parameters, trip.initial, max_step_s=step_s,
                              scenario=f"D_trip_{trip.trip_id}")
    observations = trip.observation
    times = observations.time_s.to_numpy()
    prediction = {"soc": np.interp(times, np.r_[trip.cycle.time_s.iloc[0], trace.time_s],
                                    np.r_[trip.initial.soc, trace.soc]),
                  "voltage_v": np.interp(times, (trace.time_start_s+trace.time_s)/2, trace.voltage_v)}
    return trace, summary, prediction


def residual_parts(trip: TripData, parameters: dict, cfg: dict) -> tuple[list[np.ndarray], list[dict]]:
    trace, _, prediction = forward(trip, parameters, cfg["fit_max_step_s"])
    obs = trip.observation
    t = obs.time_s.to_numpy()
    # 观测采样频率不等，拟合按时间支撑权重；论文仍另报通常样本RMSE。
    weights = np.r_[np.diff(t)/2, 0.0] + np.r_[0.0, np.diff(t)/2]
    weights = np.sqrt(weights/weights.sum())
    soc_error = (prediction["soc"]-obs.soc.to_numpy())/cfg["soc_scale_fraction"]*weights
    voltage_error = (prediction["voltage_v"]-obs.voltage_v.to_numpy())/cfg["voltage_scale_v"]*weights
    edges = np.r_[np.arange(t[0], t[-1], cfg["power_window_s"]), t[-1]]
    measured_cumulative = cumulative_trapezoid(obs.battery_power_w, t, initial=0)
    predicted_cumulative = np.r_[0.0, np.cumsum(trace.battery_w*trace.dt_s)]
    measured_window = np.diff(np.interp(edges, t, measured_cumulative))/np.diff(edges)
    predicted_window = np.diff(np.interp(edges, np.r_[t[0], trace.time_s], predicted_cumulative))/np.diff(edges)
    power_error = (predicted_window-measured_window)/cfg["power_window_scale_w"]/np.sqrt(len(measured_window))
    actual_energy_j = measured_cumulative[-1]
    net_error = np.array([(predicted_cumulative[-1]-actual_energy_j) /
                          (cfg["net_energy_relative_scale"]*abs(actual_energy_j))])
    parts = [soc_error, voltage_error, power_error, net_error]
    metadata = [{"trip_id": trip.trip_id, "channel": "soc", "time_s": t.tolist()},
                {"trip_id": trip.trip_id, "channel": "voltage", "time_s": t.tolist()},
                {"trip_id": trip.trip_id, "channel": "power_window", "time_s": ((edges[1:]+edges[:-1])/2).tolist()},
                {"trip_id": trip.trip_id, "channel": "net_energy", "time_s": [float((t[0]+t[-1])/2)],
                 "contribution_time_s": ((edges[1:]+edges[:-1])/2).tolist(),
                 "contribution_residual": ((predicted_window-measured_window)*np.diff(edges) /
                                           (cfg["net_energy_relative_scale"]*abs(actual_energy_j))).tolist()}]
    return parts, metadata


def training_diagnostics() -> dict:
    cfg = protocol()
    rows = []
    numerator = denominator = 0.0
    for trip_id in cfg["fit_trip_ids"]:
        trip = prepare_trip(trip_id, cfg)
        obs = trip.observation
        ah = cumulative_trapezoid(obs.discharge_current_a, obs.time_s, initial=0)/3600
        delta_soc = trip.initial.soc-obs.soc.to_numpy()
        numerator += float(ah@delta_soc/len(obs))
        denominator += float(ah@ah/len(obs))
        params = load_parameters()
        _, out, predicted = forward(trip, params, cfg["fit_max_step_s"])
        rows.append({**trip.preprocessing, "reference_soc_rmse_pp": float(100*np.sqrt(np.mean((predicted["soc"]-obs.soc)**2))),
                     "reference_voltage_rmse_v": float(np.sqrt(np.mean((predicted["voltage_v"]-obs.voltage_v)**2))),
                     "reference_energy_kwh": out["net_energy_kwh"],
                     "measured_energy_kwh": float(trapezoid(obs.battery_power_w, obs.time_s)/3.6e6)})
    result = {"train_only": True, "trip_ids": cfg["fit_trip_ids"], "shared_coulomb_capacity_initial_ah": denominator/numerator,
              "initial_capacity_not_true_rating": True, "trip_diagnostics": rows}
    write_json(PROJECT_ROOT / "results/tables/training_diagnostics.json", result)
    pd.DataFrame(rows).to_csv(PROJECT_ROOT / "results/tables/training_diagnostics.csv", index=False, encoding="utf-8-sig")
    return result


class CalibrationProblem:
    def __init__(self, cfg: dict):
        self.config = cfg
        self.base = load_parameters()
        self.trips = [prepare_trip(tid, cfg) for tid in cfg["fit_trip_ids"]]
        self.names = cfg["parameters"]
        self.calls = 0
        self.metadata = []
        self.residual_size = 0

    def parameters(self, vector: np.ndarray) -> dict:
        return {**self.base, **dict(zip(self.names, map(float, vector))), "parameter_status": f"training_calibrated_{self.config['experiment_version']}"}

    def residual(self, vector: np.ndarray) -> np.ndarray:
        self.calls += 1
        chunks, metadata = [], []
        try:
            for trip in self.trips:
                parts, meta = residual_parts(trip, self.parameters(vector), self.config)
                chunks.extend(parts)
                metadata.extend(meta)
        except InfeasibleSimulation:
            if self.residual_size == 0:
                raise
            return np.full(self.residual_size, 1e4)
        output = np.concatenate(chunks)
        self.metadata, self.residual_size = metadata, len(output)
        return output


def fit_parameters(*, artifact_root: Path | None = None) -> dict:
    verify_sources()
    started = time.perf_counter()
    cfg = protocol()
    output_root = PROJECT_ROOT if artifact_root is None else artifact_root
    frozen_path = PROJECT_ROOT / "records/calibration_freeze.json"
    # 冻结后的重复运行只复用参数；不覆盖已评估的实验及其哈希。
    if frozen_path.exists() and artifact_root is None:
        saved_freeze = read_json(frozen_path)
        if saved_freeze["protocol_sha256"] == sha256(PROJECT_ROOT / "configs/calibration_protocol.json"):
            if saved_freeze["calibrated_sha256"] != sha256(PROJECT_ROOT / "configs/calibrated.json"):
                raise ValueError("冻结参数文件已改动")
            return saved_freeze
    diagnostic = training_diagnostics()
    problem = CalibrationProblem(cfg)
    lower, upper = np.array(cfg["lower_bounds"]), np.array(cfg["upper_bounds"])
    initial = np.array([0.28, 0.01, 0.1, diagnostic["shared_coulomb_capacity_initial_ah"], -9.0])
    problem.residual(initial)
    nuisance = least_squares(lambda y: problem.residual(np.r_[initial[:3], y]), initial[3:],
                             bounds=(lower[3:], upper[3:]), x_scale="jac", max_nfev=cfg["max_nfev"],
                             ftol=2e-5, xtol=2e-5, gtol=2e-5)
    nuisance_vector = np.r_[initial[:3], nuisance.x]
    runs = []
    for start in [nuisance_vector, np.r_[0.42, 0.007, 0.06, nuisance.x]]:
        fit = least_squares(problem.residual, start, bounds=(lower, upper), x_scale="jac",
                            max_nfev=cfg["max_nfev"], ftol=2e-5, xtol=2e-5, gtol=2e-5)
        runs.append(fit)
        print(f"Training fit: cost={fit.cost:.6g}, nfev={fit.nfev}, params={fit.x.tolist()}", flush=True)
    best = min(runs, key=lambda value: value.cost)
    residual = problem.residual(best.x)
    write_json(output_root / "configs/calibrated.json", problem.parameters(best.x))
    write_json(output_root / "configs/nuisance_only.json", problem.parameters(nuisance_vector))
    (output_root / "results/tables").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output_root / "results/tables/calibration_linearization.npz", jacobian=best.jac,
                        residual=residual, estimate=best.x, lower=lower, upper=upper)
    write_json(output_root / "records/calibration_residual_metadata.json", {"parts": problem.metadata})
    result = {"status": "training_frozen_before_retrospective_evaluation", "experiment_version": cfg["experiment_version"],
              "test_is_untouched": cfg["test_is_untouched"], "train_trip_ids": cfg["fit_trip_ids"],
              "parameters": dict(zip(problem.names, map(float, best.x))),
              "nuisance_only_parameters": dict(zip(problem.names, map(float, nuisance_vector))),
              "cost": float(best.cost), "success": bool(best.success), "optimizer_message": str(best.message),
              "active_bounds": dict(zip(problem.names, map(int, best.active_mask))),
              "residual_calls": problem.calls, "elapsed_s": time.perf_counter()-started,
              "multistart_costs": [float(run.cost) for run in runs],
              "protocol_sha256": sha256(PROJECT_ROOT / "configs/calibration_protocol.json"),
              "calibrated_sha256": sha256(output_root / "configs/calibrated.json"),
              "note": "V2 net-energy residual was added due to V1 training energy failure. Trip600 has already been viewed in V1; V2 evaluation is retrospective. Nuisance-only comparison separates structure alignment from three-parameter fitting."}
    write_json(output_root / "records/calibration_freeze.json", result)
    return result


def reproduce_fit() -> dict:
    """完整重算训练辨识，输出隔离保存，保留已冻结实验和评价记录。"""
    destination = PROJECT_ROOT / "records/experiments/reproduction_v2"
    recomputed = fit_parameters(artifact_root=destination)
    frozen = read_json(PROJECT_ROOT / "records/calibration_freeze.json")
    differences = {name: recomputed["parameters"][name]-value for name, value in frozen["parameters"].items()}
    result = {"export_path": destination.relative_to(PROJECT_ROOT).as_posix(), "parameter_differences": differences,
              "parameters_reproduced_within_1e_6": all(abs(value)<1e-6 for value in differences.values()),
              "frozen_parameters_unchanged": sha256(PROJECT_ROOT / "configs/calibrated.json")==frozen["calibrated_sha256"]}
    write_json(destination / "comparison.json", result)
    if not result["parameters_reproduced_within_1e_6"] or not result["frozen_parameters_unchanged"]:
        raise RuntimeError("训练重现与原冻结不一致；结果已隔离，请检查环境和代码")
    return result


def confidence_analysis() -> dict:
    """训练残差的局部线性块wild近似；不把搜索边界当区间、不裁剪抽样。"""
    cfg = protocol()
    frozen = read_json(PROJECT_ROOT / "records/calibration_freeze.json")
    if frozen["protocol_sha256"] != sha256(PROJECT_ROOT / "configs/calibration_protocol.json"):
        raise ValueError("辨识协议已改变，不能复用旧线性化")
    saved = np.load(PROJECT_ROOT / "results/tables/calibration_linearization.npz")
    jac, residual, estimate = saved["jacobian"], saved["residual"], saved["estimate"]
    lower, upper = saved["lower"], saved["upper"]
    scale = upper-lower
    scaled_jac = jac*scale
    _, singular, vh = np.linalg.svd(scaled_jac, full_matrices=False)
    inverse = np.linalg.pinv(scaled_jac, rcond=1e-10)
    parts = read_json(PROJECT_ROOT / "records/calibration_residual_metadata.json")["parts"]
    rng = np.random.default_rng(cfg["random_seed"])
    repetitions = cfg["linearized_block_wild_repetitions"]
    rows, correlations = [], {}
    for length in cfg["confidence_block_lengths_s"]:
        # 同一路程同一时间块的所有通道使用同一乘子，保留跨通道相关。
        keys = [(part["trip_id"], int(t//length)) for part in parts
                for t in part.get("contribution_time_s", part["time_s"])]
        unique = {key: i for i, key in enumerate(sorted(set(keys)))}
        if sum(len(part["time_s"]) for part in parts) != len(residual):
            raise ValueError("残差索引与线性化维度不相符")
        # 先按块汇总pinv(J)r，再抽符号；避免构造N观测×1000的稠密矩阵。
        influence = np.zeros((len(estimate), len(unique)))
        cursor = 0
        for part in parts:
            if part["channel"] == "net_energy":
                # 聚合电能残差拆回时间贡献，不能把整段总误差假定在中点发生。
                for t, contribution in zip(part["contribution_time_s"], part["contribution_residual"]):
                    block = unique[(part["trip_id"], int(t//length))]
                    influence[:, block] += inverse[:, cursor]*contribution
            else:
                for j, t in enumerate(part["time_s"]):
                    block = unique[(part["trip_id"], int(t//length))]
                    influence[:, block] += inverse[:, cursor+j]*residual[cursor+j]
            cursor += len(part["time_s"])
        signs = rng.choice([-1.0, 1.0], size=(len(unique), repetitions))
        draws = estimate[:, None] - scale[:, None]*(influence@signs)
        lo, hi = np.quantile(draws, [0.025, 0.975], axis=1)
        correlations[str(length)] = np.corrcoef(draws).tolist()
        for i, name in enumerate(cfg["parameters"]):
            rows.append({"parameter": name, "estimate": float(estimate[i]), "block_length_s": length,
                         "ci_low": float(lo[i]), "ci_high": float(hi[i]), "confidence_level": .95,
                         "bootstrap_sd": float(np.std(draws[i], ddof=1)),
                         "outside_fit_bounds_fraction": float(np.mean((draws[i]<lower[i]) | (draws[i]>upper[i]))),
                         "blocks": len(unique), "repetitions": repetitions,
                         "method": "local_linearized_block_wild_not_refit"})
    frame = pd.DataFrame(rows)
    frame.to_csv(PROJECT_ROOT / "results/tables/parameter_intervals.csv", index=False, encoding="utf-8-sig")
    primary = frame[frame.block_length_s.eq(cfg["confidence_primary_block_s"])]
    result = {"confidence_level": .95, "primary_block_s": cfg["confidence_primary_block_s"],
              "method": "local linearized Rademacher block wild approximation; no nonlinear bootstrap refitting",
              "primary_intervals": primary.to_dict("records"), "all_intervals": rows,
              "scaled_jacobian_singular_values": singular.tolist(),
              "scaled_jacobian_rank": int(np.linalg.matrix_rank(scaled_jac)),
              "scaled_jacobian_condition": float(singular[0]/singular[-1]),
              "weakest_direction_in_bound_scaled_coordinates": dict(zip(cfg["parameters"], vh[-1].tolist())),
              "correlations_by_block_length": correlations,
              "limitations": ["Only two training trips; blocks do not establish population coverage across vehicles or weather.",
                              "Local derivative and fixed thermal/OCV structure; structural mismatch is not covered.",
                              "Regen threshold events can make the objective nonsmooth; linearized intervals are conditional.",
                              "No clipping to search bounds; physical-range crossings indicate weak inference."]}
    write_json(PROJECT_ROOT / "results/tables/parameter_uncertainty.json", result)
    return result


def evaluate_calibration() -> dict:
    """在已冻结设置下评估全部路程；测试结果不反馈至优化器。"""
    verify_sources()
    cfg = protocol()
    freeze_path = PROJECT_ROOT / "records/calibration_freeze.json"
    freeze = read_json(freeze_path)
    if freeze["protocol_sha256"] != sha256(PROJECT_ROOT / "configs/calibration_protocol.json") or freeze["calibrated_sha256"] != sha256(PROJECT_ROOT / "configs/calibrated.json"):
        raise ValueError("冻结参数或协议已变化，请明确新实验版本")
    # 保存第一次测试评估所对应的冻结摘要，重现允许，悄悄改参数后再测不允许。
    evaluation_path = PROJECT_ROOT / f"records/test_evaluation_{cfg['experiment_version']}.json"
    if evaluation_path.exists() and read_json(evaluation_path)["freeze_sha256"] != sha256(freeze_path):
        raise ValueError("测试集已用于另一冻结版本；请先记录实验更改原因并显式建立新版本")
    variants = {"reference": load_parameters(),
                "nuisance_only": read_json(PROJECT_ROOT / "configs/nuisance_only.json"),
                "joint_calibrated": read_json(PROJECT_ROOT / "configs/calibrated.json")}
    roles = {tid: role for role, ids in [("train", cfg["fit_trip_ids"]), ("validation", cfg["validation_trip_ids"]),
                                        ("test_retrospective", cfg["test_trip_ids"])] for tid in ids}
    rows, convergence = [], []
    for tid, role in roles.items():
        trip = prepare_trip(tid, cfg)
        obs = trip.observation
        t = obs.time_s.to_numpy()
        actual_energy = float(trapezoid(obs.battery_power_w, t)/3.6e6)
        predictions = obs[["time_s", "soc", "voltage_v", "battery_power_w"]].copy()
        for name, p in variants.items():
            trace, out, pred = forward(trip, p, cfg["final_max_step_s"])
            soc_error = pred["soc"]-obs.soc.to_numpy()
            row = {"trip_id": tid, "role": role, "variant": name, "records": len(obs),
                   "soc_rmse_pp": float(100*np.sqrt(np.mean(soc_error**2))),
                   "soc_time_weighted_rmse_pp": float(100*np.sqrt(trapezoid(soc_error**2, t)/(t[-1]-t[0]))),
                   "voltage_rmse_v": float(np.sqrt(np.mean((pred["voltage_v"]-obs.voltage_v.to_numpy())**2))),
                   "measured_net_energy_kwh": actual_energy, "predicted_net_energy_kwh": out["net_energy_kwh"],
                   "energy_ape_pct": float(100*abs(out["net_energy_kwh"]-actual_energy)/abs(actual_energy)),
                   "predicted_soc_final": out["soc_final"], "measured_soc_final": float(obs.soc.iloc[-1]),
                   **trip.preprocessing}
            rows.append(row)
            predictions[f"soc_{name}"] = pred["soc"]
            predictions[f"voltage_{name}_v"] = pred["voltage_v"]
            if name == "joint_calibrated":
                trace.to_csv(PROJECT_ROOT / f"results/timeseries/trip_{tid}_calibrated.csv", index=False, encoding="utf-8-sig")
                _, coarse, cpred = forward(trip, p, cfg["fit_max_step_s"])
                convergence.append({"trip_id": tid, "coarse_step_s": cfg["fit_max_step_s"], "fine_step_s": cfg["final_max_step_s"],
                                    "energy_relative_difference": abs(coarse["net_energy_kwh"]-out["net_energy_kwh"])/abs(out["net_energy_kwh"]),
                                    "soc_prediction_max_abs_difference_pp": float(100*np.max(np.abs(cpred["soc"]-pred["soc"])))})
        predictions.to_csv(PROJECT_ROOT / f"results/timeseries/trip_{tid}_predictions.csv", index=False, encoding="utf-8-sig")
    frame = pd.DataFrame(rows)
    groups = []
    for (role, variant), part in frame.groupby(["role", "variant"], sort=False):
        groups.append({"role": role, "variant": variant, "trips": part.trip_id.tolist(),
                       "energy_mape_pct": float(part.energy_ape_pct.mean()),
                       "soc_rmse_macro_pp": float(part.soc_rmse_pp.mean()),
                       "soc_rmse_pooled_pp": float(np.sqrt(np.average(part.soc_rmse_pp**2, weights=part.records)))})
    frame.to_csv(PROJECT_ROOT / "results/tables/calibration_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(groups).to_csv(PROJECT_ROOT / "results/tables/calibration_group_metrics.csv", index=False, encoding="utf-8-sig")
    result = {"frozen_before_this_evaluation": True, "test_is_untouched": cfg["test_is_untouched"],
              "freeze_sha256": sha256(freeze_path), "trip_metrics": rows,
              "group_metrics": groups, "step_convergence": convergence,
              "energy_definition": "Net integral of terminal voltage times discharge-positive current; group MAPE is the mean of per-trip APEs.",
              "observed_auxiliary_inputs": ["ac_w", "heater_w"],
              "prediction_inputs": "Conditioned speed, ambient, recorded AC/heater load, first SOC and assumed initial temperatures only.",
              "limitations": ["Measured auxiliary power is supplied as an input; this is not a forecast from speed/ambient alone.",
                              "Trip600 was previously viewed in V1; this V2 evaluation is retrospective, not confirmatory.",
                              "No measured battery/cabin temperature; thermal accuracy is unvalidated.",
                              "Net measured energy uses trapezoidal interpolation across the flagged observation gaps."]}
    write_json(evaluation_path, {"date_beijing": "2026-10-17", "freeze_sha256": result["freeze_sha256"],
                                 "protocol_sha256": freeze["protocol_sha256"], "test_trip_ids": cfg["test_trip_ids"],
                                 "rule": "Frozen deterministic reproduction only; test outcomes must not be used to select settings."})
    write_json(PROJECT_ROOT / "results/tables/calibration_evaluation.json", result)
    verify_sources()
    return result


def plot_calibration() -> None:
    from .workflow import setup_figures
    import matplotlib.pyplot as plt
    setup_figures()
    cfg = protocol()
    tids = cfg["fit_trip_ids"] + cfg["validation_trip_ids"] + cfg["test_trip_ids"]
    fig, axes = plt.subplots(2, len(tids), figsize=(16, 7), sharex="col")
    labels = {"reference": "原参考", "nuisance_only": "容量/电压修正", "joint_calibrated": "联合校准"}
    for column, tid in enumerate(tids):
        frame = pd.read_csv(PROJECT_ROOT / f"results/timeseries/trip_{tid}_predictions.csv")
        axes[0, column].plot(frame.time_s, frame.soc*100, "k", lw=1.7, label="实测")
        axes[1, column].plot(frame.time_s, frame.voltage_v, "k", lw=1, alpha=.65, label="实测")
        for name, label in labels.items():
            axes[0, column].plot(frame.time_s, frame[f"soc_{name}"]*100, lw=1, label=label)
            axes[1, column].plot(frame.time_s, frame[f"voltage_{name}_v"], lw=.8, label=label)
        role = "训练" if tid in cfg["fit_trip_ids"] else "验证" if tid in cfg["validation_trip_ids"] else "回顾检验"
        axes[0, column].set_title(f"{tid}（{role}）")
        axes[0, column].set_ylabel("SOC / %")
        axes[1, column].set_ylabel("端电压 / V")
        axes[1, column].set_xlabel("时间 / s")
        for ax in axes[:, column]:
            ax.grid(alpha=.25)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("冻结模型的前向预测；温度初值假定等于环境，实测空调/加热为已知输入")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/calibration_predictions.png")
    plt.close(fig)
    uncertainty = read_json(PROJECT_ROOT / "results/tables/parameter_uncertainty.json")
    primary = pd.DataFrame(uncertainty["primary_intervals"]).iloc[:3]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    for ax, (_, row), label in zip(axes, primary.iterrows(), ["空气阻力系数 Cd", "滚动阻力系数 Cr", "参考内阻 / Ω"]):
        ax.errorbar(row.estimate, 0, xerr=[[row.estimate-row.ci_low], [row.ci_high-row.estimate]], fmt="o", capsize=5)
        ax.axvline(0, color="grey", lw=.8)
        ax.set_xlabel(label)
        ax.set_yticks([])
        ax.grid(axis="x", alpha=.25)
    fig.suptitle("95%条件性近似区间（40s块wild局部线性化；非全局真实性保证）")
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT / "results/figures/parameter_intervals.png")
    plt.close(fig)
