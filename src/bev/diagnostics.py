"""仅用训练路程的Cr剖面诊断；成本曲线不冒充统计置信区间。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from .calibration import CalibrationProblem, protocol
from .io import PROJECT_ROOT, read_json, sha256, write_json


def run_training_profile(*, recompute: bool = True) -> dict:
    cfg = protocol()
    frozen = read_json(PROJECT_ROOT/"records/calibration_freeze.json")
    if frozen["protocol_sha256"] != sha256(PROJECT_ROOT/"configs/calibration_protocol.json"):
        raise ValueError("协议与冻结版本不一致")
    fingerprint = {"protocol_sha256": frozen["protocol_sha256"], "parameters_sha256": frozen["calibrated_sha256"],
                   "model_sha256": sha256(PROJECT_ROOT/"src/bev/model.py"),
                   "calibration_code_sha256": sha256(PROJECT_ROOT/"src/bev/calibration.py")}
    cached_path = PROJECT_ROOT/"results/tables/Cr_training_profile.json"
    if not recompute and cached_path.exists():
        cached = read_json(cached_path)
        if cached.get("fingerprint") == fingerprint:
            return cached
    problem = CalibrationProblem(cfg)
    x = np.array([frozen["parameters"][name] for name in cfg["parameters"]])
    free = np.array([0,2,3,4])
    lo, hi = np.array(cfg["lower_bounds"]), np.array(cfg["upper_bounds"])
    grid = sorted(set([.003, float(x[1]), .004, .006, .008, .012, .020]))
    write_json(PROJECT_ROOT/"records/profile_protocol.json",
               {"train_only": True, "fit_trip_ids": cfg["fit_trip_ids"], "parameter": "rolling_coefficient",
                "grid": grid, "rule": "Fix Cr and reoptimize other four parameters from frozen solution; diagnostic only, do not select parameters using evaluation trips.",
                "statistical_CI": False})
    rows = []
    for fixed in grid:
        def expand(y):
            z = x.copy()
            z[1], z[free] = fixed, y
            return z
        fit = least_squares(lambda y: problem.residual(expand(y)), x[free], bounds=(lo[free], hi[free]),
                            x_scale="jac", max_nfev=40, ftol=2e-5, xtol=2e-5, gtol=2e-5)
        optimum = expand(fit.x)
        rows.append({"fixed_Cr": fixed, "training_cost": float(fit.cost), "success": bool(fit.success),
                     "nfev": int(fit.nfev), **dict(zip(cfg["parameters"], map(float, optimum)))})
        print(f"Training Cr profile: Cr={fixed:.6g}, cost={fit.cost:.6g}", flush=True)
    frame = pd.DataFrame(rows)
    frame["delta_cost_from_frozen"] = frame.training_cost-frozen["cost"]
    frame.to_csv(PROJECT_ROOT/"results/tables/Cr_training_profile.csv", index=False, encoding="utf-8-sig")
    from .workflow import setup_figures
    import matplotlib.pyplot as plt
    setup_figures()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(frame.fixed_Cr, frame.training_cost, "o-")
    ax.axvline(x[1], color="#bd7133", linestyle="--", label="冻结Cr")
    ax.set_xlabel("固定滚动阻力系数 Cr")
    ax.set_ylabel("重新优化其余四参数后的训练目标")
    ax.set_title("训练内Cr剖面：诊断参数耦合，未作置信水平换算")
    ax.legend()
    ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(PROJECT_ROOT/"results/figures/Cr_training_profile.png")
    plt.close(fig)
    result = {"train_only": True, "fingerprint": fingerprint, "trip_ids": cfg["fit_trip_ids"], "rows": frame.to_dict("records"),
              "frozen_cost": frozen["cost"], "minimum_profile_cost": float(frame.training_cost.min()),
              "freeze_unchanged": frozen["calibrated_sha256"] == sha256(PROJECT_ROOT/"configs/calibrated.json"),
              "statistical_CI": False, "note": "Each row is a local constrained refit; no chi-square cutoff is justified for the composite, correlated residual objective."}
    write_json(PROJECT_ROOT/"results/tables/Cr_training_profile.json", result)
    return result
